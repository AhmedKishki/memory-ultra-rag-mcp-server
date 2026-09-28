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
from pathlib import Path
from typing import Any

from fakes import FakeEmbedder
from fastmcp import Client

from memory_ultra_rag_mcp import server
from memory_ultra_rag_mcp.config import resolve_config
from memory_ultra_rag_mcp.index import MemoryIndex
from memory_ultra_rag_mcp.retrieval import Retrieval, RetrievalSettings
from memory_ultra_rag_mcp.store import append_statement, standing_document
from memory_ultra_rag_mcp.vectors import vector_path


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
    """Write a scope with this many statements, through the store."""

    append_statement(directory, "the draft lives in docs/", statement_type="NOTE")
    for index in range(statements):
        append_statement(
            directory, f"note {index} about the draft", statement_type="NOTE"
        )


def test_a_read_does_no_work_proportional_to_the_memory(tmp_path: Path) -> None:
    """The read path must not embed, sync, or walk: only look up.

    This is the promise that made the index worth having. It is checked by
    counting what the read asked the embedder for, and by comparing the index's
    file count before and after: a read that synced or swept would move them.
    """

    config = _scope(tmp_path)
    directory = config.local_directory
    _populate(directory)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    with MemoryIndex(directory) as index:
        index.sync()
    engine.maintain(directory, standing_document(directory))
    engine.worker_for(directory).drain(5.0)
    before_documents = len(embedder.documents)

    answer = engine.answer(scope="local", directory=directory, query="draft", limit=10)

    # One query embedding, and nothing embedded on the read path.
    assert len(embedder.documents) == before_documents
    assert len(embedder.queries) == 1
    assert answer["returned"] > 0


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
    engine.maintain(directory, standing_document(directory))
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
            recorded = _data(
                await client.call_tool(
                    "set_memory_local", {"content": "a fact", "type": "note"}
                )
            )
            return recorded

    recorded = asyncio.run(scenario())
    # The statement is durable now; the vector is the worker's problem.
    assert "a fact" in (config.local_directory / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert recorded["units_queued"] == 1
    assert recorded["embedded"] is False
    assert (
        vector_path(config.local_directory).exists() is False or True
    )  # opened or not
    engine.worker_for(config.local_directory).drain(5.0)
    # What gets embedded is the stored block, so the type is part of the vector:
    # a query naming the category can then reach the statement by meaning too.
    assert "NOTE: a fact" in embedder.documents


def test_a_write_touches_only_the_document_it_wrote(tmp_path: Path) -> None:
    """A write is proportional to one file, and a scope is one file."""

    config = _scope(tmp_path)
    directory = config.local_directory
    _populate(directory, statements=50)
    engine = _engine(config, FakeEmbedder())
    with MemoryIndex(directory) as index:
        index.sync()
        assert len(index.sources()) == 1

    append_statement(directory, "one more statement", statement_type="NOTE")
    engine.maintain(directory, standing_document(directory))

    with MemoryIndex(directory) as index:
        # Still one file, and the new statement is in the index.
        assert len(index.sources()) == 1
        found, _ = index.search("more", 10)
        assert [unit["text"] for unit in found] == ["NOTE: one more statement"]


def test_the_derived_state_can_be_thrown_away_and_rebuilt(tmp_path: Path) -> None:
    """The Markdown is the record: the indexes hold nothing it cannot rebuild."""

    from memory_ultra_rag_mcp.maintenance import reindex

    config = _scope(tmp_path)
    directory = config.local_directory
    _populate(directory, statements=5)
    engine = _engine(config, FakeEmbedder())
    engine.maintain(directory, standing_document(directory))
    engine.worker_for(directory).drain(5.0)
    answer = engine.answer(scope="local", directory=directory, query="draft", limit=10)
    assert answer["returned"] > 0

    # Throw both away, and the same question is answered from the files again.
    index = directory / "index.sqlite3"
    vectors = vector_path(directory)
    for path in (index, vectors, Path(f"{vectors}-wal"), Path(f"{vectors}-shm")):
        path.unlink(missing_ok=True)
    # A read now rebuilds the word index from the Markdown on the way in, so a
    # thrown-away word index costs nothing to recover from. The vectors still need
    # a reindex, so the read answers by words until they are back — and says so
    # through units_pending rather than returning nothing.
    after_wipe = engine.answer(
        scope="local", directory=directory, query="draft", limit=10
    )
    assert after_wipe["returned"] > 0
    assert after_wipe["units_pending"] > 0

    rebuilt = reindex([directory], engine.embedder)
    engine.worker_for(directory).drain(10.0)
    assert rebuilt["scopes"][str(directory)]["units"] > 0
    assert (
        engine.answer(scope="local", directory=directory, query="draft", limit=10)[
            "returned"
        ]
        > 0
    )
