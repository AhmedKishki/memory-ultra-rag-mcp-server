"""The properties the read and write paths promise, proven rather than described.

Every one of these is a promise the design makes in prose, and each is a way the
code could quietly break it later: a read that starts walking files, a write that
waits for a model, a caller that is told "set" before the memory is durable. They
run on a hand-written model, so they are fast and deterministic, and the one that
times a read uses a ceiling rather than a comparison against a machine.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEmbedder, GatedEmbedder
from fastmcp import Client

from memory_ultra_rag_mcp import server
from memory_ultra_rag_mcp.config import resolve_config
from memory_ultra_rag_mcp.index import MemoryIndex
from memory_ultra_rag_mcp.maintenance import EmbeddingWorker, PendingUnit, reindex
from memory_ultra_rag_mcp.models import ModelError, ModelIdentity
from memory_ultra_rag_mcp.retrieval import Retrieval, RetrievalSettings
from memory_ultra_rag_mcp.vectors import VectorStore


def _data(result: Any) -> Any:
    payload = getattr(result, "data", None)
    if isinstance(payload, dict):
        return payload
    return json.loads(result.content[0].text)


def _scope(tmp_path: Path) -> Any:
    project = tmp_path / "thesis"
    project.mkdir()
    return resolve_config(project_root=project, storage_root=tmp_path / "shared")


def _engine(config, embedder: FakeEmbedder | None = None, **policy) -> Retrieval:
    model = embedder or FakeEmbedder()
    return Retrieval(
        embedder=model,
        policy=RetrievalSettings(embedding_model=model.identity.name, **policy),
    )


def _populate(directory: Path, statements: int = 200) -> None:
    """Write a scope with this many statements, through the record."""

    with MemoryIndex(directory) as index:
        for number in range(statements):
            index.insert(
                f"note {number} about the draft",
                "NOTE",
            )


def test_a_read_does_no_work_proportional_to_the_memory(tmp_path: Path) -> None:
    """A read answers a question, and the cost of that is what it is.

    The memory is a table, so a read is a query against it plus a page of rows
    back, and neither grows with what the table holds. This is the property that
    let a memory be big enough to be worth having: a read that walked the scope
    would be a read whose latency a project could only fix by remembering less.
    """

    def answer_over(statements: int) -> int:
        (tmp_path / f"scope-{statements}").mkdir(parents=True, exist_ok=True)
        config = _scope(tmp_path / f"scope-{statements}")
        directory = config.local_directory
        _populate(directory, statements=statements)
        engine = _engine(config, FakeEmbedder())
        engine.worker_for(directory).drain(20.0)
        started = time.perf_counter()
        engine.answer(scope="local", directory=directory, query="draft", limit=10)
        return int((time.perf_counter() - started) * 1000.0)

    small = answer_over(10)
    large = answer_over(2000)

    # Not a comparison against a machine's speed: a read over two thousand
    # statements must not cost hundreds of times a read over ten.
    assert large < small * 8 + 200


def test_a_read_stays_inside_its_latency_ceiling(tmp_path: Path) -> None:
    """A warm read is bounded, and says how long it took.

    The ceiling is the one in the policy, asserted rather than assumed, so a
    later change that makes a blocking lookup slow fails here. It is generous:
    the point is a guard rail, not a benchmark.
    """

    config = _scope(tmp_path)
    directory = config.local_directory
    _populate(directory)
    engine = _engine(config, FakeEmbedder())
    engine.maintain(directory)
    engine.worker_for(directory).drain(5.0)

    engine.answer(scope="local", directory=directory, query="draft", limit=10)
    # The bound is on the read itself: the tool does not publish its own timing,
    # because nothing an agent can do follows from how many milliseconds it took.
    started = time.perf_counter()
    answer = engine.answer(
        scope="local", directory=directory, query="the draft", limit=10
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert answer["units"]
    assert 0 < elapsed_ms <= engine.policy.read_ceiling_ms


def test_the_ceiling_is_asserted_against_a_fake_reranker_and_says_so() -> None:
    """The cross-encoder is the one cost this bound does not cover.

    A fake reranker scores in no time, so this ceiling guards the fusion, the
    word scan and the vector scan. The real reranker's cost is measured out of
    band and recorded in the policy's docstring; if that measurement moves, the
    number there is what has to change, and this test is what tells you it did
    not.
    """

    default = RetrievalSettings(embedding_model="fake/model")
    assert default.reranker_model is not None
    assert default.rerank_depth == 10
    assert default.read_ceiling_ms >= 500.0


def test_a_write_queues_rather_than_embeds(tmp_path: Path) -> None:
    """Recording is asynchronous: the caller is answered before the vector is.

    The embedder is held at its gate, so what the test looks at between the write
    and the release is the state the contract is about rather than a race with the
    worker's own thread.
    """

    config = _scope(tmp_path)
    embedder = GatedEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            return _data(
                await client.call_tool(
                    "record_memory", {"content": "a fact", "kind": "note"}
                )
            )

    recorded = asyncio.run(scenario())
    try:
        # The statement is durable now; the vector is the worker's problem.
        with MemoryIndex(config.local_directory) as index:
            assert [item.text for item in index.statements()] == ["a fact"]
        # The answer says what was filed and nothing about the vector queue: the
        # queue is the server's own work, and the fact it left is in the record.
        assert recorded == {
            "scope": "local",
            "status": "recorded",
            "kind": "NOTE",
            "text": "a fact",
        }
        identity = embedder.identity
        with VectorStore(
            config.local_directory, identity.name, identity.dimension
        ) as store:
            assert store.count() == 0

        embedder.release.set()
        engine.worker_for(config.local_directory).drain(5.0)

        with VectorStore(
            config.local_directory, identity.name, identity.dimension
        ) as store:
            assert store.count() == 1
    finally:
        engine.close()
    # What gets embedded is the statement alone. The type is a column now rather
    # than a prefix in the text, so it is not part of the vector: a query for a
    # category asks for the column instead of searching for a word.
    assert "a fact" in embedder.documents
    assert "NOTE: a fact" not in embedder.documents


def test_a_write_touches_only_what_it_wrote(tmp_path: Path) -> None:
    """A write is a row and an export, and the export is one small file."""

    config = _scope(tmp_path)
    directory = config.local_directory
    _populate(directory, statements=50)
    engine = _engine(config, FakeEmbedder())
    with MemoryIndex(directory) as index:
        assert index.count_units() == 50

    engine.record(directory=directory, content="one more statement", kind="NOTE")

    with MemoryIndex(directory) as index:
        assert index.count_units() == 51
        found, _ = index.search("more", 10)
        assert [row["text"] for row in found] == ["one more statement"]
    # And a rendering puts the new statement at the top of it, while the memory
    # itself stays one record and no file.
    with MemoryIndex(directory) as index:
        rendered = index.export()
    assert rendered.index("one more statement") < rendered.index(
        "note 0 about the draft"
    )
    assert not (directory / "MEMORY.md").exists()


def test_a_handoff_leaves_nothing_behind_for_the_handoff_it_replaced(
    tmp_path: Path,
) -> None:
    """A statement the record no longer holds must not go on holding a vector.

    A handoff removes the previous one by its kind, and the vector table is a
    separate table in the same file: without this the file grew by one embedding
    every time a session ended, and `units_pending` — which is the count of
    statements the vector table is short of — under-reported while a real
    statement waited for its own vector.
    """

    config = _scope(tmp_path)
    directory = config.local_directory
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    try:
        for session in range(3):
            engine.handoff(directory, f"session {session}: the corpus parses")
            engine.worker_for(directory).drain(5.0)

        with MemoryIndex(directory) as index:
            units = index.count_units()
        with VectorStore(
            directory, embedder.identity.name, embedder.identity.dimension
        ) as store:
            vectors = store.count()
    finally:
        engine.close()

    assert units == 1
    assert vectors == units


def test_a_failure_is_said_once_per_run_of_failures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A condition is not a per-attempt event.

    A model that cannot be fetched is retried on an interval for as long as the
    process runs, so a line every attempt turns something a person has to act on
    into noise they learn to scroll past. One line is said per run of failures; a
    new write is a new run, because the new write is what somebody is waiting on.
    """

    monkeypatch.setattr("memory_ultra_rag_mcp.maintenance.RETRY_SECONDS", 0.2)
    model = _CountingMissingModel()
    worker = EmbeddingWorker(model, tmp_path / "scope")
    worker.submit(PendingUnit("key", "a statement", "ITEM", "0"))
    time.sleep(1.0)
    worker.stop()

    assert model.calls > 2, f"the worker tried {model.calls} times, not repeatedly"
    assert capsys.readouterr().err.count("stays pending") == 1


def test_a_lookup_failure_leaves_the_unit_pending_and_the_worker_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failure of the lookup layer must not take the queue with it.

    The unit was taken out of the queue and the thread died with it, so the next
    write was queued for a thread that no longer existed: the record kept every
    statement and the meaning side of that process was off for the rest of its life,
    with a traceback on the only channel a stdio server may use. The failure is
    reported, the statement stays pending, and the worker keeps waiting for a
    reason to try again.
    """

    scope = tmp_path / "not-a-directory"
    scope.write_text("a file where a scope directory belongs")
    worker = EmbeddingWorker(FakeEmbedder(), scope)
    try:
        worker.submit(PendingUnit("key", "a statement", "NOTE", "0"))
        assert worker.drain(1.0) is False

        assert worker.pending == 1
        worker.submit(PendingUnit("other", "another statement", "NOTE", "0"))
        assert worker.pending == 2
    finally:
        worker.stop()

    assert "stays pending" in capsys.readouterr().err


def test_the_vectors_can_be_thrown_away_and_rebuilt(tmp_path: Path) -> None:
    """The document and the record survive; the vectors are derived from them.

    Only the vectors are disposable, and only at the cost of one embedding per
    statement. The record is the memory: deleting the file that holds it is not a
    rebuild, it is a loss, and nothing here pretends otherwise.
    """

    config = _scope(tmp_path)
    directory = config.local_directory
    _populate(directory, statements=5)
    engine = _engine(config, FakeEmbedder())
    engine.maintain(directory)
    engine.worker_for(directory).drain(5.0)
    assert engine.answer(scope="local", directory=directory, query="draft", limit=10)[
        "units"
    ]

    with MemoryIndex(directory) as index:
        assert index.drop_vectors() == 5

    # The record is untouched: the statements and their counts are still there.
    after_wipe = engine.answer(
        scope="local", directory=directory, query="draft", limit=10
    )
    assert after_wipe["units"]
    # The one condition a read still discloses: a statement whose vector is gone is
    # findable by its words alone until the reindex puts it back.
    assert after_wipe["units_pending"] > 0

    reindex([directory], engine.embedder)
    engine.worker_for(directory).drain(10.0)
    assert engine.answer(scope="local", directory=directory, query="draft", limit=10)[
        "units"
    ]


class _MissingModel:
    """An embedder that cannot be fetched, so every call fails as a missing model."""

    identity = ModelIdentity("embedding", "missing/model", 1)

    def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        raise ModelError("the model is not available")

    def embed_query(self, text: str) -> tuple[float, ...]:
        raise ModelError("the model is not available")


class _CountingMissingModel(_MissingModel):
    """The missing model again, counting how often it was asked."""

    def __init__(self) -> None:
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        self.calls += 1
        raise ModelError("the model is not available")


def test_a_missing_model_is_asked_once_per_wait_rather_than_once_per_moment(
    tmp_path: Path,
) -> None:
    """A model that cannot be fetched is a condition, not a busy loop.

    The retry put the unit straight back on the queue and the worker took it out
    again immediately, which measured at over a million attempts in two seconds —
    a whole core for a statement that was never going to embed. The unit stays
    queued for the next write, so the retry is what waits and not the unit.
    """

    model = _CountingMissingModel()
    worker = EmbeddingWorker(model, tmp_path / "scope")
    worker.submit(PendingUnit("key", "a statement", "ITEM", "0"))

    time.sleep(1.0)

    assert model.calls == 1, f"the model was asked {model.calls} times in one second"
    # The unit is still pending, so the next write or a reindex finds it.
    assert worker.pending == 1
    worker.stop()


def test_a_new_write_retries_a_missing_model_at_once(tmp_path: Path) -> None:
    """The wait is what backs off, not the work: a write clears it immediately."""

    model = _CountingMissingModel()
    worker = EmbeddingWorker(model, tmp_path / "scope")
    worker.submit(PendingUnit("key", "a statement", "ITEM", "0"))
    time.sleep(0.5)
    assert model.calls == 1

    worker.submit(PendingUnit("other", "another statement", "ITEM", "0"))
    time.sleep(0.5)

    assert model.calls > 1, "a new write did not clear the retry wait"
    worker.stop()


def test_a_worker_stops_even_while_its_model_is_missing(tmp_path: Path) -> None:
    """A unit put back must not keep a stopped worker spinning.

    The retry that leaves a statement pending is the one thing that can stop a
    worker from reaching the end of its queue: without this the thread takes the
    same unit out again every poll, `stop` waits out its whole timeout, and a
    process that is shutting down — or a test suite that has leaked a worker —
    keeps a thread burning a core forever.
    """

    worker = EmbeddingWorker(_MissingModel(), tmp_path / "scope")
    worker.submit(PendingUnit("key", "a statement", "ITEM", "0"))
    # Long enough to be unmistakable, short enough to fail loudly.
    worker.drain(1.0)

    started = time.monotonic()
    worker.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"stop waited {elapsed:.2f}s for a worker it cannot empty"
    assert worker.pending == 0
