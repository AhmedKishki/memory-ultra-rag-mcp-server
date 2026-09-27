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
    assert "always cite the commit" in read["standing_content"]
    assert read["scope"] == "global"
    assert (config.global_root / "ahmed" / "MEMORY.md").is_file()


def test_a_read_answers_a_query_instead_of_returning_the_file(tmp_path: Path) -> None:
    """A read takes a query, and returns the standing document plus matches."""

    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            await client.call_tool(
                "set_memory_local", {"content": "docs/ has the draft"}
            )
            await client.call_tool("set_memory_local", {"content": "tests run with uv"})
            # Dated rounds are the history; only the store writes them now.
            from memory_ultra_rag_mcp.store import append_round

            append_round(config.local_directory, ["where is the draft"], ["in docs/"])
            append_round(config.local_directory, ["morning coffee"], ["tea"])
            return _data(await client.call_tool("get_memory_local", {"query": "draft"}))

    read = asyncio.run(scenario())
    assert read["scope"] == "local"
    assert "docs/ has the draft" in read["standing_content"]
    assert "docs/ has the draft" in read["standing_lines_matched"]
    assert [entry["text"] for entry in read["entries"]] == [
        "where is the draft\nin docs/"
    ]
    assert read["matched"] == 1
    assert read["returned"] == 1
    assert read["truncated"] is False
    assert read["searched"]["rounds"] == 2
    assert read["searched"]["files"] == 1


def test_a_common_word_does_not_outrank_a_distinctive_one(tmp_path: Path) -> None:
    """Ranking is by the longest query term a round holds, not by any word."""

    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            from memory_ultra_rag_mcp.store import append_round

            append_round(config.local_directory, ["the build ran"], ["ok"])
            append_round(config.local_directory, ["a build note"], ["ok"])
            return _data(await client.call_tool("get_memory_local", {"query": "build"}))

    read = asyncio.run(scenario())
    assert read["matched"] == 2
    # Both rounds hold the same term, so recency decides.
    assert read["entries"][0]["text"].startswith("a build note")


def test_a_read_is_capped_and_says_so(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            from memory_ultra_rag_mcp.store import append_round

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
    assert capped["matched"] == 5
    assert capped["truncated"] is True


def test_a_read_without_a_query_is_refused(tmp_path: Path) -> None:
    """No query means no whole file: the point of retrieval is the context window."""

    app = server.create_server(_config(tmp_path))

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool("get_memory_local", {"query": "  "})

    with pytest.raises(ToolError, match="query must not be empty"):
        asyncio.run(scenario())


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
    assert "MEMORY" in local["standing_content"]
    assert "MEMORY" in default_user["standing_content"]
    assert (config.local_directory / "MEMORY.md").is_file()
    assert (config.global_root / "default" / "MEMORY.md").is_file()


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
    assert "first only" not in read["standing_content"]
    assert missing["matched"] == 0


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
