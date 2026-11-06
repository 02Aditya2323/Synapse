"""SQLite for graphs, executions and profiles; fingerprint-named files for large values.

Content-addressed graph storage. Identical values are written once, and `revision()`
provides an inexpensive key for API caches.

Tables:
    graphs       one row per SIR graph, including every optimized variant
    executions   one row per run of a graph on a task
    node_runs    one row per node execution: measured latency, tokens, cost, cached
    profiles     one row per profiling session over a graph
    artifacts    verdicts, comparisons and benchmark reports, as JSON blobs
    cache        the content-addressed node cache (runtime/cache.py owns the policy)

An execution that crashed is stored with its error and `status='crashed'`. It is never
a slow workflow and never a failed optimization (docs/CORRECTNESS.md) -- the aggregates
in `profile/` and `bench/` exclude it and count it separately.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from synapse0g.graph.core.hashing import canonical, fingerprint
from synapse0g.graph.core.sir import Graph, Profile

DEFAULT_DIR = Path(os.environ.get("SYNAPSE_DATA", "data"))

#: Where the store lives. Unset means local SQLite under `SYNAPSE_DATA`, which is the
#: default and the private tier: nothing leaves the machine.
STORE_URI_ENV = "SYNAPSE_STORE"

#: Which workspace this process writes to in a shared backend. The default is the
#: machine name, deliberately: two people pointing at one cluster do **not** share a
#: cache until one of them says so explicitly. See `MongoStore` for why that matters.
WORKSPACE_ENV = "SYNAPSE_WORKSPACE"


def lean_body(execution: Any) -> str:
    """An execution as stored. Node runs live in their own table, so they are not repeated
    here. Outputs become a digest unless full capture is on (see core/retention.py)."""
    from synapse0g.graph.core.retention import capture_full

    if capture_full():
        return execution.model_dump_json(exclude={"node_runs"})
    meta = {**execution.meta, "outputs_digest": fingerprint(execution.outputs)}
    return execution.model_copy(update={"meta": meta, "outputs": {}}).model_dump_json(exclude={"node_runs"})


def default_workspace() -> str:
    return os.environ.get(WORKSPACE_ENV) or socket.gethostname() or "local"


@runtime_checkable
class StoreProtocol(Protocol):
    """What every storage backend must provide.

    Extracted so Synapse can be **local by default and hosted by choice**. The local
    tier is the privacy guarantee: SQLite under `data/`, bound to 127.0.0.1, no
    telemetry, nothing leaving the machine. A team that is happy for their graphs to
    live in a shared cluster sets `SYNAPSE_STORE` and gets the same product against a
    different backend.

    The interface is deliberately narrow — documents in, documents out, a cheap
    `revision()` for cache invalidation — because that is the whole of what the API,
    the CLI and the MCP server actually use, and a narrow interface is what makes a
    second backend an afternoon rather than a rewrite.
    """

    # graphs
    def save_graph(self, graph: Graph) -> str: ...
    def load_graph(self, graph_id: str) -> Graph: ...
    def list_graphs(self, **kw) -> list[Graph]: ...
    def graph_exists(self, graph_id: str) -> bool: ...
    def variants_of(self, graph_id: str) -> list[Graph]: ...

    # executions
    def save_execution(self, execution: Any) -> str: ...
    def load_execution(self, execution_id: str) -> Any: ...
    def list_executions(self, **kw) -> list[Any]: ...
    def count_executions(self, **kw) -> int: ...

    # profiles and artifacts
    def save_profile(self, profile: Profile, *, profile_id: str | None = ...) -> str: ...
    def latest_profile(self, graph_id: str, *, cold: bool | None = ...) -> Profile | None: ...
    def put_artifact(self, kind: str, key: str, body: Any) -> None: ...
    def get_artifact(self, kind: str, key: str) -> Any | None: ...
    def list_artifacts(self, kind: str, *, limit: int = ...) -> list[tuple[str, Any]]: ...

    # blobs
    def put_blob(self, value: Any) -> str: ...
    def get_blob(self, digest: str) -> Any: ...
    def has_blob(self, digest: str) -> bool: ...

    # the content-addressed cache
    def cache_put(self, entry: dict) -> None: ...
    def cache_get(self, node_hash: str) -> dict | None: ...
    def cache_clear(self) -> int: ...
    def cache_items(self) -> list[tuple[str, str, int, float]]: ...
    def cache_delete(self, node_hash: str) -> None: ...
    def cache_blob_in_use(self, digest: str) -> bool: ...
    def blob_delete(self, digest: str) -> None: ...

    # retention
    def delete_executions_before(self, iso_time: str) -> int: ...
    def size_report(self) -> dict: ...
    def cache_savings(self) -> tuple[int, int | None, float | None]: ...

    # housekeeping
    def revision(self) -> tuple: ...
    def counts(self) -> dict: ...
    def close(self) -> None: ...

    @property
    def mode(self) -> str: ...


def _savings(rows: list) -> tuple[int, int | None, float | None]:
    """Hits, tokens and cost they avoided. Tokens or cost nobody reported stay None."""
    tokens = [(a or 0) + (b or 0) for a, b, _ in rows if a is not None or b is not None]
    costs = [c for _, _, c in rows if c is not None]
    return len(rows), sum(tokens) if tokens else None, sum(costs) if costs else None


def open_store(target: str | Path | None = None) -> StoreProtocol:
    """Open the configured store. **Local unless told otherwise.**

        open_store()                                  -> SQLite under data/   (private)
        open_store("data/mine")                       -> SQLite there         (private)
        SYNAPSE_STORE=mongodb+srv://... open_store()  -> MongoDB / Atlas      (shared)

    The default is the private one on purpose. A tool that silently sends a team's
    workflow graphs somewhere because a variable happened to be set in the environment
    is a tool nobody should run against their production agents.
    """
    uri = str(target) if target is not None else os.environ.get(STORE_URI_ENV, "")
    if uri.startswith(("mongodb://", "mongodb+srv://")):
        from synapse0g.graph.core.store_mongo import MongoStore

        return MongoStore(uri)
    return Store(Path(uri) if uri else DEFAULT_DIR)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graphs (
    graph_id TEXT PRIMARY KEY,
    name TEXT, sir_version INTEGER,
    derived_from TEXT, pipeline TEXT, n_nodes INTEGER,
    created_at TEXT, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS graphs_name ON graphs(name);
CREATE INDEX IF NOT EXISTS graphs_derived ON graphs(derived_from);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    task_id TEXT, split TEXT, status TEXT,
    cold INTEGER, optimized INTEGER,
    wall_ms REAL, tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL,
    provider_calls INTEGER, cache_hits INTEGER,
    success INTEGER, error TEXT,
    created_at TEXT, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS executions_graph ON executions(graph_id);
CREATE INDEX IF NOT EXISTS executions_task ON executions(task_id);
CREATE INDEX IF NOT EXISTS executions_status ON executions(status);

CREATE TABLE IF NOT EXISTS node_runs (
    execution_id TEXT NOT NULL, seq INTEGER NOT NULL,
    node_id TEXT NOT NULL, instance TEXT,
    body TEXT NOT NULL,
    PRIMARY KEY (execution_id, seq)
);
CREATE INDEX IF NOT EXISTS node_runs_node ON node_runs(node_id);

CREATE TABLE IF NOT EXISTS profiles (
    profile_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL, executions INTEGER, cold INTEGER, split TEXT,
    created_at TEXT, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS profiles_graph ON profiles(graph_id);

CREATE TABLE IF NOT EXISTS artifacts (
    kind TEXT NOT NULL, key TEXT NOT NULL,
    created_at TEXT, body TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);

CREATE TABLE IF NOT EXISTS cache (
    node_hash TEXT PRIMARY KEY,
    target TEXT, effect TEXT,
    blob TEXT NOT NULL,
    tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL, latency_ms REAL,
    seed INTEGER, model_id TEXT,
    created_at TEXT, created_mono REAL
);
CREATE INDEX IF NOT EXISTS cache_target ON cache(target);
"""


class Store:
    """Thread-safe enough for our use: one connection, one lock, `check_same_thread`
    off so the async scheduler's threads can share it."""

    def __init__(self, root: Path | str = DEFAULT_DIR):
        self.root = Path(root)
        self.blob_dir = self.root / "blobs"
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._writes = 0
        self._db = sqlite3.connect(self.root / "synapse.db", check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # ---- blobs -------------------------------------------------------------
    def put_blob(self, value: Any) -> str:
        """Write a value once, named by its own fingerprint. Returns the digest."""
        digest = fingerprint(value)
        path = self._blob_path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # a temp name of its own: two threads may write the same blob at once
            tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(canonical(value), encoding="utf-8")
            os.replace(tmp, path)
        return digest

    def get_blob(self, digest: str) -> Any:
        return json.loads(self._blob_path(digest).read_text(encoding="utf-8"))

    def has_blob(self, digest: str) -> bool:
        return self._blob_path(digest).exists()

    def _blob_path(self, digest: str) -> Path:
        return self.blob_dir / digest[:2] / f"{digest}.json"

    # ---- graphs ------------------------------------------------------------
    def save_graph(self, graph: Graph) -> str:
        row = (
            graph.id,
            graph.name,
            graph.sir_version,
            graph.derived_from,
            json.dumps(graph.pipeline),
            len(graph.nodes),
            graph.created_at,
            graph.model_dump_json(),
        )
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO graphs VALUES (?,?,?,?,?,?,?,?)", row)
            self._db.commit()
            self._writes += 1
        return graph.id

    def load_graph(self, graph_id: str) -> Graph:
        with self._lock:
            row = self._db.execute("SELECT body FROM graphs WHERE graph_id = ?", (graph_id,)).fetchone()
        if row is None:
            raise KeyError(f"no graph {graph_id!r}")
        return Graph.model_validate_json(row[0])

    def list_graphs(self, *, name: str | None = None, derived_from: str | None = None,
                    limit: int = 100, offset: int = 0) -> list[Graph]:
        where, args = [], []
        if name is not None:
            where.append("name = ?")
            args.append(name)
        if derived_from is not None:
            where.append("derived_from = ?")
            args.append(derived_from)
        sql = "SELECT body FROM graphs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, graph_id LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._db.execute(sql, (*args, limit, offset)).fetchall()
        return [Graph.model_validate_json(r[0]) for r in rows]

    def graph_exists(self, graph_id: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM graphs WHERE graph_id = ?", (graph_id,)).fetchone() is not None

    def variants_of(self, graph_id: str) -> list[Graph]:
        """Every optimized form derived from this graph."""
        return self.list_graphs(derived_from=graph_id, limit=200)

    # ---- executions --------------------------------------------------------
    def save_execution(self, execution: Any) -> str:
        """`execution` is a `runtime.executor.Execution`. Typed loosely here so
        core/ does not depend on runtime/ -- the dependency runs the other way."""
        e = execution
        row = (
            e.execution_id, e.graph_id, e.task_id, e.split, e.status,
            int(e.cold), int(e.optimized),
            e.wall_ms, e.tokens_in, e.tokens_out, e.cost_usd,
            e.provider_calls, e.cache_hits,
            None if e.success is None else int(e.success), e.error,
            e.created_at, lean_body(e),
        )
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            self._db.execute("DELETE FROM node_runs WHERE execution_id = ?", (e.execution_id,))
            self._db.executemany(
                "INSERT INTO node_runs VALUES (?,?,?,?,?)",
                [
                    (e.execution_id, i, nr.node_id, nr.instance, nr.model_dump_json())
                    for i, nr in enumerate(e.node_runs)
                ],
            )
            self._db.commit()
            self._writes += 1
        return e.execution_id

    def load_execution(self, execution_id: str):
        from synapse0g.graph.runtime.executor import Execution

        with self._lock:
            row = self._db.execute(
                "SELECT body FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no execution {execution_id!r}")
        return self._with_node_runs(Execution.model_validate_json(row[0]))

    def _with_node_runs(self, execution):
        """Join node runs back on. A row written before they moved out still has its own."""
        from synapse0g.graph.runtime.executor import NodeRun

        with self._lock:
            rows = self._db.execute(
                "SELECT body FROM node_runs WHERE execution_id = ? ORDER BY seq", (execution.execution_id,)
            ).fetchall()
        if rows:
            execution.node_runs = [NodeRun.model_validate_json(r[0]) for r in rows]
        return execution

    def list_executions(self, *, graph_id: str | None = None, task_id: str | None = None,
                        split: str | None = None, status: str | None = None,
                        cold: bool | None = None, limit: int = 200, offset: int = 0) -> list[Any]:
        from synapse0g.graph.runtime.executor import Execution

        where, args = [], []
        for col, val in (("graph_id", graph_id), ("task_id", task_id), ("split", split), ("status", status)):
            if val is not None:
                where.append(f"{col} = ?")
                args.append(val)
        if cold is not None:
            where.append("cold = ?")
            args.append(int(cold))
        sql = "SELECT body FROM executions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._db.execute(sql, (*args, limit, offset)).fetchall()
        return [self._with_node_runs(Execution.model_validate_json(r[0])) for r in rows]

    def count_executions(self, *, graph_id: str | None = None, status: str | None = None) -> int:
        where, args = [], []
        for col, val in (("graph_id", graph_id), ("status", status)):
            if val is not None:
                where.append(f"{col} = ?")
                args.append(val)
        sql = "SELECT COUNT(*) FROM executions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock:
            return self._db.execute(sql, tuple(args)).fetchone()[0]

    # ---- profiles ----------------------------------------------------------
    def save_profile(self, profile: Profile, *, profile_id: str | None = None) -> str:
        pid = profile_id or f"p_{fingerprint((profile.graph_id, profile.executions, profile.cold))[:12]}"
        row = (
            pid, profile.graph_id, profile.executions, int(profile.cold), profile.split,
            profile.meta.get("created_at", ""), profile.model_dump_json(),
        )
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO profiles VALUES (?,?,?,?,?,?,?)", row)
            self._db.commit()
            self._writes += 1
        return pid

    def latest_profile(self, graph_id: str, *, cold: bool | None = None) -> Profile | None:
        """The most recent profile for a graph, or `None`.

        `None` is a real answer -- a graph that has never run has no profile, and a pass
        asked to optimize it must say so rather than optimize against zeros.
        """
        sql = "SELECT body FROM profiles WHERE graph_id = ?"
        args: list[Any] = [graph_id]
        if cold is not None:
            sql += " AND cold = ?"
            args.append(int(cold))
        sql += " ORDER BY rowid DESC LIMIT 1"
        with self._lock:
            row = self._db.execute(sql, tuple(args)).fetchone()
        return Profile.model_validate_json(row[0]) if row else None

    # ---- artifacts (verdicts, comparisons, reports) ------------------------
    def put_artifact(self, kind: str, key: str, body: Any) -> None:
        from synapse0g.graph.core.sir import utc_now

        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?)",
                (kind, key, utc_now(), json.dumps(body, default=str)),
            )
            self._db.commit()
            self._writes += 1

    def get_artifact(self, kind: str, key: str) -> Any | None:
        with self._lock:
            row = self._db.execute(
                "SELECT body FROM artifacts WHERE kind = ? AND key = ?", (kind, key)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_artifacts(self, kind: str, *, limit: int = 100) -> list[tuple[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT key, body FROM artifacts WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [(k, json.loads(b)) for k, b in rows]

    # ---- revision and counts ----------------------------------------------
    def revision(self) -> tuple:
        """Changes whenever anything is written. The API's response cache keys on this
        -- memoization makes repeated status checks inexpensive."""
        with self._lock:
            row = self._db.execute(
                "SELECT (SELECT COUNT(*) FROM graphs), (SELECT COUNT(*) FROM executions), "
                "(SELECT COUNT(*) FROM profiles), (SELECT COUNT(*) FROM cache)"
            ).fetchone()
        return (*row, self._writes)

    def counts(self) -> dict:
        with self._lock:
            graphs = self._db.execute("SELECT COUNT(*) FROM graphs").fetchone()[0]
            optimized = self._db.execute("SELECT COUNT(*) FROM graphs WHERE derived_from IS NOT NULL").fetchone()[0]
            executions = self._db.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
            crashed = self._db.execute("SELECT COUNT(*) FROM executions WHERE status = 'crashed'").fetchone()[0]
            node_runs = self._db.execute("SELECT COUNT(*) FROM node_runs").fetchone()[0]
            cached = self._db.execute(
                "SELECT COUNT(*) FROM node_runs WHERE json_extract(body, '$.cached') = 1"
            ).fetchone()[0]
            profiles = self._db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
            cache_entries = self._db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        blobs = list(self.blob_dir.rglob("*.json"))
        return {
            "graphs": graphs,
            "optimized_graphs": optimized,
            "executions": executions,
            "crashed_executions": crashed,
            "node_runs": node_runs,
            "cached_node_runs": cached,
            "profiles": profiles,
            "cache_entries": cache_entries,
            "unique_blobs": len(blobs),
            "blob_mb": round(sum(p.stat().st_size for p in blobs) / 1e6, 2),
        }

    # ---- cache (runtime/cache.py owns the policy; this is just storage) ----
    def cache_put(self, entry: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry["node_hash"], entry.get("target"), entry.get("effect"),
                    entry["blob"], entry.get("tokens_in"), entry.get("tokens_out"),
                    entry.get("cost_usd"), entry.get("latency_ms"),
                    entry.get("seed"), entry.get("model_id"),
                    entry.get("created_at"), entry.get("created_mono"),
                ),
            )
            self._db.commit()
            self._writes += 1

    def cache_get(self, node_hash: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT node_hash, target, effect, blob, tokens_in, tokens_out, cost_usd, latency_ms, "
                "seed, model_id, created_at, created_mono FROM cache WHERE node_hash = ?",
                (node_hash,),
            ).fetchone()
        if row is None:
            return None
        cols = ("node_hash", "target", "effect", "blob", "tokens_in", "tokens_out", "cost_usd",
                "latency_ms", "seed", "model_id", "created_at", "created_mono")
        return dict(zip(cols, row, strict=True))

    def cache_clear(self) -> int:
        """Drop every entry. `synapse compare --cold` calls this so a cold number is
        actually cold; tests call it so a warm cache from another test cannot leak in."""
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            self._db.execute("DELETE FROM cache")
            self._db.commit()
            self._writes += 1
        return n

    # ---- retention (policy in core/retention.py) ---------------------------
    def cache_items(self) -> list[tuple[str, str, int, float]]:
        with self._lock:
            rows = self._db.execute("SELECT node_hash, blob, created_mono FROM cache").fetchall()
        return [
            (h, b, self._blob_path(b).stat().st_size if self._blob_path(b).exists() else 0, float(t or 0))
            for h, b, t in rows
        ]

    def cache_delete(self, node_hash: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM cache WHERE node_hash = ?", (node_hash,))
            self._db.commit()
            self._writes += 1

    def cache_blob_in_use(self, digest: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM cache WHERE blob = ? LIMIT 1", (digest,)).fetchone() is not None

    def blob_delete(self, digest: str) -> None:
        self._blob_path(digest).unlink(missing_ok=True)

    def delete_executions_before(self, iso_time: str) -> int:
        with self._lock:
            ids = [r[0] for r in self._db.execute(
                "SELECT execution_id FROM executions WHERE created_at < ?", (iso_time,)).fetchall()]
            self._db.executemany("DELETE FROM node_runs WHERE execution_id = ?", [(i,) for i in ids])
            self._db.executemany("DELETE FROM executions WHERE execution_id = ?", [(i,) for i in ids])
            self._db.commit()
            self._writes += 1
        return len(ids)

    def size_report(self) -> dict:
        with self._lock:
            sizes = {
                t: self._db.execute(f"SELECT COALESCE(SUM(LENGTH(body)), 0) FROM {t}").fetchone()[0]
                for t in ("graphs", "executions", "node_runs", "profiles", "artifacts")
            }
        sizes["cache_blobs"] = sum(p.stat().st_size for p in self.blob_dir.rglob("*.json"))
        return sizes

    def cache_savings(self) -> tuple[int, int | None, float | None]:
        with self._lock:
            rows = self._db.execute(
                "SELECT json_extract(body, '$.tokens_in'), json_extract(body, '$.tokens_out'), "
                "json_extract(body, '$.cost_usd') FROM node_runs WHERE json_extract(body, '$.cached') = 1"
            ).fetchall()
        return _savings(rows)

    @property
    def mode(self) -> str:
        """Which tier this is. Reported by `/api/health` and `synapse graphs`, because
        "is my data leaving this machine?" should never require reading the source."""
        return "local"

    @property
    def location(self) -> str:
        return str(self.root)

    def close(self) -> None:
        self._db.close()
