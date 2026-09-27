"""Project isolation over the real stdio server.

Two projects, two servers, one shared storage tree: each project's memory stays
inside its own repository, and both see the same global memory. Nothing here
needs a UltraRAG checkout - the behaviour is this package's, taken from upstream's
formats and checked against the fixtures.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport


def _data(result: Any) -> Any:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    return json.loads(result.content[0].text)


def _transport(project: Path, storage: Path) -> StdioTransport:
    return StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "memory_ultra_rag_mcp",
            "--project-root",
            str(project),
            "--storage-root",
            str(storage),
        ],
    )


@pytest.fixture()
def two_projects(tmp_path: Path) -> tuple[Path, Path, Path]:
    storage = tmp_path / "shared" / "ui-storage"
    return tmp_path / "thesis", tmp_path / "notes", storage


def test_each_project_keeps_its_memory_in_its_own_repository(
    two_projects: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    thesis, notes, storage = two_projects
    thesis.mkdir()
    notes.mkdir()

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(_transport(thesis, storage)) as client:
            recorded = _data(
                await client.call_tool(
                    "set_memory_local", {"content": "The thesis lives in drafts/"}
                )
            )
        async with Client(_transport(notes, storage)) as client:
            other = _data(
                await client.call_tool("get_memory_local", {"query": "thesis"})
            )
            return recorded, other

    recorded, other = asyncio.run(scenario())
    assert recorded["directory"] == str(thesis / ".memory-rag")

    # The statement is inside the thesis repository, not in the shared tree.
    standing = (thesis / ".memory-rag" / "MEMORY.md").read_text(encoding="utf-8")
    assert "The thesis lives in drafts/" in standing
    assert not (storage / "memory" / "local-thesis").exists()

    # The other project reads its own memory, which says nothing about the thesis.
    assert other["searched"]["directory"] == str(notes / ".memory-rag")
    assert "The thesis lives in drafts/" not in json.dumps(other)
    assert other["units"] == []


def test_both_projects_share_the_global_memory(
    two_projects: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    thesis, notes, storage = two_projects
    thesis.mkdir()
    notes.mkdir()

    async def scenario() -> tuple[dict[str, Any], str]:
        async with Client(_transport(thesis, storage)) as client:
            await client.call_tool(
                "set_memory_global",
                {"user_id": "ahmed", "content": "Prefer British English"},
            )
        async with Client(_transport(notes, storage)) as client:
            read = _data(
                await client.call_tool(
                    "get_memory_global", {"user_id": "ahmed", "query": "English"}
                )
            )
            return read, read["searched"]["directory"]

    read, directory = asyncio.run(scenario())
    assert directory == str(storage / "memory" / "ahmed")
    assert [unit["text"] for unit in read["units"]] == ["Prefer British English"]
    assert read["scope"] == "global"
    # The second project's server read the same global tree.
    assert "Prefer British English" in json.dumps(read)
