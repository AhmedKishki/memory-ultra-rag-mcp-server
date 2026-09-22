"""One stdio MCP server over UltraRAG's own memory server, unmodified.

The upstream server is started as a child process from a pinned UltraRAG
checkout and proxied, so its two tools, their parameters, their defaults, and
their storage behaviour are the upstream ones. This package adds three things
and nothing else: validation of the checkout, a storage root the process can
actually write to, and a tool surface narrowed to the memory tools.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from mcp.types import CallToolRequestParams, ListToolsRequest
from mcp.types import Tool as MCPTool

from . import __version__
from .config import ConfigurationError, ServerConfig, resolve_config
from .instructions import SERVER_INSTRUCTIONS
from .manifest import MEMORY_SERVER_ENTRYPOINT, MEMORY_TOOLS, STORAGE_ENV_VAR

__all__ = ["SERVER_NAME", "create_server", "main"]

SERVER_NAME = "memory-ultra-rag-mcp"


class MemorySurfaceOnly(Middleware):
    """Present the memory tools and nothing else.

    UltraRAG's server base class registers a pipeline ``build`` tool beside the
    tools a server defines. That tool belongs to a pipeline deployment: it has
    no pipeline to build here, and presenting it would offer an agent a tool
    that cannot work. The narrowing is the one deliberate difference from the
    upstream surface, and it removes a tool rather than changing one.
    """

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: Any,
    ) -> Sequence[MCPTool]:
        tools = await call_next(context)
        return [tool for tool in tools if tool.name in MEMORY_TOOLS]

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: Any,
    ) -> Any:
        if context.message.name not in MEMORY_TOOLS:
            raise ToolError(
                f"{context.message.name!r} is not part of this server's surface; "
                f"it exposes {', '.join(MEMORY_TOOLS)}"
            )
        return await call_next(context)


def child_environment(config: ServerConfig) -> dict[str, str]:
    """Return the environment the upstream memory server runs in.

    ``PYTHONPATH`` points at the checkout's own ``src`` so the child imports the
    pinned UltraRAG it was started from, and the storage variable is what makes
    the memory visible to a UltraRAG UI pointed at the same root.
    """
    env = dict(os.environ)
    source_root = str(config.ultrarag_root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        os.pathsep.join((source_root, existing)) if existing else source_root
    )
    env[STORAGE_ENV_VAR] = str(config.storage_root)
    env["ULTRARAG_LOG_TS"] = f"memory_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["log_level"] = config.log_level
    return env


def create_server(config: ServerConfig) -> FastMCP[Any]:
    """Create the server that proxies the pinned UltraRAG memory server."""
    transport = StdioTransport(
        command=str(config.python_executable),
        args=[str(config.ultrarag_root / MEMORY_SERVER_ENTRYPOINT)],
        env=child_environment(config),
        cwd=str(config.workspace_root),
        keep_alive=True,
        log_file=config.workspace_root / "logs" / "memory-child-stderr.log",
    )

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {}
        finally:
            await transport.close()

    server = FastMCP(
        name=SERVER_NAME,
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        lifespan=lifespan,
    )
    # A missing upstream process must fail discovery instead of exposing an
    # incomplete server.
    server.provider_error_strategy = "raise"
    server.add_middleware(MemorySurfaceOnly())
    server.add_provider(ProxyProvider(lambda: ProxyClient(transport)))
    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description=(
            "Serve UltraRAG's own memory server over stdio: one global MEMORY.md "
            "per user and one dated dialogue file per day."
        ),
    )
    parser.add_argument(
        "--ultrarag-root",
        default=os.environ.get("ULTRARAG_ROOT"),
        help=(
            "UltraRAG checkout to serve. Must be the pinned commit and free of "
            "tracked modifications. Defaults to $ULTRARAG_ROOT."
        ),
    )
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get("MEMORY_ULTRARAG_WORKSPACE"),
        help=(
            "Directory holding this server's logs and, by default, its storage. "
            "Defaults to the per-user data directory."
        ),
    )
    parser.add_argument(
        "--storage-root",
        default=os.environ.get("ULTRARAG_UI_STORAGE_ROOT"),
        help=(
            "UltraRAG UI storage root to write memory into. Point a UltraRAG UI "
            "at the same directory to see the same memory. Defaults to "
            "<workspace-root>/ui-storage."
        ),
    )
    parser.add_argument(
        "--python-executable",
        default=None,
        help="Interpreter used for the upstream child server (default: this one).",
    )
    parser.add_argument(
        "--log-level",
        default="warn",
        choices=("debug", "info", "warn", "error"),
        help="Log level handed to the upstream child server.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the stdio server; diagnostics go to stderr, never stdout."""
    arguments = _parser().parse_args(argv)
    if not arguments.ultrarag_root:
        print(
            f"{SERVER_NAME}: --ultrarag-root is required (or set $ULTRARAG_ROOT)",
            file=sys.stderr,
        )
        return 2
    try:
        config = resolve_config(
            ultrarag_root=arguments.ultrarag_root,
            workspace_root=arguments.workspace_root,
            storage_root=arguments.storage_root,
            python_executable=arguments.python_executable,
            log_level=arguments.log_level,
        )
    except ConfigurationError as error:
        print(f"{SERVER_NAME}: {error}", file=sys.stderr)
        return 2
    create_server(config).run(transport="stdio", show_banner=False)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
