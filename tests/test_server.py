"""The four tools, the two roots they touch, and what a read returns."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from memory_ultra_rag_mcp import server
from memory_ultra_rag_mcp.config import resolve_config
from memory_ultra_rag_mcp.index import index_path
from memory_ultra_rag_mcp.store import append_round, append_statement


def _config(tmp_path: Path):
    project = tmp_path / "thesis"
    project.mkdir()
    return resolve_config(
        project_root=project,
        storage_root=tmp_path / "shared",
    )


def _data(result: Any) -> Any:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    return json.loads(result.content[0].text)


def test_the_surface_is_the_four_memory_tools(tmp_path: Path) -> None:
    """Two verbs, two scopes, and nothing else to choose between."""

    app = server.create_server(_config(tmp_path))

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
    app = server.create_server(config)

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


def test_a_global_statement_is_written_under_the_storage_root(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            recorded = _data(
                await client.call_tool(
                    "set_memory_global",
                    {"user_id": "ahmed", "content": "always cite the commit"},
                )
            )
            read = _data(
                await client.call_tool(
                    "get_memory_global", {"user_id": "ahmed", "query": "cite"}
                )
            )
            return recorded, read

    recorded, read = asyncio.run(scenario())
    assert recorded["directory"] == str(config.global_root / "ahmed")
    assert read["scope"] == "global"
    assert [unit["text"] for unit in read["units"]] == ["always cite the commit"]
    assert read["units"][0]["kind"] == "statement"
    assert (config.global_root / "ahmed" / "MEMORY.md").is_file()


def test_a_read_returns_the_matching_units_and_nothing_else(tmp_path: Path) -> None:
    """The answer is what matched; a memory is never handed over whole."""

    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            append_statement(config.local_directory, "docs/ has the draft")
            append_statement(config.local_directory, "tests run with uv")
            append_round(config.local_directory, ["where is the draft"], ["in docs/"])
            append_round(config.local_directory, ["morning coffee"], ["tea"])
            return _data(await client.call_tool("get_memory_local", {"query": "draft"}))

    read = asyncio.run(scenario())
    assert read["scope"] == "local"
    assert read["matched_by"] == "all-words"
    # Both units that hold "draft" come back, in the index's order.
    assert {unit["text"] for unit in read["units"]} == {
        "docs/ has the draft",
        "where is the draft\nin docs/",
    }
    assert read["returned"] == 2
    assert read["truncated"] is False
    assert read["searched"]["directory"] == str(config.local_directory)
    # The other statement and the coffee round are not in the answer.
    answer = json.dumps(read)
    assert "tests run with uv" not in answer
    assert "morning coffee" not in answer
    # Every unit says where it came from, so a caller can go to the original.
    assert {unit["source"] for unit in read["units"]} <= {
        "MEMORY.md",
        *(
            f"project/{path.name}"
            for path in (config.local_directory / "project").glob("*.md")
        ),
    }


def test_a_read_finds_a_statement_and_a_round_together(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            append_statement(config.local_directory, "the draft index is in docs/")
            append_round(config.local_directory, ["where is the draft"], ["in docs/"])
            return _data(await client.call_tool("get_memory_local", {"query": "draft"}))

    read = asyncio.run(scenario())
    assert read["returned"] == 2
    assert {unit["kind"] for unit in read["units"]} == {"statement", "round"}


def test_a_query_falls_back_to_the_words_that_exist(tmp_path: Path) -> None:
    """A question with words no round holds still finds the round that is close."""

    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            append_round(config.local_directory, ["where is the draft"], ["in docs/"])
            question = _data(
                await client.call_tool(
                    "get_memory_local", {"query": "where does the draft live"}
                )
            )
            exact = _data(
                await client.call_tool("get_memory_local", {"query": "draft"})
            )
            return question, exact

    question, exact = asyncio.run(scenario())
    assert exact["matched_by"] == "all-words"
    assert question["matched_by"] == "rarest-words"
    assert [unit["text"] for unit in question["units"]] == [
        "where is the draft\nin docs/"
    ]


def test_a_read_is_capped_and_says_so(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            for index in range(5):
                append_round(config.local_directory, [f"draft note {index}"], ["noted"])
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

    app = server.create_server(_config(tmp_path))

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool("get_memory_local", {"query": "  "})

    with pytest.raises(ToolError, match="query must not be empty"):
        asyncio.run(scenario())


def test_a_read_answers_from_a_memory_edited_by_hand(tmp_path: Path) -> None:
    """The index follows the files, so a hand edit is what gets read."""

    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            first = _data(
                await client.call_tool("get_memory_local", {"query": "quixotic"})
            )
            standing = config.local_directory / "MEMORY.md"
            standing.write_text(
                standing.read_text(encoding="utf-8")
                + "\nthe quixotic release is in tags\n",
                encoding="utf-8",
            )
            second = _data(
                await client.call_tool("get_memory_local", {"query": "quixotic"})
            )
            return first, second

    before, after = asyncio.run(scenario())
    assert before["units"] == []
    assert [unit["text"] for unit in after["units"]] == [
        "the quixotic release is in tags"
    ]


def test_the_index_is_derived_and_can_be_deleted(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            append_statement(config.local_directory, "the draft lives in docs/")
            await client.call_tool("get_memory_local", {"query": "draft"})
            index_path(config.local_directory).unlink()
            return _data(await client.call_tool("get_memory_local", {"query": "draft"}))

    read = asyncio.run(scenario())
    assert [unit["text"] for unit in read["units"]] == ["the draft lives in docs/"]
    assert index_path(config.local_directory).is_file()


def test_the_two_kinds_do_not_share_a_directory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            local = _data(await client.call_tool("get_memory_local", {"query": "x"}))
            default_user = _data(
                await client.call_tool("get_memory_global", {"query": "x"})
            )
            return local, default_user

    local, default_user = asyncio.run(scenario())
    assert local["searched"]["directory"] == str(config.local_directory)
    assert default_user["searched"]["directory"] == str(config.global_root / "default")
    # A first read brings each scope into being, as upstream does, and each scope
    # indexes its own files.
    assert (config.local_directory / "MEMORY.md").is_file()
    assert (config.global_root / "default" / "MEMORY.md").is_file()
    assert local["searched"]["index"] != default_user["searched"]["index"]
    assert local["units"] == []
    assert default_user["units"] == []


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
        async with Client(server.create_server(first)) as client:
            await client.call_tool("set_memory_local", {"content": "first only"})
        async with Client(server.create_server(second)) as client:
            read = _data(await client.call_tool("get_memory_local", {"query": "first"}))
            missing = _data(
                await client.call_tool("get_memory_local", {"query": "only"})
            )
        return read, missing

    read, missing = asyncio.run(scenario())
    assert "first only" not in json.dumps(read)
    assert missing["units"] == []


def test_an_unusable_user_id_is_refused(tmp_path: Path) -> None:
    app = server.create_server(_config(tmp_path))

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool(
                "get_memory_global", {"user_id": "../escape", "query": "x"}
            )

    with pytest.raises(ToolError, match="Invalid user_id format"):
        asyncio.run(scenario())


def test_an_empty_statement_is_refused(tmp_path: Path) -> None:
    app = server.create_server(_config(tmp_path))

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool("set_memory_local", {"content": "   "})

    with pytest.raises(ToolError, match="content must not be empty"):
        asyncio.run(scenario())


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
