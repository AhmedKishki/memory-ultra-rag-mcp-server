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
            saved = _data(
                await client.call_tool(
                    "save_local_memory",
                    {"q_ls": ["Thesis question"], "ans_ls": ["Thesis answer"]},
                )
            )
        async with Client(_transport(notes, storage)) as client:
            other = _data(await client.call_tool("get_local_memory", {}))
            return saved, other

    saved, other = asyncio.run(scenario())
    assert saved["directory"] == str(thesis / ".memory-rag")

    # The round is inside the thesis repository, not in the shared tree.
    rounds = sorted((thesis / ".memory-rag" / "project").glob("*.md"))
    assert len(rounds) == 1
    assert "Thesis question" in rounds[0].read_text(encoding="utf-8")
    assert not (storage / "memory" / "local-thesis").exists()

    # The other project reads its own memory, which says nothing about the thesis.
    assert other["directory"] == str(notes / ".memory-rag")
    assert "Thesis question" not in other["local_memory_content"]


def test_both_projects_share_the_global_memory(
    two_projects: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    thesis, notes, storage = two_projects
    thesis.mkdir()
    notes.mkdir()

    async def scenario() -> tuple[str, str]:
        async with Client(_transport(thesis, storage)) as client:
            await client.call_tool(
                "save_memory",
                {"user_id": "ahmed", "q_ls": ["Global fact"], "ans_ls": ["Noted."]},
            )
        async with Client(_transport(notes, storage)) as client:
            read = _data(
                await client.call_tool("get_global_memory", {"user_id": "ahmed"})
            )
            return read["current_user_id"], read["global_memory_content"]

    user_id, content = asyncio.run(scenario())
    assert user_id == "ahmed"
    global_rounds = sorted((storage / "memory" / "ahmed" / "project").glob("*.md"))
    assert len(global_rounds) == 1
    assert "Global fact" in global_rounds[0].read_text(encoding="utf-8")
    # The second project's server read the same global tree.
    assert "MEMORY" in content
