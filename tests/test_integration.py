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
    """A real child process on a real pipe, with a model that costs nothing.

    The transport is what these tests are about — a process, a pipe, the real
    server object. The model is not: it would fetch 65 MB per run and put a
    model's opinion inside an assertion about isolation.
    """

    runner = Path(__file__).resolve().parent / "serve_with_fake.py"
    return StdioTransport(
        command=sys.executable,
        args=[
            str(runner),
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
                    "set_memory_local",
                    {"content": "The thesis lives in drafts/", "type": "note"},
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
    assert other["scope"] == "local"
    assert "The thesis lives in drafts/" not in json.dumps(other)
    assert other["units"] == []
    assert not (notes / ".memory-rag" / "index-vectors.sqlite3").exists() or not any(
        unit["source"].startswith("project/") for unit in other["units"]
    )


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
                {"content": "Prefer British English", "type": "preference"},
            )
        async with Client(_transport(notes, storage)) as client:
            read = _data(
                await client.call_tool("get_memory_global", {"query": "English"})
            )
            return read, [unit["source"] for unit in read["units"]]

    read, sources = asyncio.run(scenario())
    # Both projects answered from the same file in the shared tree.
    assert sources == ["MEMORY.md"]
    assert [unit["text"] for unit in read["units"]] == ["Prefer British English"]
    assert read["scope"] == "global"
    # The second project's server read the same global memory, which is the
    # account's one, and not a per-project copy.
    assert "Prefer British English" in json.dumps(read)
