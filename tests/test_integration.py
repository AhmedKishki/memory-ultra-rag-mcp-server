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
                    {"content": "The thesis lives in drafts/", "kind": "note"},
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
    # The other project has its own file, or none, and no unit of the thesis's is
    # in it either way.
    other_file = notes / ".memory-rag" / "memory.sqlite3"
    assert not other_file.exists() or not any(
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
                {"content": "Prefer British English", "kind": "preference"},
            )
        async with Client(_transport(notes, storage)) as client:
            read = _data(
                await client.call_tool("get_memory_global", {"query": "English"})
            )
            return read, [unit["text"] for unit in read["units"]]

    read, answers = asyncio.run(scenario())
    # Both projects answered from the same memory in the shared tree, and the
    # answer is the statement rather than a file: a read answers a question.
    assert answers == ["Prefer British English"]
    assert read["scope"] == "global"
    assert [unit["text"] for unit in read["units"]] == ["Prefer British English"]
    assert read["scope"] == "global"
    # The second project's server read the same global memory, which is the
    # account's one, and not a per-project copy.
    assert "Prefer British English" in json.dumps(read)


def test_the_answer_says_what_to_do_when_nothing_matched(
    two_projects: tuple[Path, Path, Path],
) -> None:
    """An empty answer is where an agent decides whether to try again.

    The instructions tell it to try other words; an answer that says only
    `returned: 0` leaves the guidance in a document the caller read once, so the
    answer carries the next step with it.
    """

    thesis, _notes, storage = two_projects
    thesis.mkdir()

    async def scenario() -> dict[str, Any]:
        async with Client(_transport(thesis, storage)) as client:
            await client.call_tool(
                "set_memory_local",
                {"content": "the draft lives in drafts/", "kind": "NOTE"},
                raise_on_error=True,
            )
            return {
                "miss": _data(
                    await client.call_tool(
                        "get_memory_local", {"query": "penguin migration"}
                    )
                ),
                "hit": _data(
                    await client.call_tool("get_memory_local", {"query": "draft"})
                ),
            }

    answers = asyncio.run(scenario())
    assert answers["miss"]["returned"] == 0
    assert "Try other words" in answers["miss"]["hint"]
    # And an answer that matched says nothing of the sort, because there is
    # nothing to retry.
    assert answers["hit"]["returned"] == 1
    assert "hint" not in answers["hit"]
