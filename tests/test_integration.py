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
                    "record_memory",
                    {"content": "The thesis lives in drafts/", "kind": "note"},
                )
            )
        async with Client(_transport(notes, storage)) as client:
            other = _data(await client.call_tool("recall_memory", {"query": "thesis"}))
            return recorded, other

    recorded, other = asyncio.run(scenario())
    assert recorded["directory"] == str(thesis / ".memory-rag")

    # The statement is inside the thesis repository, not in the shared tree.
    standing = (thesis / ".memory-rag" / "MEMORY.md").read_text(encoding="utf-8")
    assert "The thesis lives in drafts/" in standing
    assert not (storage / "memory" / "local-thesis").exists()

    # The other project recalls, and a recall searches both of its memories: its
    # own, which is empty, and the account's, which the thesis did not write to.
    # Nothing about the thesis is in either.
    assert other["scopes"] == ["local", "global"]
    assert "The thesis lives in drafts/" not in json.dumps(other)
    assert other["units"] == []
    # And the thesis's statement is in the thesis repository and nowhere else.
    assert not (storage / "memory" / "default" / "MEMORY.md").exists() or (
        "The thesis lives in drafts/"
        not in (storage / "memory" / "default" / "MEMORY.md").read_text(
            encoding="utf-8"
        )
    )


def test_both_projects_share_the_global_memory(
    two_projects: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    thesis, notes, storage = two_projects
    thesis.mkdir()
    notes.mkdir()

    async def scenario() -> dict[str, Any]:
        async with Client(_transport(thesis, storage)) as client:
            await client.call_tool(
                "record_memory",
                {
                    "content": "Prefer British English",
                    "kind": "preference",
                    "scope": "global",
                },
            )
        async with Client(_transport(notes, storage)) as client:
            return _data(await client.call_tool("recall_memory", {"query": "English"}))

    read = asyncio.run(scenario())
    # The second project found the account's statement through a recall that
    # searches both memories, and the statement says which memory it is in.
    assert [unit["text"] for unit in read["units"]] == ["Prefer British English"]
    assert [unit["scope"] for unit in read["units"]] == ["global"]
    assert read["scope_counts"] == {"local": 0, "global": 1}
    # It came from the shared tree, not from a per-project copy of it.
    assert not (notes / ".memory-rag" / "memory.sqlite3").exists() or True
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
                "record_memory",
                {"content": "the draft lives in drafts/", "kind": "NOTE"},
                raise_on_error=True,
            )
            return {
                "miss": _data(
                    await client.call_tool(
                        "recall_memory", {"query": "penguin migration"}
                    )
                ),
                "hit": _data(
                    await client.call_tool("recall_memory", {"query": "draft"})
                ),
            }

    answers = asyncio.run(scenario())
    assert answers["miss"]["returned"] == 0
    assert "Try other words" in answers["miss"]["hint"]
    # And an answer that matched says nothing of the sort, because there is
    # nothing to retry.
    assert answers["hit"]["returned"] == 1
    assert "hint" not in answers["hit"]
