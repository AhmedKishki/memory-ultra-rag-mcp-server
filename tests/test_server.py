"""The four tools, the two roots they touch, and what a read and a write report.

Every test here runs on a hand-written embedder rather than the local model, so
the suite fetches nothing and a query's result is arithmetic rather than an
opinion. What is under test is the contract: what a read answers, what it refuses,
what it discloses, and what a write guarantees before it returns.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEmbedder, FakeReranker
from fastmcp import Client
from fastmcp.exceptions import ToolError

from memory_ultra_rag_mcp import server
from memory_ultra_rag_mcp.config import resolve_config
from memory_ultra_rag_mcp.retrieval import Retrieval, RetrievalSettings
from memory_ultra_rag_mcp.store import append_round
from memory_ultra_rag_mcp.vectors import vector_path


def _data(result: Any) -> Any:
    """Return a tool's object response, whichever way the client handed it back."""

    payload = getattr(result, "data", None)
    if isinstance(payload, dict):
        return payload
    return json.loads(result.content[0].text)


def _config(tmp_path: Path):
    project = tmp_path / "thesis"
    project.mkdir()
    return resolve_config(
        project_root=project,
        storage_root=tmp_path / "shared",
    )


def _engine(
    config,
    embedder: FakeEmbedder | None = None,
    **policy,
) -> Retrieval:
    """Build this process's retrieval, on a fake model unless told otherwise."""

    model = embedder or FakeEmbedder()
    return Retrieval(
        embedder=model,
        policy=RetrievalSettings(embedding_model=model.identity.name, **policy),
    )


def _app(config, embedder: FakeEmbedder | None = None, **policy):
    return server.create_server(
        config, retrieval=_engine(config, embedder, **policy), warm=False
    )


def test_the_surface_is_the_four_memory_tools(tmp_path: Path) -> None:
    """Two verbs, two scopes, and nothing else to choose between."""

    app = _app(_config(tmp_path))

    async def scenario() -> list[str]:
        async with Client(app) as client:
            return sorted(tool.name for tool in await client.list_tools())

    assert asyncio.run(scenario()) == [
        "get_memory_global",
        "get_memory_local",
        "set_memory_global",
        "set_memory_local",
    ]


def test_a_local_statement_is_written_into_the_project(tmp_path: Path) -> None:
    config = _config(tmp_path)
    embedder = FakeEmbedder()
    app = _app(config, embedder)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            return _data(
                await client.call_tool(
                    "set_memory_local", {"content": "The draft lives in docs/"}
                )
            )

    recorded = asyncio.run(scenario())
    assert recorded["status"] == "set"
    assert recorded["standing_document"] == str(config.local_directory / "MEMORY.md")
    standing = (config.local_directory / "MEMORY.md").read_text(encoding="utf-8")
    assert "The draft lives in docs/" in standing
    # A statement is not an exchange, so it writes no dated round.
    assert not (config.local_directory / "project").exists()
    # The write is durable and the vector is queued, not computed on this thread.
    assert recorded["embedded"] is False
    assert recorded["units_queued"] == 1
    assert recorded["embedding_model"] == "fake/model"


def test_a_statement_is_embedded_off_the_calling_thread(tmp_path: Path) -> None:
    """Recording is asynchronous: the caller is answered, the vector arrives."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            recorded = _data(
                await client.call_tool("set_memory_local", {"content": "a fact"})
            )
            # The work happens on the worker; a read reports what it has caught.
            drained = all(
                engine.worker_for(config.local_directory).drain(5.0) for _ in (1,)
            )
            read = _data(
                await client.call_tool("get_memory_local", {"query": "a fact"})
            )
            return recorded | {"drained": drained, "read": read}

    outcome = asyncio.run(scenario())
    assert outcome["drained"] is True
    assert "a fact" in embedder.documents
    assert outcome["read"]["units_pending"] == 0


def test_a_write_survives_a_model_that_cannot_be_loaded(tmp_path: Path) -> None:
    """A memory is never at risk because a lookup model is missing."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    embedder.fails = True
    app = _app(config, embedder)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            recorded = _data(
                await client.call_tool("set_memory_local", {"content": "a fact"})
            )
            drained = _engine(config, embedder)
            return recorded | {
                "pending": drained.worker_for(config.local_directory).pending
            }

    outcome = asyncio.run(scenario())
    assert outcome["status"] == "set"
    assert "a fact" in (config.local_directory / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert outcome["embedded"] is False


def test_a_global_statement_is_written_under_the_storage_root(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            recorded = _data(
                await client.call_tool(
                    "set_memory_global", {"content": "always cite the commit"}
                )
            )
            read = _data(await client.call_tool("get_memory_global", {"query": "cite"}))
            return recorded | {"read": read}

    outcome = asyncio.run(scenario())
    assert outcome["directory"] == str(config.global_directory)
    assert config.global_directory == config.storage_root / "memory" / "default"
    assert "always cite the commit" in outcome["read"]["units"][0]["text"]
    assert (config.global_directory / "MEMORY.md").is_file()


def test_a_read_answers_a_question_not_a_file(tmp_path: Path) -> None:
    """Only what matched comes back, and each unit says where it came from."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "set_memory_local", {"content": "docs/ has the draft"}
            )
            await client.call_tool("set_memory_local", {"content": "tests run with uv"})
            for line, answer in (
                ("where is the draft", "in docs/"),
                ("morning coffee", "tea"),
            ):
                written = append_round(config.local_directory, [line], [answer])
                engine.maintain(config.local_directory, written)
            engine.worker_for(config.local_directory).drain(5.0)
            return _data(await client.call_tool("get_memory_local", {"query": "draft"}))

    read = asyncio.run(scenario())
    assert read["scope"] == "local"
    assert {unit["text"] for unit in read["units"]} == {
        "docs/ has the draft",
        "where is the draft\nin docs/",
    }
    answer = json.dumps(read)
    assert "tests run with uv" not in answer
    assert "morning coffee" not in answer
    # The upstream template is a seed, not content, so it cannot answer.
    assert "i am jack" not in answer
    for unit in read["units"]:
        assert unit["source"]
        assert unit["matched_by"] in {"lexical", "semantic", "lexical|semantic"}


def test_a_read_finds_a_statement_by_meaning_alone(tmp_path: Path) -> None:
    """The words may share nothing, and the statement still comes back."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "set_memory_local", {"content": "the third draft lives in drafts/"}
            )
            engine.worker_for(config.local_directory).drain(5.0)
            return _data(
                await client.call_tool("get_memory_local", {"query": "manuscript"})
            )

    read = asyncio.run(scenario())
    assert read["semantic_available"] is True
    assert [unit["text"] for unit in read["units"]] == [
        "the third draft lives in drafts/"
    ]
    assert read["matched_by"] == "semantic"
    assert read["units"][0]["matched_by"] == "semantic"


def test_a_read_that_needs_no_meaning_says_which_side_answered(tmp_path: Path) -> None:
    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "set_memory_local", {"content": "always cite the commit"}
            )
            engine.worker_for(config.local_directory).drain(5.0)
            return _data(await client.call_tool("get_memory_local", {"query": "cite"}))

    read = asyncio.run(scenario())
    assert read["matched_by"] in {"lexical", "both"}
    assert "always cite the commit" in json.dumps(read)


def test_a_read_says_so_when_the_model_is_not_resident(tmp_path: Path) -> None:
    """A read refuses to stall on a load, and reports the worse answer."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool("set_memory_local", {"content": "a fact"})
            engine.worker_for(config.local_directory).drain(5.0)
            embedder._loaded = False
            return _data(await client.call_tool("get_memory_local", {"query": "fact"}))

    read = asyncio.run(scenario())
    assert read["semantic_available"] is False
    # Words still answer, and the answer is the worse one, labelled as such.
    assert [unit["text"] for unit in read["units"]] == ["a fact"]


def test_a_read_is_capped_and_says_so(tmp_path: Path) -> None:
    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            for index in range(5):
                written = append_round(
                    config.local_directory, [f"draft note {index}"], ["noted"]
                )
                engine.maintain(config.local_directory, written)
            engine.worker_for(config.local_directory).drain(5.0)
            return (
                _data(await client.call_tool("get_memory_local", {"query": "draft"})),
                _data(
                    await client.call_tool(
                        "get_memory_local", {"query": "draft", "limit": 2}
                    )
                ),
            )

    everything, capped = asyncio.run(scenario())
    assert everything["returned"] == 5
    assert everything["truncated"] is False
    assert capped["returned"] == 2
    assert capped["truncated"] is True


def test_a_read_without_a_query_is_refused(tmp_path: Path) -> None:
    """No query means no whole file: the point of retrieval is the context window."""

    app = _app(_config(tmp_path), FakeEmbedder())

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool("get_memory_local", {"query": "  "})

    with pytest.raises(ToolError, match="query must not be empty"):
        asyncio.run(scenario())


def test_a_read_reports_when_the_index_is_behind(tmp_path: Path) -> None:
    """A file edited in place is not silently served from a stale index."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            await client.call_tool("set_memory_local", {"content": "a fact"})
            engine.worker_for(config.local_directory).drain(5.0)
            fresh = _data(await client.call_tool("get_memory_local", {"query": "fact"}))
            standing = config.local_directory / "MEMORY.md"
            standing.write_text(
                standing.read_text(encoding="utf-8") + "\nand a hand-written line\n",
                encoding="utf-8",
            )
            behind = _data(
                await client.call_tool("get_memory_local", {"query": "hand"})
            )
            return fresh, behind

    fresh, behind = asyncio.run(scenario())
    assert fresh["index_files_behind"] == 0
    # The index is behind, and the read says so rather than pretending.
    assert behind["index_files_behind"] == 1


def test_the_two_kinds_do_not_share_a_directory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            local = _data(await client.call_tool("get_memory_local", {"query": "x"}))
            default_user = _data(
                await client.call_tool("get_memory_global", {"query": "x"})
            )
            return local, default_user

    local, default_user = asyncio.run(scenario())
    assert local["scope"] == "local"
    assert default_user["scope"] == "global"
    assert (config.local_directory / "MEMORY.md").is_file()
    assert (config.global_directory / "MEMORY.md").is_file()
    # Two scopes, two derived files, neither able to answer for the other.
    assert vector_path(config.local_directory).parent == config.local_directory
    assert vector_path(config.global_directory).parent == config.global_directory


def test_a_project_read_cannot_see_another_projects_memory(tmp_path: Path) -> None:
    (tmp_path / "first" / "thesis").mkdir(parents=True)
    (tmp_path / "second" / "thesis").mkdir(parents=True)
    first = resolve_config(
        project_root=tmp_path / "first" / "thesis",
        storage_root=tmp_path / "shared",
    )
    second = resolve_config(
        project_root=tmp_path / "second" / "thesis",
        storage_root=tmp_path / "shared",
    )

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        embedder = FakeEmbedder()
        async with Client(_app(first, embedder)) as client:
            await client.call_tool("set_memory_local", {"content": "first only"})
            _engine(first, embedder).worker_for(first.local_directory).drain(5.0)
        async with Client(_app(second, embedder)) as client:
            read = _data(await client.call_tool("get_memory_local", {"query": "first"}))
            missing = _data(
                await client.call_tool("get_memory_local", {"query": "only"})
            )
        return read, missing

    read, missing = asyncio.run(scenario())
    assert "first only" not in json.dumps(read)
    assert missing["units"] == []


def test_the_global_tools_take_no_user(tmp_path: Path) -> None:
    """One global memory, so there is nothing to name."""

    app = _app(_config(tmp_path), FakeEmbedder())

    async def scenario() -> list[set[str]]:
        async with Client(app) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            return [
                set(tools["get_memory_global"].inputSchema["properties"]),
                set(tools["set_memory_global"].inputSchema["properties"]),
            ]

    get_parameters, set_parameters = asyncio.run(scenario())
    assert get_parameters == {"query", "limit"}
    assert set_parameters == {"content"}


def test_an_empty_statement_is_refused(tmp_path: Path) -> None:
    app = _app(_config(tmp_path), FakeEmbedder())

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool("set_memory_local", {"content": "   "})

    with pytest.raises(ToolError, match="content must not be empty"):
        asyncio.run(scenario())


def test_a_reranker_orders_the_answer_when_it_is_switched_on(tmp_path: Path) -> None:
    """Off by default, and when on it decides the order it reports."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder, reranker_model="fake/reranker")
    engine.reranker = FakeReranker()  # type: ignore[assignment] - the seam itself, on a fake
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            # A page write: through the store, then the write side's maintenance,
            # which is what gives the round a vector.
            for line in ("the draft is a plan", "the draft lives in docs"):
                written = append_round(config.local_directory, [line], ["noted"])
                engine.maintain(config.local_directory, written)
            engine.worker_for(config.local_directory).drain(5.0)
            return _data(
                await client.call_tool("get_memory_local", {"query": "the draft docs"})
            )

    read = asyncio.run(scenario())
    assert read["reranked"] is True
    # The words match both rounds, and the reranker's scores put the round
    # holding more of the query first. Ordering only: both still come back.
    assert [unit["text"] for unit in read["units"]] == [
        "the draft lives in docs\nnoted",
        "the draft is a plan\nnoted",
    ]


def test_the_command_line_requires_a_project(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert server.main([]) == 2
    assert "--project-root is required" in capsys.readouterr().err


def test_the_command_line_reports_a_missing_project(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert server.main(["--project-root", str(tmp_path / "absent")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_a_failing_model_is_reported_and_the_write_still_lands(tmp_path: Path) -> None:
    """Durability first: the statement is in the file whatever the model does."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    embedder.fails = True
    app = _app(config, embedder)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            return _data(
                await client.call_tool("set_memory_global", {"content": "a rule"})
            )

    recorded = asyncio.run(scenario())
    assert recorded["status"] == "set"
    assert recorded["embedded"] is False
    assert "a rule" in (config.global_directory / "MEMORY.md").read_text(
        encoding="utf-8"
    )
