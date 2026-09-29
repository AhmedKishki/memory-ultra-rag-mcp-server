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
from memory_ultra_rag_mcp.index import MemoryIndex
from memory_ultra_rag_mcp.retrieval import Retrieval, RetrievalSettings


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


def test_the_surface_is_four_memory_tools(tmp_path: Path) -> None:
    """Four verbs, named for what they do, with the scope an argument rather
    than a second tool. An agent chooses a memory and a verb, never a tool."""

    app = _app(_config(tmp_path))

    async def scenario() -> list[str]:
        async with Client(app) as client:
            return sorted(tool.name for tool in await client.list_tools())

    assert asyncio.run(scenario()) == [
        "forget_memory",
        "recall_memory",
        "record_handoff",
        "record_memory",
    ]


def test_recording_takes_a_scope_and_defaults_to_the_project(tmp_path: Path) -> None:
    """One tool for both memories, and the safe default is the project's."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            record = tools["record_memory"].inputSchema
            assert record["properties"]["scope"]["default"] == "local"
            # And no tool anywhere still asks for a memory by name.
            for tool in tools.values():
                assert "local" not in tool.name
                assert "global" not in tool.name
            here = _data(
                await client.call_tool(
                    "record_memory",
                    {"content": "a fact about this project"},
                    raise_on_error=True,
                )
            )
            everywhere = _data(
                await client.call_tool(
                    "record_memory",
                    {"content": "short answers", "scope": "global"},
                    raise_on_error=True,
                ),
            )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "record_memory",
                    {"content": "a fact", "scope": "everywhere"},
                    raise_on_error=True,
                )
            return {"here": here, "everywhere": everywhere}

    answers = asyncio.run(scenario())
    assert answers["here"]["scope"] == "local"
    assert answers["everywhere"]["scope"] == "global"
    assert "a fact about this project" in (
        config.local_directory / "MEMORY.md"
    ).read_text(encoding="utf-8")
    assert "short answers" in (config.global_directory / "MEMORY.md").read_text(
        encoding="utf-8"
    )


def test_a_recall_searches_both_memories_and_ranks_them_together(
    tmp_path: Path,
) -> None:
    """One question is one answer, and each statement says where it came from."""

    config = _config(tmp_path)
    engine = _engine(config, FakeEmbedder())
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "the corpus parser lives in corpus_io.py", "kind": "PLAN"},
                raise_on_error=True,
            )
            await client.call_tool(
                "record_memory",
                {
                    "content": "always ask before editing the corpus parser",
                    "kind": "RULE",
                    "scope": "global",
                },
                raise_on_error=True,
            )
            return _data(
                await client.call_tool(
                    "recall_memory", {"query": "corpus parser"}, raise_on_error=True
                )
            )

    answer = asyncio.run(scenario())
    assert {unit["scope"] for unit in answer["units"]} == {"local", "global"}
    assert answer["scope_counts"] == {"local": 1, "global": 1}
    assert answer["scopes"] == ["local", "global"]
    assert engine is not None


def test_forgetting_finds_the_statement_in_whichever_memory_holds_it(
    tmp_path: Path,
) -> None:
    """A caller who cannot say which memory meant it should not have to guess."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "short answers", "scope": "global"},
                raise_on_error=True,
            )
            return _data(
                await client.call_tool(
                    "forget_memory", {"text": "short answers"}, raise_on_error=True
                )
            )

    forgotten = asyncio.run(scenario())
    assert forgotten["scope"] == "global"
    assert "short answers" not in (config.global_directory / "MEMORY.md").read_text(
        encoding="utf-8"
    )


def test_a_local_statement_is_written_into_the_project(tmp_path: Path) -> None:
    config = _config(tmp_path)
    embedder = FakeEmbedder()
    app = _app(config, embedder)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            return _data(
                await client.call_tool(
                    "record_memory",
                    {"content": "The draft lives in docs/", "kind": "note"},
                )
            )

    recorded = asyncio.run(scenario())
    assert recorded["status"] == "recorded"
    assert recorded["standing_document"] == str(config.local_directory / "MEMORY.md")
    standing = (config.local_directory / "MEMORY.md").read_text(encoding="utf-8")
    assert "The draft lives in docs/" in standing
    # A scope is one document: no dated directory, and no second Markdown file.
    assert not (config.local_directory / "project").exists()
    assert [p.name for p in config.local_directory.glob("*.md")] == ["MEMORY.md"]
    # The write is durable and the vector is queued, not computed on this thread.
    assert recorded["embedded"] is False
    assert recorded["units_queued"] == 1
    assert recorded["embedding_model"] == "fake/model"


def test_a_statement_is_embedded_off_the_calling_thread(tmp_path: Path) -> None:
    """Recording is asynchronous: the caller is answered before the vector is."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = _app(config, embedder)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            recorded = _data(
                await client.call_tool(
                    "record_memory", {"content": "a fact", "kind": "note"}
                )
            )
            drained = engine.worker_for(config.local_directory).drain(5.0)
            read = _data(await client.call_tool("recall_memory", {"query": "a fact"}))
            return recorded | {"drained": drained, "read": read}

    outcome = asyncio.run(scenario())
    assert outcome["drained"] is True
    # What gets embedded is the statement itself. The type is a column of the
    # record rather than a prefix in the text, so it is not part of the vector.
    assert "a fact" in embedder.documents
    assert outcome["read"]["returned"] == 1


def test_a_write_survives_a_model_that_cannot_be_loaded(tmp_path: Path) -> None:
    """A memory is never at risk because a lookup model is missing."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    embedder.fails = True
    app = _app(config, embedder)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            recorded = _data(
                await client.call_tool(
                    "record_memory", {"content": "a fact", "kind": "note"}
                )
            )
            drained = _engine(config, embedder)
            return recorded | {
                "pending": drained.worker_for(config.local_directory).pending
            }

    outcome = asyncio.run(scenario())
    assert outcome["status"] == "recorded"
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
                    "record_memory",
                    {
                        "content": "always cite the commit",
                        "kind": "rule",
                        "scope": "global",
                    },
                )
            )
            read = _data(await client.call_tool("recall_memory", {"query": "cite"}))
            return recorded | {"read": read}

    outcome = asyncio.run(scenario())
    assert outcome["directory"] == str(config.global_directory)
    assert config.global_directory == config.storage_root / "memory" / "default"
    assert "always cite the commit" in outcome["read"]["units"][0]["text"]
    assert (config.global_directory / "MEMORY.md").is_file()


def test_a_read_answers_a_question_not_a_file(tmp_path: Path) -> None:
    """A read answers the question, and reports what it could not do."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "morning coffee is not in this project", "kind": "NOTE"},
                raise_on_error=True,
            )
            await client.call_tool(
                "record_memory",
                {"content": "the draft lives in drafts/", "kind": "PLAN"},
                raise_on_error=True,
            )
            return _data(await client.call_tool("recall_memory", {"query": "draft"}))

    read = asyncio.run(scenario())
    answer = json.dumps(read)
    assert "tests run with uv" not in answer
    assert "morning coffee" not in answer
    # The upstream template is a seed, not content, so it cannot answer.
    assert "i am jack" not in answer
    for unit in read["units"]:
        assert unit["kind"]
        assert unit["text"]
        assert unit["stamp"] is not None


def test_a_read_finds_a_statement_by_meaning_alone(tmp_path: Path) -> None:
    """The words may share nothing, and the statement still comes back."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder)
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "the third draft lives in drafts/", "kind": "note"},
            )
            engine.worker_for(config.local_directory).drain(5.0)
            return _data(
                await client.call_tool("recall_memory", {"query": "manuscript"})
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
                "record_memory",
                {"content": "always cite the commit", "kind": "rule"},
            )
            engine.worker_for(config.local_directory).drain(5.0)
            return _data(await client.call_tool("recall_memory", {"query": "cite"}))

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
            await client.call_tool(
                "record_memory", {"content": "a fact", "kind": "note"}
            )
            engine.worker_for(config.local_directory).drain(5.0)
            embedder._loaded = False
            return _data(await client.call_tool("recall_memory", {"query": "fact"}))

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
                await client.call_tool(
                    "record_memory",
                    {"content": f"draft note {index} noted", "kind": "NOTE"},
                )
            engine.worker_for(config.local_directory).drain(5.0)
            return (
                _data(await client.call_tool("recall_memory", {"query": "draft"})),
                _data(
                    await client.call_tool(
                        "recall_memory", {"query": "draft", "limit": 2}
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
            await client.call_tool("recall_memory", {"query": "  "})

    with pytest.raises(ToolError, match="query must not be empty"):
        asyncio.run(scenario())


def test_a_hand_written_statement_is_not_part_of_the_memory(tmp_path: Path) -> None:
    """The document is an export, so a hand edit to it is overwritten and said so.

    The memory is the record. A line typed into the export is not remembered, and
    the read says the document was rewritten rather than leaving a person to find
    out later that their edit went nowhere.
    """

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "a recorded fact", "kind": "NOTE"},
                raise_on_error=True,
            )
            document = config.local_directory / "MEMORY.md"
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n\nNOTE: a hand-written statement\n",
                encoding="utf-8",
            )
            return _data(
                await client.call_tool("recall_memory", {"query": "hand-written"})
            )

    answered = asyncio.run(scenario())
    assert answered["document_rewritten"] is True
    assert not [unit for unit in answered["units"] if "hand-written" in unit["text"]]
    # And the memory it was answering about is untouched.
    assert "a recorded fact" in (config.local_directory / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert "hand-written" not in (config.local_directory / "MEMORY.md").read_text(
        encoding="utf-8"
    )


def test_a_statement_adopted_from_an_export_is_counted_as_untyped(
    tmp_path: Path,
) -> None:
    """A memory recovered from an export has no type, and reads as `ITEM`."""

    config = _config(tmp_path)
    (config.local_directory).mkdir(parents=True, exist_ok=True)
    (config.local_directory / "MEMORY.md").write_text(
        "# MEMORY\ni am jack. i like LLMs.\n\na bare untyped note\n",
        encoding="utf-8",
    )
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            return _data(
                await client.call_tool(
                    "recall_memory", {"query": "untyped"}, raise_on_error=True
                )
            )

    answered = asyncio.run(scenario())
    assert "a bare untyped note" in [unit["text"] for unit in answered["units"]]
    assert answered["units"][0]["kind"] == "ITEM"


def test_a_read_keeps_serving_a_statement_deleted_from_the_export(
    tmp_path: Path,
) -> None:
    """Deleting a line from the export does not forget anything.

    A statement is forgotten by `forrecall_memory` or by nobody: the export is
    written from the record, so an edit to it is overwritten with the record and
    disclosed. A read that quietly served less because a line was cut from a file
    would be a read whose answer depends on a file it does not read.
    """

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "a doomed fact", "kind": "NOTE"},
                raise_on_error=True,
            )
            document = config.local_directory / "MEMORY.md"
            document.write_text(
                document.read_text(encoding="utf-8").replace("a doomed fact", ""),
                encoding="utf-8",
            )
            return _data(
                await client.call_tool(
                    "recall_memory", {"query": "doomed"}, raise_on_error=True
                )
            )

    answered = asyncio.run(scenario())
    assert [unit["text"] for unit in answered["units"]] == ["a doomed fact"]
    assert answered["document_rewritten"] is True


def test_an_unchanged_read_does_not_write_the_document_again(
    tmp_path: Path,
) -> None:
    """A read that finds the document in line says so, and writes nothing."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "a fact", "kind": "NOTE"},
                raise_on_error=True,
            )
            first = _data(await client.call_tool("recall_memory", {"query": "fact"}))
            second = _data(await client.call_tool("recall_memory", {"query": "fact"}))
            return {"first": first, "second": second}

    answered = asyncio.run(scenario())
    # A read brings both documents in line, and the account's is written out the
    # first time. The second read finds nothing to do and says so.
    assert answered["first"]["document_rewritten"] is True
    assert answered["second"]["document_rewritten"] is False


def test_the_two_memories_do_not_share_a_directory(tmp_path: Path) -> None:
    """One statement, one memory, and the answer says which it went to."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            here = _data(
                await client.call_tool(
                    "record_memory",
                    {"content": "a project fact", "kind": "PLAN"},
                    raise_on_error=True,
                )
            )
            everywhere = _data(
                await client.call_tool(
                    "record_memory",
                    {
                        "content": "short answers",
                        "kind": "PREFERENCE",
                        "scope": "global",
                    },
                    raise_on_error=True,
                )
            )
            return here, everywhere

    here, everywhere = asyncio.run(scenario())
    assert (here["scope"], everywhere["scope"]) == ("local", "global")
    assert config.local_directory != config.global_directory
    assert (
        (config.local_directory / "MEMORY.md")
        .read_text(encoding="utf-8")
        .endswith("a project fact\n")
    )
    assert (
        (config.global_directory / "MEMORY.md")
        .read_text(encoding="utf-8")
        .endswith("short answers\n")
    )


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
            await client.call_tool(
                "record_memory", {"content": "first only", "kind": "note"}
            )
            _engine(first, embedder).worker_for(first.local_directory).drain(5.0)
        async with Client(_app(second, embedder)) as client:
            read = _data(await client.call_tool("recall_memory", {"query": "first"}))
            missing = _data(await client.call_tool("recall_memory", {"query": "only"}))
        return read, missing

    read, missing = asyncio.run(scenario())
    assert "first only" not in json.dumps(read)
    assert missing["units"] == []


def test_no_tool_takes_a_user_or_a_project(tmp_path: Path) -> None:
    """One project and one account, so nothing has to be named to reach either."""

    app = _app(_config(tmp_path), FakeEmbedder())

    async def scenario() -> dict[str, set[str]]:
        async with Client(app) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            return {
                name: set(tool.inputSchema["properties"])
                for name, tool in tools.items()
            }

    properties = asyncio.run(scenario())
    assert properties["record_memory"] == {"content", "kind", "scope"}
    assert properties["recall_memory"] == {"query", "kind", "limit"}
    assert properties["forget_memory"] == {"text", "scope"}
    assert properties["record_handoff"] == {"content"}
    for name, keys in properties.items():
        assert "user" not in keys, name
        assert "project" not in keys, name


def test_a_kind_defaults_to_item_and_a_bad_one_is_refused(tmp_path: Path) -> None:
    """Every statement carries a type, so the parameter is optional and defaults."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            for name in ("record_memory", "record_memory"):
                schema = tools[name].inputSchema
                assert "kind" not in set(schema.get("required", []))
                assert schema["properties"]["kind"]["default"] == "ITEM"
            recorded = _data(
                await client.call_tool(
                    "record_memory", {"content": "a fact"}, raise_on_error=True
                )
            )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "record_memory",
                    {"content": "a fact", "kind": "two words"},
                    raise_on_error=True,
                )
            return recorded

    recorded = asyncio.run(scenario())
    assert recorded["kind"] == "ITEM"
    # The type is a column of the record, and the file states the memory itself.
    with MemoryIndex(config.local_directory) as index:
        assert [item.kind for item in index.statements()] == ["ITEM"]
    assert (
        (config.local_directory / "MEMORY.md")
        .read_text(encoding="utf-8")
        .endswith("a fact\n")
    )


def test_a_read_returns_the_kind_and_not_the_kind_in_the_text(
    tmp_path: Path,
) -> None:
    """The type is a field of the answer, and the text is the statement alone."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "the draft lives in drafts/", "kind": "CONVENTION"},
                raise_on_error=True,
            )
            return {
                "by_statement": _data(
                    await client.call_tool(
                        "recall_memory", {"query": "draft"}, raise_on_error=True
                    )
                ),
                "by_type": _data(
                    await client.call_tool(
                        "recall_memory",
                        {"query": "draft", "kind": "CONVENTION"},
                        raise_on_error=True,
                    )
                ),
                "by_another_type": _data(
                    await client.call_tool(
                        "recall_memory",
                        {"query": "draft", "kind": "PLAN"},
                        raise_on_error=True,
                    )
                ),
            }

    answered = asyncio.run(scenario())
    assert [unit["text"] for unit in answered["by_statement"]["units"]] == [
        "the draft lives in drafts/"
    ]
    # The type is in the answer, by either route, and never in the text.
    assert answered["by_statement"]["units"][0]["kind"] == "CONVENTION"
    assert "CONVENTION:" not in str(answered["by_statement"])
    assert answered["by_type"]["units"], "asking for a category must find it"
    assert answered["by_another_type"]["units"] == []


def test_an_empty_statement_is_refused(tmp_path: Path) -> None:
    app = _app(_config(tmp_path), FakeEmbedder())

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool("record_memory", {"content": "   ", "kind": "note"})

    with pytest.raises(ToolError, match="content must not be empty"):
        asyncio.run(scenario())


def test_a_reranker_orders_the_answer_when_it_is_switched_on(tmp_path: Path) -> None:
    """On by default, and when it runs it decides the order it reports."""

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = _engine(config, embedder, reranker_model="fake/reranker")
    engine.reranker = FakeReranker()  # type: ignore[assignment] - the seam itself, on a fake
    app = server.create_server(config, retrieval=engine, warm=False)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            for line in ("the draft is a plan", "the draft lives in docs"):
                await client.call_tool(
                    "record_memory", {"content": line, "kind": "NOTE"}
                )
            engine.worker_for(config.local_directory).drain(5.0)
            return _data(
                await client.call_tool("recall_memory", {"query": "the draft docs"})
            )

    read = asyncio.run(scenario())
    assert read["reranked"] is True
    # The words match both statements, and the reranker's scores put the one
    # holding more of the query first. Ordering only: both still come back.
    assert [unit["text"] for unit in read["units"]] == [
        "the draft lives in docs",
        "the draft is a plan",
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
                await client.call_tool(
                    "record_memory",
                    {"content": "a rule", "kind": "rule", "scope": "global"},
                )
            )

    recorded = asyncio.run(scenario())
    assert recorded["status"] == "recorded"
    assert recorded["embedded"] is False
    assert "a rule" in (config.global_directory / "MEMORY.md").read_text(
        encoding="utf-8"
    )


def test_a_handoff_replaces_the_previous_one(tmp_path: Path) -> None:
    """A project holds one handoff, not a list of them, and the count is reported."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            first = _data(
                await client.call_tool(
                    "record_handoff",
                    {"content": "session one: the corpus is a stub"},
                    raise_on_error=True,
                )
            )
            second = _data(
                await client.call_tool(
                    "record_handoff",
                    {"content": "session two: the corpus parses now"},
                    raise_on_error=True,
                )
            )
            return first, second

    first, second = asyncio.run(scenario())
    assert first["replaced"] == 0
    assert second["replaced"] == 1
    assert second["kind"] == "HANDOFF"

    with MemoryIndex(config.local_directory) as index:
        handoffs = [item for item in index.statements() if item.kind == "HANDOFF"]
    assert [item.text for item in handoffs] == ["session two: the corpus parses now"]
    # The replaced handoff is gone, and gone from the document rather than merely
    # from the newest one.
    document = (config.local_directory / "MEMORY.md").read_text(encoding="utf-8")
    assert "session two" in document
    assert "session one" not in document


def test_a_handoff_is_the_newest_statement_and_is_recallable(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "the corpus parser lives in corpus_io.py", "kind": "PLAN"},
                raise_on_error=True,
            )
            await client.call_tool(
                "record_handoff",
                {"content": "the corpus parser lives in corpus_io.py and works"},
                raise_on_error=True,
            )
            return _data(
                await client.call_tool(
                    "recall_memory", {"query": "corpus parser"}, raise_on_error=True
                )
            )

    answer = asyncio.run(scenario())
    handoffs = [unit for unit in answer["units"] if unit["kind"] == "HANDOFF"]
    assert len(handoffs) == 1
    assert handoffs[0]["text"].startswith("the corpus parser lives in")
    # A statement is a statement: the read returns the words, not the label.
    assert not handoffs[0]["text"].startswith("HANDOFF")


def test_forgetting_removes_exactly_the_statement_named(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "the draft lives in docs/", "kind": "NOTE"},
                raise_on_error=True,
            )
            await client.call_tool(
                "record_memory",
                {"content": "always cite the commit", "kind": "RULE"},
                raise_on_error=True,
            )
            return _data(
                await client.call_tool(
                    "forget_memory",
                    {"text": "the draft lives in docs/"},
                    raise_on_error=True,
                )
            )

    forgotten = asyncio.run(scenario())
    assert forgotten["forgotten"] == 1
    assert forgotten["kind"] == "NOTE"
    assert forgotten["text"] == "the draft lives in docs/"

    with MemoryIndex(config.local_directory) as index:
        remaining = [item.text for item in index.statements()]
    document = (config.local_directory / "MEMORY.md").read_text(encoding="utf-8")
    assert remaining == ["always cite the commit"]
    assert "the draft lives in docs/" not in document
    assert "always cite the commit" in document


def test_forgetting_needs_the_exact_text(tmp_path: Path) -> None:
    """Nothing is removed unless the text is the statement, and it says why."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> tuple[list[str], list[str]]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "the draft lives in docs/", "kind": "NOTE"},
                raise_on_error=True,
            )
            tools = {tool.name: tool for tool in await client.list_tools()}
            # The parameter is named for what it is: the text, not a search.
            assert set(tools["forget_memory"].inputSchema["properties"]) == {
                "text",
                "scope",
            }
            messages = []
            for text in ("draft", "the draft lives in docs", ""):
                with pytest.raises(ToolError) as raised:
                    await client.call_tool(
                        "forget_memory", {"text": text}, raise_on_error=True
                    )
                messages.append(str(raised.value))
            read = _data(
                await client.call_tool(
                    "recall_memory", {"query": "draft"}, raise_on_error=True
                )
            )
            return messages, [unit["text"] for unit in read["units"]]

    messages, remaining = asyncio.run(scenario())
    assert "nothing matched exactly" in messages[0]
    assert "nothing matched exactly" in messages[1]
    assert "must not be empty" in messages[2]
    # The statement is still there: a query that does not match changes nothing.
    assert remaining == ["the draft lives in docs/"]


def test_forgetting_refuses_when_the_text_is_ambiguous(tmp_path: Path) -> None:
    """The same words filed twice is not a reason to remove both of them."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> str:
        async with Client(app) as client:
            for kind in ("PLAN", "NOTE"):
                await client.call_tool(
                    "record_memory",
                    {"content": "the draft lives in docs/", "kind": kind},
                    raise_on_error=True,
                )
            with pytest.raises(ToolError) as raised:
                await client.call_tool(
                    "forget_memory",
                    {"text": "the draft lives in docs/"},
                    raise_on_error=True,
                )
            return str(raised.value)

    message = asyncio.run(scenario())
    assert "2 statements match that text exactly" in message
    assert "[local PLAN]" in message
    assert "[local NOTE]" in message


def test_a_read_reports_when_a_statement_was_added_and_how_often_it_was_recalled(
    tmp_path: Path,
) -> None:
    """A statement carries its own history, and a read counts itself in it."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "the draft lives in docs/", "kind": "NOTE"},
                raise_on_error=True,
            )
            first = _data(
                await client.call_tool(
                    "recall_memory", {"query": "draft"}, raise_on_error=True
                )
            )
            second = _data(
                await client.call_tool(
                    "recall_memory", {"query": "draft"}, raise_on_error=True
                )
            )
            return first["units"], second["units"]

    first, second = asyncio.run(scenario())
    assert first[0]["added_at"]
    # The read that is answering you has already counted itself, so the count is
    # "how many times this has been handed over", not "before this call".
    assert first[0]["recalls"] == 1
    assert first[0]["last_recalled_at"]
    assert second[0]["recalls"] == 2


def test_the_newest_statement_is_answered_before_an_older_equal_one(
    tmp_path: Path,
) -> None:
    """The preference is for what is new, and it is a number rather than a rule."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> list[dict[str, Any]]:
        async with Client(app) as client:
            for index in range(6):
                await client.call_tool(
                    "record_memory",
                    {
                        "content": f"the corpus parser note {index} lives here",
                        "kind": "NOTE",
                    },
                    raise_on_error=True,
                )
            return _data(
                await client.call_tool(
                    "recall_memory", {"query": "corpus parser"}, raise_on_error=True
                )
            )["units"]

    units = asyncio.run(scenario())
    assert len(units) > 1
    # The newest is in the answer, which is what the preference is for: it is
    # never dropped for being new, only never allowed to bury a better match.
    assert any("note 5" in unit["text"] for unit in units)
    dates = {unit["text"]: unit["added_at"] for unit in units}
    assert dates["the corpus parser note 5 lives here"] == max(dates.values())


def test_the_recency_bonus_decays_and_is_never_a_worse_match(tmp_path: Path) -> None:
    """The bonus is `recency_bonus` of a score, decaying, and nothing worse than that."""

    from memory_ultra_rag_mcp.read import _recency
    from memory_ultra_rag_mcp.retrieval import RetrievalSettings

    policy = RetrievalSettings(embedding_model="fake/model", recency_bonus=0.1)
    assert _recency(None, policy) == 0.0
    assert _recency(0, policy) == 0.1
    assert _recency(1, policy) == 0.05
    # A tenth more than its own score cannot lift a match that is half as good.
    strong, weak = 1.0, 0.5
    assert strong * (1.0 + _recency(0, policy)) > weak * (1.0 + _recency(0, policy))
    off = RetrievalSettings(embedding_model="fake/model", recency_bonus=0.0)
    assert off.recency_bonus == 0.0
    assert _recency(0, off) == 0.0


def test_recency_prefers_the_newest_and_keeps_a_better_match_first(
    tmp_path: Path,
) -> None:
    """The default nudge: new wins between equals, and the right answer still wins."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> list[dict[str, Any]]:
        async with Client(app) as client:
            # The one statement that holds the path the query names.
            await client.call_tool(
                "record_memory",
                {
                    "content": "the corpus parser note lives in corpus_io.py",
                    "kind": "NOTE",
                },
                raise_on_error=True,
            )
            for index in range(20):
                await client.call_tool(
                    "record_memory",
                    {
                        "content": f"the corpus parser note {index} lives in a.py",
                        "kind": "NOTE",
                    },
                    raise_on_error=True,
                )
            return _data(
                await client.call_tool(
                    "recall_memory", {"query": "corpus_io.py"}, raise_on_error=True
                )
            )["units"]

    units = asyncio.run(scenario())
    # Nineteen newer statements all mention the parser, and the one that holds
    # the path the query named is still the answer.
    assert units[0]["text"] == "the corpus parser note lives in corpus_io.py"


def test_a_zero_recency_bonus_answers_on_the_match_alone(tmp_path: Path) -> None:
    """Turning the preference off is a configuration, and it is honoured."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder(), recency_bonus=0.0)

    async def scenario() -> list[dict[str, Any]]:
        async with Client(app) as client:
            for index in range(6):
                await client.call_tool(
                    "record_memory",
                    {
                        "content": f"the corpus parser note {index} lives in a.py",
                        "kind": "NOTE",
                    },
                    raise_on_error=True,
                )
            return _data(
                await client.call_tool(
                    "recall_memory", {"query": "corpus parser"}, raise_on_error=True
                )
            )["units"]

    units = asyncio.run(scenario())
    assert len(units) > 1
    # With no preference the answer is the fused order, which is the reranker and
    # the two sides, and the scores are the ones they produced.
    assert [unit["score"] for unit in units] == sorted(
        (unit["score"] for unit in units), reverse=True
    )


def test_the_preference_is_among_the_statements_that_matched(tmp_path: Path) -> None:
    """A newer statement that does not match the query is not the answer."""

    config = _config(tmp_path)
    app = _app(config, FakeEmbedder())

    async def scenario() -> list[dict[str, Any]]:
        async with Client(app) as client:
            await client.call_tool(
                "record_memory",
                {"content": "the gitlab runner uses a 4 GiB runner", "kind": "NOTE"},
                raise_on_error=True,
            )
            await client.call_tool(
                "record_memory",
                {
                    "content": "always cite the commit that introduced a change",
                    "kind": "RULE",
                },
                raise_on_error=True,
            )
            return _data(
                await client.call_tool(
                    "recall_memory", {"query": "gitlab runner"}, raise_on_error=True
                )
            )["units"]

    units = asyncio.run(scenario())
    # The newer statement is first in the document and it does not match the
    # query, so it is not the answer: the preference is among statements that did.
    assert units[0]["text"] == "the gitlab runner uses a 4 GiB runner"
    assert "always cite the commit" not in [unit["text"] for unit in units]


def test_the_scope_argument_names_the_two_destinations_and_no_rule_for_choosing(
    tmp_path: Path,
) -> None:
    """The server says where a statement can go, and leaves the judgement semantic.

    A set of phrases that make a statement global would be worse than nothing: an
    agent that waits for a marker files a user-wide rule in the project, and the
    rule is then invisible everywhere else.
    """

    from memory_ultra_rag_mcp.instructions import SERVER_INSTRUCTIONS

    app = _app(_config(tmp_path), FakeEmbedder())

    async def scenario() -> str:
        async with Client(app) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            return tools["record_memory"].inputSchema["properties"]["scope"][
                "description"
            ]

    described = asyncio.run(scenario())
    assert "this project" in described
    assert "across projects" in described
    # The choice is described as read off the substance, and the absence of a
    # marker is explicitly not a reason to file local.
    assert "wording" in described or "substance" in described
    for marker in ('"always"', '"never"', '"from now on"', "in every project"):
        assert marker not in described
        assert marker not in SERVER_INSTRUCTIONS
    assert "most prompts carry no marker" in described
    assert "prompts carry no marker of their own" in SERVER_INSTRUCTIONS
