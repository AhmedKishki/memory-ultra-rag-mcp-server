"""Every test hands back the background workers it started.

An engine builds one `EmbeddingWorker` per scope, and a worker starts a daemon
thread the first time a statement is queued. A test that does not close its
engine therefore leaves that thread running, holding an embedder and its own
scope; after a few dozen tests the run spends its time in GIL contention rather
than in the code under test, which is what made this suite take eight minutes
and made one test cost three seconds alone and forty-five in a full run.

So this fixture records the workers a test creates and stops each of them
afterwards. It is a test-hygiene rule, not a server one: the server builds one
engine per process and keeps it for the process's life on purpose, which is why
`Retrieval.close` exists rather than being called on a timer.
"""

from __future__ import annotations

import pytest

from memory_ultra_rag_mcp import retrieval as retrieval_module
from memory_ultra_rag_mcp.maintenance import EmbeddingWorker


@pytest.fixture(autouse=True)
def _stop_embedding_workers(monkeypatch: pytest.MonkeyPatch) -> object:
    """Stop every embedding worker the test started, whatever the test did."""

    created: list[EmbeddingWorker] = []

    class Tracked(EmbeddingWorker):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(retrieval_module, "EmbeddingWorker", Tracked)
    yield
    for worker in created:
        worker.stop()
