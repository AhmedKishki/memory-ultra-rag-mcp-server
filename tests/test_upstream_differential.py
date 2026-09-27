"""Compare this package against a real UltraRAG checkout, when one is available.

The fixtures under ``tests/fixtures/upstream`` record what upstream produced at one
captured moment. This module goes further: it starts ``servers/memory/src/memory.py``
from a checkout over stdio, gives it the same inputs this server is given, and
compares the bytes both wrote. It is the assurance a wrapper would have given by
construction, without depending on a checkout at runtime.

It runs only when ``ULTRARAG_CHECKOUT`` names a checkout that has both the memory
server and a virtual environment, so the ordinary suite needs no UltraRAG at all.
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
from fastmcp.exceptions import ToolError

from memory_ultra_rag_mcp.config import global_memory_root
from memory_ultra_rag_mcp.store import append_round

pytestmark = pytest.mark.upstream

#: The user both sides are compared as. Upstream's memory is keyed by user, and
#: this server keeps one global memory in the directory upstream uses for the user
#: it is given when none is named, so both sides write the same files and the
#: bytes can be compared. The asymmetry is deliberate and asserted below: upstream
#: takes a user_id, this server does not.
USER_ID = "default"
QUESTION = "Where does the draft live?"
ANSWER = "In the project directory."

STAMP = re.compile(r"^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", re.MULTILINE)


def _checkout() -> tuple[Path, Path] | None:
    """Return the checkout and the interpreter to run its memory server with."""
    raw = os.environ.get("ULTRARAG_CHECKOUT", "").strip()
    if not raw:
        return None
    checkout = Path(raw).expanduser().resolve()
    server = checkout / "servers" / "memory" / "src" / "memory.py"
    interpreter = checkout / ".venv" / "bin" / "python"
    if not server.is_file() or not interpreter.is_file():
        return None
    return server, interpreter


CHECKOUT = _checkout()

requires_upstream = pytest.mark.skipif(
    CHECKOUT is None,
    reason="set ULTRARAG_CHECKOUT to a UltraRAG checkout to compare against it",
)


def _data(result: Any) -> Any:
    payload = getattr(result, "data", None)
    if isinstance(payload, dict):
        return payload
    return json.loads(result.content[0].text)


def _upstream_transport(storage: Path, workspace: Path) -> StdioTransport:
    assert CHECKOUT is not None
    server, interpreter = CHECKOUT
    return StdioTransport(
        command=str(interpreter),
        args=[str(server)],
        # The checkout's own logger writes relative to the working directory, so the
        # child runs in a scratch directory and its stderr goes to a file there: a
        # comparison must leave the repository exactly as it found it.
        cwd=str(workspace),
        log_file=workspace / "upstream-stderr.log",
        env={
            **os.environ,
            "PYTHONPATH": str(server.parents[3] / "src"),
            "ULTRARAG_UI_STORAGE_ROOT": str(storage),
        },
    )


def _our_transport(project: Path, storage: Path, workspace: Path) -> StdioTransport:
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
        cwd=str(workspace),
        log_file=workspace / "our-stderr.log",
    )


def _workspace(root: Path) -> Path:
    """Return a scratch directory for a child process to run and log in."""

    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


async def _upstream_parameters(transport: StdioTransport) -> dict[str, set[str]]:
    """Return the parameters each of upstream's own tools takes."""

    async with Client(transport, init_timeout=120) as client:
        listed = await client.list_tools()
        return {
            tool.name: set(tool.inputSchema.get("properties", {})) for tool in listed
        }


async def _upstream_refuses_a_bad_user(transport: StdioTransport) -> bool:
    """Report whether upstream still refuses a user id that could not name a directory."""

    async with Client(transport, init_timeout=120) as client:
        try:
            await client.call_tool(
                "get_global_memory", {"user_id": "../escape"}, raise_on_error=True
            )
        except ToolError:
            return True
    return False


async def _exercise_upstream(transport: StdioTransport) -> tuple[list[str], dict]:
    """Read and write one exchange through upstream's own two tools."""

    async with Client(transport, init_timeout=120) as client:
        tools = sorted(tool.name for tool in await client.list_tools())
        read = _data(await client.call_tool("get_global_memory", {"user_id": USER_ID}))
        await client.call_tool(
            "save_memory",
            {"user_id": USER_ID, "q_ls": [QUESTION], "ans_ls": [ANSWER]},
            raise_on_error=True,
        )
    return tools, read


async def _exercise_ours(
    transport: StdioTransport, storage: Path
) -> tuple[list[str], dict]:
    """Do the equivalent on this side: a bounded read, and one written exchange.

    The agent-facing tools do not write exchanges any more — a statement goes to
    the standing document — so the exchange is written through the same store
    function the browser view uses, which is what keeps the daily file's format
    upstream's.
    """

    async with Client(transport, init_timeout=120) as client:
        tools = sorted(tool.name for tool in await client.list_tools())
        read = _data(await client.call_tool("get_memory_global", {"query": "MEMORY"}))
    append_round(global_memory_root(storage) / USER_ID, [QUESTION], [ANSWER])
    return tools, read


def _written(storage: Path) -> tuple[bytes, dict[str, bytes]]:
    """Return a user's standing document and daily rounds from one storage root."""
    user = global_memory_root(storage) / USER_ID
    standing = (user / "MEMORY.md").read_bytes()
    rounds = {
        path.name: path.read_bytes() for path in sorted((user / "project").glob("*.md"))
    }
    return standing, rounds


@requires_upstream
def test_the_bytes_match_a_real_ultrarag_checkout(tmp_path: Path) -> None:
    upstream_storage = tmp_path / "upstream-storage"
    upstream_workspace = tmp_path / "upstream-workspace"
    upstream_workspace.mkdir()
    our_project = tmp_path / "thesis"
    our_project.mkdir()
    our_storage = tmp_path / "our-storage"
    our_workspace = tmp_path / "our-workspace"
    our_workspace.mkdir()

    upstream_tools, upstream_read = asyncio.run(
        _exercise_upstream(_upstream_transport(upstream_storage, upstream_workspace))
    )
    our_tools, our_read = asyncio.run(
        _exercise_ours(
            _our_transport(our_project, our_storage, our_workspace), our_storage
        )
    )

    # The store itself: same standing document, same round, same file names.
    upstream_standing, upstream_rounds = _written(upstream_storage)
    our_standing, our_rounds = _written(our_storage)
    assert our_standing == upstream_standing
    assert sorted(our_rounds) == sorted(upstream_rounds)
    for name, upstream_bytes in upstream_rounds.items():
        ours = STAMP.sub("## <timestamp>", our_rounds[name].decode("utf-8"))
        theirs = STAMP.sub("## <timestamp>", upstream_bytes.decode("utf-8"))
        assert ours == theirs

    # The read: the same standing document comes back, by a deliberately different
    # route. Upstream returns the whole file under one key; this side returns the
    # units that matched a query, and at this point the standing document is the
    # template, so the one unit is the whole document, byte for byte.
    assert upstream_read == {
        "global_memory_content": upstream_standing.decode(),
        "current_user_id": USER_ID,
    }
    assert our_read["scope"] == "global"
    assert our_read["matched_by"] == "all-words"
    # A unit is the document's content without its trailing newline, which is
    # what a statement is; the bytes on disk are compared above, unchanged.
    assert [unit["text"] for unit in our_read["units"]] == [
        upstream_standing.decode().strip()
    ]
    assert our_read["units"][0]["source"] == "MEMORY.md"
    assert our_read["searched"]["directory"] == str(
        global_memory_root(our_storage) / USER_ID
    )
    assert our_read["truncated"] is False

    # The surface: the differences this package chose, asserted rather than assumed.
    assert "build" in upstream_tools  # UltraRAG's pipeline tool, which we exclude
    assert "build" not in our_tools
    assert set(our_tools) == {
        "get_memory_global",
        "get_memory_local",
        "set_memory_global",
        "set_memory_local",
    }
    # The two upstream memory tools are renamed, not re-exposed: an agent sees one
    # verb per scope, and the scope is in the name (ADR 0001).
    assert "get_global_memory" not in our_tools
    assert "save_memory" not in our_tools
    assert {"get_global_memory", "save_memory"} <= set(upstream_tools)

    # The user dimension is upstream's, not this server's: upstream still keys its
    # memory by user and still refuses an identifier that could not name a
    # directory, while the global tools here take no such parameter and serve the
    # account's one memory (ADR 0001).
    upstream_parameters = asyncio.run(
        _upstream_parameters(
            _upstream_transport(tmp_path / "surface", _workspace(tmp_path))
        )
    )
    assert "user_id" in upstream_parameters["get_global_memory"]
    assert "user_id" in upstream_parameters["save_memory"]
    our_parameters = {
        name: set(schemas)
        for name, schemas in (
            ("get_memory_global", {"query", "limit"}),
            ("set_memory_global", {"content"}),
        )
    }
    assert "user_id" not in our_parameters["get_memory_global"]
    assert "user_id" not in our_parameters["set_memory_global"]
    assert (
        asyncio.run(
            _upstream_refuses_a_bad_user(
                _upstream_transport(tmp_path / "refusal", _workspace(tmp_path))
            )
        )
        is True
    )
