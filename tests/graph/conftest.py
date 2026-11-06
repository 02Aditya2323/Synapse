"""Shared test graphs.

Hermetic by construction: nothing here touches a provider, the cache or the store
(CLAUDE.md section 9 -- "tests must be hermetic"; a populated cache or a locally
trained policy changes results).
"""

from __future__ import annotations

import pytest

from synapse0g.graph.core.sir import Effect, Graph, Literal_, Node, Ref, Region


@pytest.fixture(autouse=True)
def _hermetic():
    """Reset the two global registries around every test.

    Both are process-global by design -- a tool name means one thing per process, and a
    registered fake routes a model id everywhere -- which makes them the obvious way for
    one test to change another's result. This caught a real one: a fake `agent-small`
    left behind by an earlier test made a lifted-trace replay test pass locally while
    failing in CI, where the test ran alone and the node reached for a real provider.

    CLAUDE.md section 9: tests must be hermetic. Enforcing it here rather than per file
    means a new test file gets it without remembering to.
    """
    from synapse0g.graph.frontend import dsl
    from synapse0g.graph.runtime import providers

    dsl.clear_registry()
    providers.clear_fakes()
    yield
    dsl.clear_registry()
    providers.clear_fakes()


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    """`.env` is loaded into the environment on import, so a developer's real Slack bot would
    otherwise receive every crashed test run and be asked to approve every test order."""
    for name in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL", "SLACK_APP_TOKEN", "SLACK_APPROVERS", "SYNAPSE_APPROVALS",
                 "SLACK_WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)


def n(node_id: str, **kw) -> Node:
    """A node with workable defaults. `kind` and `target` default to a pure transform
    so a test only states what it is actually about."""
    kw.setdefault("kind", "transform")
    kw.setdefault("target", node_id)
    kw.setdefault("effect", Effect.PURE)
    return Node(id=node_id, **kw)


@pytest.fixture
def fanout() -> Graph:
    """The worked example from docs/IR_SPEC.md section 8: plan -> search -> summarize
    -> synthesize, written sequentially, parallelizable because nothing has an effect
    edge. Two map regions."""
    return Graph(
        id="g_fanout",
        name="research_fanout",
        nodes=[
            n(
                "n0",
                kind="model",
                target="agent-small",
                args={"question": Ref(node="n_in", output="question")},
                outputs=["subqueries"],
                determinism="seeded",
            ),
            n(
                "n1",
                kind="retrieval",
                target="web_search",
                effect=Effect.READ,
                staleness_s=3600,
                args={"query": Ref(node="n0", output="subqueries")},
                outputs=["docs"],
                region="r_map1",
            ),
            n(
                "n2",
                kind="model",
                target="agent-small",
                args={"docs": Ref(node="n1", output="docs")},
                outputs=["note"],
                determinism="seeded",
                region="r_map2",
            ),
            n(
                "n3",
                kind="model",
                target="agent-small",
                args={"notes": Ref(node="n2", output="note")},
                outputs=["answer"],
                determinism="seeded",
            ),
        ],
        regions=[
            Region(id="r_map1", kind="map", body=["n1"], over=Ref(node="n0", output="subqueries")),
            Region(id="r_map2", kind="map", body=["n2"], over=Ref(node="n1", output="docs")),
        ],
        inputs={"question": "str"},
        outputs={"answer": Ref(node="n3", output="answer")},
    )


@pytest.fixture
def fanout_with_input(fanout: Graph) -> Graph:
    """`fanout` with its dangling workflow-input ref replaced by a literal, so it
    validates on its own."""
    nodes = [node.model_copy(deep=True) for node in fanout.nodes]
    nodes[0].args = {"question": Literal_(value="who invented the transformer?")}
    return fanout.copy_with(nodes=nodes).validate_graph()


@pytest.fixture
def duplicate_tool_calls() -> Graph:
    """The real `cse` opportunity from the recorded corpus: `get_price("INFY")` called
    twice with an identical input hash inside one execution
    """
    return Graph(
        id="g_trading",
        name="trading_agent",
        nodes=[
            n("p1", kind="tool", target="get_price", effect=Effect.READ, staleness_s=60,
              args={"symbol": Literal_(value="INFY")}, outputs=["price"]),
            n("think", kind="model", target="agent-small", determinism="seeded",
              args={"price": Ref(node="p1", output="price")}, outputs=["decision"]),
            n("p2", kind="tool", target="get_price", effect=Effect.READ, staleness_s=60,
              args={"symbol": Literal_(value="INFY")}, outputs=["price"]),
            n("size", kind="transform", target="position_size",
              args={"price": Ref(node="p2", output="price"),
                    "decision": Ref(node="think", output="decision")}, outputs=["qty"]),
        ],
        outputs={"qty": Ref(node="size", output="qty")},
    ).validate_graph()


@pytest.fixture
def shop_ops() -> Graph:
    """The `irreversible` showcase (demo beat 6): a workflow that places an order.
    `place_order` must survive every pass untouched."""
    return Graph(
        id="g_shop",
        name="shop_ops",
        nodes=[
            n("look", kind="tool", target="lookup_sku", effect=Effect.READ, staleness_s=300,
              args={"sku": Literal_(value="SKU-9")}, outputs=["item"]),
            n("cart", kind="tool", target="add_to_cart", effect=Effect.WRITE, writes=["cart"],
              args={"item": Ref(node="look", output="item")}, outputs=["cart_id"]),
            n("notify", kind="io", target="send_email", effect=Effect.EXTERNAL,
              args={"cart": Ref(node="cart", output="cart_id")}, outputs=["sent"]),
            n("order", kind="tool", target="place_order", effect=Effect.IRREVERSIBLE,
              args={"cart": Ref(node="cart", output="cart_id")}, outputs=["order_id"]),
        ],
        outputs={"order_id": Ref(node="order", output="order_id")},
    ).validate_graph()


@pytest.fixture
def dead_code() -> Graph:
    """A graph with one genuinely dead pure node and one unused `external` node that is
    *not* dead, because its effect is the point."""
    return Graph(
        id="g_dead",
        name="has_dead_nodes",
        nodes=[
            n("a", args={"x": Literal_(value=1)}, outputs=["value"]),
            n("used", args={"a": Ref(node="a", output="value")}, outputs=["value"]),
            n("dead", args={"a": Ref(node="a", output="value")}, outputs=["value"]),
            n("logged", kind="io", target="post_metric", effect=Effect.EXTERNAL,
              args={"a": Ref(node="a", output="value")}, outputs=["ok"]),
        ],
        outputs={"out": Ref(node="used", output="value")},
    ).validate_graph()
