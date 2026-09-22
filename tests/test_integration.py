"""Fidelity, measured: the real upstream memory server, served and called.

This is the suite that proves the claim in the README. It starts the installed
console script as a child process, exactly as an MCP client would, with
``--ultrarag-root`` pointing at a real checkout, and checks the surface, the
storage layout, and the file format against what UltraRAG's own server produces.

It is opt-in because it needs a UltraRAG checkout at the pinned revision:

    ULTRARAG_ROOT=/path/to/UltraRAG uv run --frozen pytest -q -m integration
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from memory_ultra_rag_mcp.manifest import MEMORY_TOOLS

ULTRARAG_ROOT = os.environ.get("ULTRARAG_ROOT")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ULTRARAG_ROOT,
        reason="set ULTRARAG_ROOT to a pinned UltraRAG checkout to run this suite",
    ),
]

ENTRY_RE = re.compile(r"^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", re.MULTILINE)


def _data(result: Any) -> Any:
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


@pytest.fixture()
def transport(tmp_path: Path) -> StdioTransport:
    workspace = tmp_path / "workspace"
    return StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "memory_ultra_rag_mcp",
            "--ultrarag-root",
            str(ULTRARAG_ROOT),
            "--workspace-root",
            str(workspace),
        ],
    )


def test_the_served_surface_is_the_two_kinds_of_memory(
    transport: StdioTransport,
) -> None:
    async def scenario() -> list[str]:
        async with Client(transport) as client:
            return sorted(tool.name for tool in await client.list_tools())

    assert asyncio.run(scenario()) == sorted(
        [*MEMORY_TOOLS, "get_local_memory", "save_local_memory"]
    )


def test_local_memory_is_written_under_the_projects_own_scope(
    transport: StdioTransport, tmp_path: Path
) -> None:
    """Two projects keep two memories, in UltraRAG's own layout and format."""
    question = "Where does the draft live?"
    answer = "In the project directory."

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(transport) as client:
            first = _data(
                await client.call_tool(
                    "save_local_memory",
                    {
                        "project_id": "thesis",
                        "q_ls": [question],
                        "ans_ls": [answer],
                    },
                )
            )
            second = _data(
                await client.call_tool(
                    "save_local_memory",
                    {"project_id": "notes", "q_ls": ["Other?"], "ans_ls": ["Yes."]},
                )
            )
            return first, second

    first, second = asyncio.run(scenario())
    assert first == {"project_id": "thesis", "scope": "local-thesis", "status": "saved"}
    assert second["scope"] == "local-notes"

    memory = tmp_path / "workspace" / "ui-storage" / "memory"
    thesis_rounds = sorted((memory / "local-thesis" / "project").glob("*.md"))
    notes_rounds = sorted((memory / "local-notes" / "project").glob("*.md"))
    assert len(thesis_rounds) == 1 and len(notes_rounds) == 1

    text = thesis_rounds[0].read_text(encoding="utf-8")
    assert text.startswith(f"# Project Memory {thesis_rounds[0].stem}\n")
    assert ENTRY_RE.search(text)
    assert f"- user: {question}" in text
    assert f"- assistant: {answer}" in text
    # The other project's round is in its own file, not this one.
    assert "Other?" not in text


def test_local_memory_and_global_memory_stay_apart(
    transport: StdioTransport, tmp_path: Path
) -> None:
    """A project's memory and a user's memory are two files in one tree."""

    async def scenario() -> tuple[dict[str, Any], str]:
        async with Client(transport) as client:
            local = _data(
                await client.call_tool("get_local_memory", {"project_id": "thesis"})
            )
            global_read = _data(
                await client.call_tool("get_global_memory", {"user_id": "ahmed"})
            )
            return local, global_read["global_memory_content"]

    local, global_content = asyncio.run(scenario())
    assert local["scope"] == "local-thesis"
    assert "MEMORY" in local["local_memory_content"]
    assert "MEMORY" in global_content

    memory = tmp_path / "workspace" / "ui-storage" / "memory"
    assert (memory / "local-thesis" / "MEMORY.md").is_file()
    assert (memory / "ahmed" / "MEMORY.md").is_file()
    # The two words differ: local memory is not a user's global memory.
    assert (memory / "local-thesis" / "MEMORY.md").read_text(encoding="utf-8") == local[
        "local_memory_content"
    ]


@pytest.mark.parametrize("project_id", ["../escape", "a/b", "with space"])
def test_an_unusable_project_identifier_is_refused(
    transport: StdioTransport, project_id: str
) -> None:
    async def scenario() -> tuple[bool, str]:
        async with Client(transport) as client:
            result = await client.call_tool(
                "get_local_memory", {"project_id": project_id}, raise_on_error=False
            )
            return result.is_error, "\n".join(
                getattr(block, "text", "") for block in result.content
            )

    is_error, message = asyncio.run(scenario())
    assert is_error is True
    assert "project_id" in message


def test_memory_is_written_where_the_ui_reads_it(
    transport: StdioTransport, tmp_path: Path
) -> None:
    """The layout and the file format are UltraRAG's, not this package's."""
    user = "fidelity"
    question = "What do you remember about me?"
    answer = "Only what is in your global memory file."

    async def scenario() -> tuple[str, Any]:
        async with Client(transport) as client:
            read = _data(await client.call_tool("get_global_memory", {"user_id": user}))
            await client.call_tool(
                "save_memory",
                {"user_id": user, "q_ls": [question], "ans_ls": [answer]},
            )
            return read["global_memory_content"], read["current_user_id"]

    content, current_user = asyncio.run(scenario())
    user_root = tmp_path / "workspace" / "ui-storage" / "memory" / user

    # Upstream's template, created on first read.
    assert "MEMORY" in content
    assert current_user == user
    assert (user_root / "MEMORY.md").read_text(encoding="utf-8") == content

    daily = sorted((user_root / "project").glob("*.md"))
    assert len(daily) == 1
    text = daily[0].read_text(encoding="utf-8")
    assert text.startswith(f"# Project Memory {daily[0].stem}\n")
    assert ENTRY_RE.search(text)
    assert f"- user: {question}" in text
    assert f"- assistant: {answer}" in text


def test_upstream_validation_is_unchanged(transport: StdioTransport) -> None:
    """An invalid user_id is refused by upstream, with upstream's message."""

    async def scenario() -> tuple[bool, str]:
        async with Client(transport) as client:
            result = await client.call_tool(
                "get_global_memory", {"user_id": "../escape"}, raise_on_error=False
            )
            return result.is_error, _data_string(result)

    is_error, message = asyncio.run(scenario())
    assert is_error is True
    assert "Invalid user_id format" in message


def _data_string(result: Any) -> str:
    parts = [getattr(block, "text", "") for block in result.content]
    return "\n".join(parts)


def test_serving_writes_nothing_into_the_checkout(transport: StdioTransport) -> None:
    """Reading memory never writes into the UltraRAG checkout it is served from."""
    memory_dir = Path(str(ULTRARAG_ROOT)) / "servers" / "memory"
    before = {str(path.relative_to(memory_dir)) for path in memory_dir.rglob("*")}

    async def scenario() -> None:
        async with Client(transport) as client:
            await client.call_tool("get_global_memory", {"user_id": "untouched"})

    asyncio.run(scenario())
    after = {str(path.relative_to(memory_dir)) for path in memory_dir.rglob("*")}
    assert after == before
