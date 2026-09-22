"""The four tools, and the two roots they write to."""

from __future__ import annotations

import asyncio
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
        workspace_root=tmp_path / "ws",
    )


def _data(result: Any) -> Any:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    import json

    return json.loads(result.content[0].text)


def test_the_surface_is_the_four_memory_tools(tmp_path: Path) -> None:
    app = server.create_server(_config(tmp_path))

    async def scenario() -> list[str]:
        async with Client(app) as client:
            return sorted(tool.name for tool in await client.list_tools())

    assert asyncio.run(scenario()) == [
        "get_global_memory",
        "get_local_memory",
        "save_local_memory",
        "save_memory",
    ]


def test_a_local_round_is_written_inside_the_project(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> dict[str, Any]:
        async with Client(app) as client:
            return _data(
                await client.call_tool(
                    "save_local_memory",
                    {"q_ls": ["Where does the draft live?"], "ans_ls": ["Here."]},
                )
            )

    saved = asyncio.run(scenario())
    assert saved["status"] == "saved"
    assert saved["written"].startswith(str(config.local_directory))
    rounds = sorted((config.local_directory / "project").glob("*.md"))
    assert len(rounds) == 1
    assert "- user: Where does the draft live?" in rounds[0].read_text(encoding="utf-8")
    # The project's standing document was created by the write, as upstream does.
    assert (config.local_directory / "MEMORY.md").is_file()


def test_a_global_round_is_written_under_the_storage_root(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            saved = _data(
                await client.call_tool(
                    "save_memory",
                    {"user_id": "ahmed", "q_ls": ["Remember X"], "ans_ls": ["Kept."]},
                )
            )
            read = _data(
                await client.call_tool("get_global_memory", {"user_id": "ahmed"})
            )
            return saved, read

    saved, read = asyncio.run(scenario())
    assert saved["written"].startswith(str(config.global_root / "ahmed"))
    assert read["current_user_id"] == "ahmed"
    assert "MEMORY" in read["global_memory_content"]
    assert (config.global_root / "ahmed" / "MEMORY.md").is_file()


def test_the_two_kinds_do_not_share_a_directory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = server.create_server(config)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(app) as client:
            local = _data(await client.call_tool("get_local_memory", {}))
            default = _data(await client.call_tool("get_global_memory", {}))
            return local, default

    local, default_user = asyncio.run(scenario())
    assert local["directory"] == str(config.local_directory)
    assert default_user["current_user_id"] == "default"
    assert "MEMORY" in default_user["global_memory_content"]
    assert (config.local_directory / "MEMORY.md").is_file()
    assert (config.global_root / "default" / "MEMORY.md").is_file()


def test_an_unusable_user_id_is_refused(tmp_path: Path) -> None:
    app = server.create_server(_config(tmp_path))

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool("get_global_memory", {"user_id": "../escape"})

    with pytest.raises(ToolError, match="Invalid user_id format"):
        asyncio.run(scenario())


def test_an_empty_message_is_refused(tmp_path: Path) -> None:
    app = server.create_server(_config(tmp_path))

    async def scenario() -> None:
        async with Client(app) as client:
            await client.call_tool(
                "save_local_memory", {"q_ls": [""], "ans_ls": ["answer"]}
            )

    with pytest.raises(ToolError, match="user_message cannot be empty"):
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


def test_the_file_name_matches_the_stamp_inside_it(tmp_path: Path) -> None:
    """One write produces the file name and the heading from one moment."""
    app = server.create_server(_config(tmp_path))

    async def scenario() -> str:
        async with Client(app) as client:
            saved = _data(
                await client.call_tool(
                    "save_local_memory", {"q_ls": ["q"], "ans_ls": ["a"]}
                )
            )
            return saved["written"]

    written = Path(asyncio.run(scenario()))
    text = written.read_text(encoding="utf-8")
    header = text.splitlines()[0].removeprefix("# Project Memory ")
    stamp_date = text.splitlines()[2].split()[1]
    assert written.stem == header == stamp_date
