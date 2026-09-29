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

from fakes import FakeEmbedder
from fastmcp import Client

from memory_ultra_rag_mcp import server
from memory_ultra_rag_mcp.config import resolve_config
from memory_ultra_rag_mcp.index import MemoryIndex
from memory_ultra_rag_mcp.retrieval import Retrieval, RetrievalSettings


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
    answer = engine.answer(
        scope="local", directory=directory, query="the draft", limit=10
    )

    assert answer["elapsed_ms"] > 0
    assert answer["elapsed_ms"] <= engine.policy.read_ceiling_ms


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
    """Recording is asynchronous: the caller is answered before the vector is."""

    config = _scope(tmp_path)
    embedder = FakeEmbedder()
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
    # The statement is durable now; the vector is the worker's problem.
    with MemoryIndex(config.local_directory) as index:
        assert [item.text for item in index.statements()] == ["a fact"]
    assert recorded["units_queued"] == 1
    assert recorded["embedded"] is False
    engine.worker_for(config.local_directory).drain(5.0)
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


def test_the_vectors_can_be_thrown_away_and_rebuilt(tmp_path: Path) -> None:
    """The document and the record survive; the vectors are derived from them.

    Only the vectors are disposable, and only at the cost of one embedding per
    statement. The record is the memory: deleting the file that holds it is not a
    rebuild, it is a loss, and nothing here pretends otherwise.
    """

    from memory_ultra_rag_mcp.maintenance import reindex

    config = _scope(tmp_path)
    directory = config.local_directory
    _populate(directory, statements=5)
    engine = _engine(config, FakeEmbedder())
    engine.maintain(directory)
    engine.worker_for(directory).drain(5.0)
    assert engine.answer(scope="local", directory=directory, query="draft", limit=10)[
        "returned"
    ]

    with MemoryIndex(directory) as index:
        assert index.drop_vectors() == 5

    # The record is untouched: the statements and their counts are still there.
    after_wipe = engine.answer(
        scope="local", directory=directory, query="draft", limit=10
    )
    assert after_wipe["returned"] > 0
    assert after_wipe["units_pending"] > 0

    reindex([directory], engine.embedder)
    engine.worker_for(directory).drain(10.0)
    assert engine.answer(scope="local", directory=directory, query="draft", limit=10)[
        "returned"
    ]
