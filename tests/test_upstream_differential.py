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

from memory_ultra_rag_mcp.config import global_memory_root

pytestmark = pytest.mark.upstream

USER_ID = "comparison"
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


def _upstream_transport(storage: Path) -> StdioTransport:
    assert CHECKOUT is not None
    server, interpreter = CHECKOUT
    return StdioTransport(
        command=str(interpreter),
        args=[str(server)],
        env={
            **os.environ,
            "PYTHONPATH": str(server.parents[3] / "src"),
            "ULTRARAG_UI_STORAGE_ROOT": str(storage),
        },
    )


def _our_transport(project: Path, storage: Path) -> StdioTransport:
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


async def _exercise(transport: StdioTransport) -> tuple[list[str], dict, dict]:
    """Read and write one round through a transport, and report what happened."""
    async with Client(transport, init_timeout=120) as client:
        tools = sorted(tool.name for tool in await client.list_tools())
        read = _data(await client.call_tool("get_global_memory", {"user_id": USER_ID}))
        await client.call_tool(
            "save_memory",
            {"user_id": USER_ID, "q_ls": [QUESTION], "ans_ls": [ANSWER]},
            raise_on_error=True,
        )
    return tools, read, {}


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
    our_project = tmp_path / "thesis"
    our_project.mkdir()
    our_storage = tmp_path / "our-storage"

    upstream_tools, upstream_read, _ = asyncio.run(
        _exercise(_upstream_transport(upstream_storage))
    )
    our_tools, our_read, _ = asyncio.run(
        _exercise(_our_transport(our_project, our_storage))
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

    # The read: same keys, same content, same user id echoed back.
    assert upstream_read == {
        "global_memory_content": upstream_standing.decode(),
        "current_user_id": USER_ID,
    }
    assert our_read == upstream_read

    # The surface: the differences this package chose, asserted rather than assumed.
    assert "build" in upstream_tools  # UltraRAG's pipeline tool, which we exclude
    assert "build" not in our_tools
    assert {"get_global_memory", "save_memory"} <= set(our_tools)
    assert set(our_tools) == {
        "get_global_memory",
        "get_local_memory",
        "save_local_memory",
        "save_memory",
    }
