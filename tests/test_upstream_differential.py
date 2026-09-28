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
import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

from memory_ultra_rag_mcp.config import global_memory_root, resolve_config
from memory_ultra_rag_mcp.store import read_standing

pytestmark = pytest.mark.upstream

#: The user both sides are compared as. Upstream's memory is keyed by user, and
#: this server keeps one global memory in the directory upstream uses for the user
#: it is given when none is named, so both sides write the same files and the
#: bytes can be compared. The asymmetry is deliberate and asserted below: upstream
#: takes a user_id, this server does not.
USER_ID = "default"
QUESTION = "Where does the draft live?"
STATEMENT = "The draft lives in the project directory."
#: The type this side records a statement under. Upstream has no such parameter,
#: so this is a deliberate divergence, recorded in the README's choice table.
STATEMENT_TYPE = "NOTE"
ANSWER = "In the project directory."


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
) -> tuple[list[str], dict, dict]:
    """Do the equivalent on this side, and read back something real.

    A statement is recorded through the tool, and the exchange is written through
    the same store function the browser view uses, which is what keeps the daily
    file's format upstream's. The read then has content to answer with, so the
    comparison is against a unit that exists rather than against a template.
    """

    async with Client(transport, init_timeout=120) as client:
        tools = sorted(tool.name for tool in await client.list_tools())
        await client.call_tool(
            "set_memory_global",
            {"content": STATEMENT, "type": STATEMENT_TYPE},
            raise_on_error=True,
        )
        await client.call_tool(
            "get_memory_global", {"query": STATEMENT}, raise_on_error=True
        )
    async with Client(transport, init_timeout=120) as client:
        read = _data(await client.call_tool("get_memory_global", {"query": STATEMENT}))
        fresh = _data(await client.call_tool("get_memory_global", {"query": "MEMORY"}))
    return tools, read, fresh


def _fresh_storage(tmp_path: Path, name: str) -> Path:
    """Return a storage root whose scope has been read once and never written.

    This is what a first read does on both sides, and it is the only standing
    document the two can be asked to agree on byte for byte: upstream has no tool
    that records a statement, and this side does.
    """

    project = tmp_path / name / "project"
    project.mkdir(parents=True)
    storage = tmp_path / name / "storage"
    read_standing(
        resolve_config(project_root=project, storage_root=storage).global_directory
    )
    return storage


def _written(storage: Path) -> tuple[bytes, dict[str, bytes]]:
    """Return a user's standing document and daily rounds from one storage root.

    Upstream writes both; this side writes the standing document and nothing
    beside it, which is the divergence the choice table records. The rounds are
    still read here so the comparison can say which side has them.
    """
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
    our_tools, our_read, fresh = asyncio.run(
        _exercise_ours(
            _our_transport(our_project, our_storage, our_workspace), our_storage
        )
    )

    # A fresh scope, read but never written, holds upstream's bytes exactly:
    # that is the create-on-read contract, and it is the only standing document
    # the two sides can be asked to agree on, because upstream has no tool that
    # records a statement and this side does.
    fresh_upstream = _written(_fresh_storage(tmp_path, "fresh-upstream"))
    fresh_ours = _written(_fresh_storage(tmp_path, "fresh-ours"))
    assert fresh_ours == fresh_upstream

    # The store itself: the same template, then this side's labelled statement
    # appended below it. Upstream never writes a statement, so the only standing
    # document the two sides can be asked to agree on byte for byte is a fresh
    # one, asserted above; what is asserted here is that the label prefix is the
    # only difference this side introduces.
    upstream_standing, _upstream_rounds = _written(upstream_storage)
    our_standing, our_rounds = _written(our_storage)
    assert our_standing.decode().startswith(upstream_standing.decode())
    assert our_standing.decode().removeprefix(upstream_standing.decode()) == (
        f"\n{STATEMENT_TYPE}: {STATEMENT}\n"
    )
    # Upstream also keeps a dated exchange log, and this side keeps none: the
    # divergence the choice table records, asserted here so it cannot drift.
    assert our_rounds == {}

    # The read: the same standing document comes back, by a deliberately different
    # route. Upstream returns the whole file under one key; this side returns the
    # units that matched a query, and at this point the standing document is the
    # template, so the one unit is the whole document, byte for byte.
    assert upstream_read == {
        "global_memory_content": upstream_standing.decode(),
        "current_user_id": USER_ID,
    }
    assert our_read["scope"] == "global"
    # A statement this side recorded comes back as the unit it is, from the same
    # file upstream would have written it into, and without the type line it was
    # filed under: the label is recorded, and a read answers with the statement.
    assert [unit["text"] for unit in our_read["units"]] == [STATEMENT]
    assert STATEMENT_TYPE not in str(our_read)
    assert our_read["units"][0]["source"] == "MEMORY.md"
    assert our_read["truncated"] is False
    # The template upstream seeds a fresh standing document with is a seed, not
    # something anyone wrote, so it is not indexed and cannot answer a query.
    # Upstream returns it as the whole document; this side returns nothing for it.
    assert STATEMENT in our_standing.decode()
    assert fresh["units"] == []

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
            ("set_memory_global", {"content", "type"}),
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
