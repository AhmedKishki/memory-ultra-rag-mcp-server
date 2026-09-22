"""One stdio MCP server serving UltraRAG's own memory, global and local.

Global memory is UltraRAG's own: one ``MEMORY.md`` per user and one dated
dialogue file per day, written by the upstream server running as a child process
from a pinned checkout, so the parameters, the defaults, the file names, and the
file formats are upstream's. Local memory is the same memory scoped to a project
instead of a user, written by the same upstream code through the same two tools,
so a project's memory has the same shape as a user's and lives in the same tree.

What this package adds beyond that is entry-point work: validation of the
checkout, a storage root the process can write to, a workspace for its log, a
memory-only surface, and the two local-memory tools.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.client.client import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from mcp.types import CallToolRequestParams, ListToolsRequest, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import Field

from . import __version__
from .config import ConfigurationError, ServerConfig, resolve_config
from .instructions import SERVER_INSTRUCTIONS
from .manifest import MEMORY_SERVER_ENTRYPOINT, MEMORY_TOOLS, STORAGE_ENV_VAR
from .scopes import ScopeError, local_scope

__all__ = ["SERVER_NAME", "create_server", "main"]

SERVER_NAME = "memory-ultra-rag-mcp"

#: Tools this server registers itself, beside the two UltraRAG's server has.
LOCAL_TOOLS = ("get_local_memory", "save_local_memory")

SERVED_TOOLS = MEMORY_TOOLS + LOCAL_TOOLS

ProjectIdParameter = Annotated[
    str,
    Field(
        description=(
            "Identifier of the project whose local memory is used, so two "
            "projects keep two separate memories. Letters, digits, '_' and '-' "
            "only, at most 64 characters."
        )
    ),
]
QuestionListParameter = Annotated[
    list[str],
    Field(
        description=(
            "The user's message for this round, as a single-element list, in the "
            "shape UltraRAG's own tool takes."
        )
    ),
]
AnswerListParameter = Annotated[
    list[str],
    Field(
        description=(
            "The assistant's reply for this round, as a single-element list, in "
            "the shape UltraRAG's own tool takes."
        )
    ),
]

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, idempotentHint=True, openWorldHint=False
)
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


class MemorySurfaceOnly(Middleware):
    """Present the memory tools and nothing else.

    UltraRAG's server base class registers a pipeline ``build`` tool beside the
    tools a server defines. That tool belongs to a pipeline deployment: it has
    no pipeline to build here, so it is kept off the surface this server offers.
    """

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: Any,
    ) -> Sequence[MCPTool]:
        tools = await call_next(context)
        return [tool for tool in tools if tool.name in SERVED_TOOLS]

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: Any,
    ) -> Any:
        if context.message.name not in SERVED_TOOLS:
            raise ToolError(
                f"{context.message.name!r} is not part of this server's surface; "
                f"it serves {', '.join(SERVED_TOOLS)}"
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


async def _upstream_tool(
    transport: StdioTransport,
    name: str,
    arguments: dict[str, Any],
) -> Any:
    """Call one of UltraRAG's own memory tools.

    The client is short-lived and the transport keeps the child process alive, so
    the upstream server and its initialized state persist across calls, and the
    file formats stay the ones UltraRAG writes.
    """
    async with Client(transport) as client:
        result = await client.call_tool(name, arguments)
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    text = "".join(getattr(block, "text", "") for block in result.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def create_server(config: ServerConfig) -> FastMCP[Any]:
    """Create the server that serves global and local memory."""
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

    @server.tool(name=LOCAL_TOOLS[0], annotations=READ_ONLY_ANNOTATIONS)
    async def get_local_memory(
        project_id: ProjectIdParameter,
    ) -> dict[str, Any]:
        """Read a project's local memory.

        Local memory is one project's own standing memory, held apart from every
        user's global memory. The read returns the whole file for that project,
        and creates it from UltraRAG's template the first time the project's
        memory is read.
        """
        try:
            scope = local_scope(project_id)
        except ScopeError as error:
            raise ToolError(str(error)) from error
        payload = await _upstream_tool(
            transport, "get_global_memory", {"user_id": scope}
        )
        return {
            "project_id": project_id,
            "scope": scope,
            "local_memory_content": payload.get("global_memory_content", ""),
        }

    @server.tool(name=LOCAL_TOOLS[1], annotations=WRITE_ANNOTATIONS)
    async def save_local_memory(
        project_id: ProjectIdParameter,
        q_ls: QuestionListParameter,
        ans_ls: AnswerListParameter,
    ) -> dict[str, Any]:
        """Append one round to a project's local memory.

        The round is written with UltraRAG's own format and its own daily file,
        under the project's scope, so it appears beside the project's standing
        memory and is read by anything pointed at the same storage.
        """
        try:
            scope = local_scope(project_id)
        except ScopeError as error:
            raise ToolError(str(error)) from error
        await _upstream_tool(
            transport,
            "save_memory",
            {"user_id": scope, "q_ls": q_ls, "ans_ls": ans_ls},
        )
        return {"project_id": project_id, "scope": scope, "status": "saved"}

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
