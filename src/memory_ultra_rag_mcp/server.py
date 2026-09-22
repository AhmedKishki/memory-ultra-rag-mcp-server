"""One stdio MCP server serving UltraRAG's memory, global and local.

Both kinds work the same way and have the same shape: a standing document plus
one dated dialogue file per day, written in UltraRAG's own format. They differ in
where they live and therefore who can see them.

* **global memory** belongs to a user and lives in UltraRAG's UI storage tree, so
  every instance serving that tree reads the same memory.
* **local memory** belongs to the project this server is bound to and lives inside
  that repository, under ``.memory-rag``, so it travels with the project.

The agent chooses which one a write goes to; the two tools of each kind take the
same arguments, and the project is bound at startup rather than named per call.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .config import ConfigurationError, ServerConfig, resolve_config
from .instructions import SERVER_INSTRUCTIONS
from .store import (
    StoreError,
    append_round,
    daily_rounds,
    read_standing,
    standing_document,
    user_id_error,
)

__all__ = ["SERVER_NAME", "create_server", "main"]

SERVER_NAME = "memory-ultra-rag-mcp"

UserIdParameter = Annotated[
    str,
    Field(
        description=(
            "Whose global memory is used, so two users keep two memories. "
            "Letters, digits, '_' and '-' only; 'default' when no user is named."
        )
    ),
]
QuestionListParameter = Annotated[
    list[str],
    Field(description="The user's message for this round, as a single-element list."),
]
AnswerListParameter = Annotated[
    list[str],
    Field(
        description="The assistant's reply for this round, as a single-element list."
    ),
]

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, idempotentHint=True, openWorldHint=False
)
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


def _global_directory(config: ServerConfig, user_id: str) -> Path:
    """Return one user's global memory directory, or refuse the identifier."""
    problem = user_id_error(user_id)
    if problem is not None:
        raise ToolError(problem)
    normalized = str(user_id or "default").strip() or "default"
    return config.global_root / normalized


def create_server(
    config: ServerConfig,
    *,
    ui_port: int | None = None,
) -> FastMCP[Any]:
    """Create the server that serves one project's memory and the global tree.

    With ``ui_port`` the same process also serves the browser view of exactly the
    memory these tools serve; without it nothing of the browser stack is loaded.
    """
    holder: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[dict[str, Any]]:
        if ui_port is None:
            yield {}
            return
        # Imported lazily so a server that serves no view never loads uvicorn and
        # starlette, and so this module does not import the view that imports it.
        from .ui import UIPort

        view = UIPort(config, port=ui_port)
        await view.start()
        holder["view"] = view
        if view.error is not None:
            print(f"{SERVER_NAME}: {view.error}", file=sys.stderr)
        else:
            print(f"{SERVER_NAME}: browser view at {view.url}", file=sys.stderr)
        try:
            yield {}
        finally:
            active = holder.pop("view", None)
            if active is not None:
                await active.stop()

    server = FastMCP(
        name=SERVER_NAME,
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        lifespan=lifespan,
    )

    @server.tool(name="get_global_memory", annotations=READ_ONLY_ANNOTATIONS)
    def get_global_memory(user_id: UserIdParameter = "default") -> dict[str, Any]:
        """Read a user's global memory.

        Global memory holds what a user wants remembered everywhere, and every
        instance pointed at the same storage root reads it. The read returns the
        whole standing document and creates it from UltraRAG's template the first
        time that user's memory is read.
        """
        directory = _global_directory(config, user_id)
        return {
            "global_memory_content": read_standing(directory),
            "current_user_id": directory.name,
        }

    @server.tool(name="save_memory", annotations=WRITE_ANNOTATIONS)
    def save_memory(
        user_id: UserIdParameter,
        q_ls: QuestionListParameter,
        ans_ls: AnswerListParameter,
    ) -> dict[str, Any]:
        """Append one round to a user's global memory.

        The round is written to that user's daily file with UltraRAG's own format:
        a dated heading, the user's line, and the assistant's line.
        """
        directory = _global_directory(config, user_id)
        try:
            written = append_round(directory, q_ls, ans_ls)
        except StoreError as error:
            raise ToolError(str(error)) from error
        return _saved(directory, written)

    @server.tool(name="get_local_memory", annotations=READ_ONLY_ANNOTATIONS)
    def get_local_memory() -> dict[str, Any]:
        """Read this project's local memory.

        Local memory belongs to the repository this server is bound to and lives
        inside it, under `.memory-rag`, so it stays with the project and no other
        project reads it. The read returns the whole standing document and creates
        it from UltraRAG's template the first time the project's memory is read.
        """
        return {
            "local_memory_content": read_standing(config.local_directory),
            "directory": str(config.local_directory),
        }

    @server.tool(name="save_local_memory", annotations=WRITE_ANNOTATIONS)
    def save_local_memory(
        q_ls: QuestionListParameter,
        ans_ls: AnswerListParameter,
    ) -> dict[str, Any]:
        """Append one round to this project's local memory.

        The round is written inside the project with UltraRAG's own format, so a
        project's memory has the same shape as a user's global memory and can be
        read from the project directory alone.
        """
        try:
            written = append_round(config.local_directory, q_ls, ans_ls)
        except StoreError as error:
            raise ToolError(str(error)) from error
        return _saved(config.local_directory, written)

    return server


def _saved(scope_directory: Path, written: Path) -> dict[str, Any]:
    """Describe one appended round the same way for both kinds of memory."""
    return {
        "status": "saved",
        "directory": str(scope_directory),
        "standing_document": str(standing_document(scope_directory)),
        "rounds_directory": str(daily_rounds(scope_directory)),
        "written": str(written),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description=(
            "Serve UltraRAG's memory over stdio: a project's own memory inside "
            "the repository, and the user's global memory in the shared tree."
        ),
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help=(
            "Repository whose local memory is served. Local memory is kept in "
            "<project-root>/.memory-rag. Required."
        ),
    )
    parser.add_argument(
        "--storage-root",
        default=None,
        help=(
            "UltraRAG UI storage tree holding every user's global memory. Defaults "
            "to $ULTRARAG_UI_STORAGE_ROOT, then <workspace-root>/ui-storage."
        ),
    )
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Directory for this server's own files (default: the user data dir).",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=None,
        help=(
            "Also serve a local browser view of this memory on this loopback port; "
            "the view is reported on stderr and stops with this server."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the stdio server; diagnostics go to stderr, never stdout."""
    arguments = _parser().parse_args(argv)
    if not arguments.project_root:
        print(
            f"{SERVER_NAME}: --project-root is required; point it at the "
            "repository whose memory is served",
            file=sys.stderr,
        )
        return 2
    try:
        config = resolve_config(
            project_root=arguments.project_root,
            storage_root=arguments.storage_root,
            workspace_root=arguments.workspace_root,
        )
    except ConfigurationError as error:
        print(f"{SERVER_NAME}: {error}", file=sys.stderr)
        return 2
    if arguments.ui_port is not None and not 1 <= arguments.ui_port <= 65535:
        print(f"{SERVER_NAME}: --ui-port must be between 1 and 65535", file=sys.stderr)
        return 2
    create_server(config, ui_port=arguments.ui_port).run(
        transport="stdio", show_banner=False
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
