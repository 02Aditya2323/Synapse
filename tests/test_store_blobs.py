"""Blobs are content-addressed, so parallel steps often write the same one at the same moment."""

import threading
from concurrent.futures import ThreadPoolExecutor

from synapse0g.graph.core.store import Store


def test_the_same_blob_written_by_many_threads_at_once(tmp_path):
    store = Store(tmp_path)
    for i in range(40):  # a fresh blob each round, eight writers released together
        value = {"round": i, "text": "x" * 100_000}
        gate = threading.Barrier(8)

        def write(_, gate=gate, value=value):
            gate.wait()
            return store.put_blob(value)

        with ThreadPoolExecutor(8) as pool:
            digests = list(pool.map(write, range(8)))
        assert len(set(digests)) == 1 and store.get_blob(digests[0]) == value
    assert not list(tmp_path.rglob("*.tmp"))
