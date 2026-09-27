"""One stdio MCP server serving UltraRAG's memory, global and local.

Both kinds work the same way and have the same shape: a standing document plus
one dated dialogue file per day, written in UltraRAG's own format. They differ in
where they live and therefore who can see them.

* **global memory** belongs to a user and lives in UltraRAG's UI storage tree, so
  every instance serving that tree reads the same memory.
* **local memory** belongs to the project this server is bound to and lives inside
  that repository, under ``.memory-rag``, so it travels with the project.

The surface is four tools, named by what they do and which kind they touch, so the
only decision an agent makes is whether a memory is this project's or this user's.
No tool returns a whole memory: a read takes a query, answers it from the standing
document and the dated rounds, and reports what it searched, because a memory that
grows by appending cannot be handed to a model whole without spending the context
window on it.
"""

from __future__ import annotations

import argparse
import os
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
from .config import (
    PROJECT_ROOT_ENV_VAR,
    STORAGE_ENV_VAR,
    ULTRARAG_STORAGE_ENV_VAR,
    ConfigurationError,
    ServerConfig,
    resolve_config,
)
from .index import IndexError, MemoryIndex, fts5_available
from .instructions import SERVER_INSTRUCTIONS
from .store import (
    StoreError,
    append_statement,
    daily_rounds,
    read_standing,
    standing_document,
    user_id_error,
)

__all__ = ["SERVER_NAME", "create_server", "main"]

SERVER_NAME = "memory-ultra-rag-mcp"

#: The environment variable that turns the browser view on for every start.
UI_PORT_ENV_VAR = "MEMORY_ULTRARAG_UI_PORT"

#: How many dated rounds one read may return. A memory grows by appending, so a read
#: is capped and says so; the cap is a parameter so a caller that needs more can
#: ask for more deliberately.
DEFAULT_RESULT_LIMIT = 10
MAX_RESULT_LIMIT = 50

UserIdParameter = Annotated[
    str,
    Field(
        description=(
            "Whose global memory is used, so two users keep two memories. "
            "Letters, digits, '_' and '-' only; 'default' when no user is named."
        )
    ),
]
ContentParameter = Annotated[
    str,
    Field(
        description=(
            "The one statement to remember, in the user's own words where it is "
            "theirs. Plain text: a line or a short paragraph, not a question and "
            "an answer."
        )
    ),
]
QueryParameter = Annotated[
    str,
    Field(
        description=(
            "What to recall, as words. The standing document and the dated rounds "
            "are searched for these words, and the rounds that match come back "
            "newest first. Required: this server answers a question rather than "
            "returning a memory whole."
        )
    ),
]
LimitParameter = Annotated[
    int,
    Field(
        description=(
            "How many dated rounds to return, at most "
            f"{MAX_RESULT_LIMIT}. The standing document is always returned."
        )
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
    memory these tools serve. Without it, nothing of this package's view module is
    imported and no port is opened.
    """
    holder: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[dict[str, Any]]:
        if ui_port is None:
            yield {}
            return
        # Imported lazily because this module is imported by the view that imports
        # it, and because a server that serves no view needs none of its code.
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

    @server.tool(name="set_memory_global", annotations=WRITE_ANNOTATIONS)
    def set_memory_global(
        content: ContentParameter,
        user_id: UserIdParameter = "default",
    ) -> dict[str, Any]:
        """Remember one statement for this user, everywhere they work.

        Use it for what is true of the user, and for a standing instruction they
        gave: those belong in every project, so they go here and nowhere else. The
        statement is appended to that user's standing document in their global
        memory, which every read returns and which the user and the browser view
        can edit.
        """
        directory = _global_directory(config, user_id)
        try:
            digest = append_statement(directory, content)
        except StoreError as error:
            raise ToolError(str(error)) from error
        return _recorded(directory, digest)

    @server.tool(name="set_memory_local", annotations=WRITE_ANNOTATIONS)
    def set_memory_local(content: ContentParameter) -> dict[str, Any]:
        """Remember one statement for this project only.

        Use it for what is true here — a decision, a path, a convention, where
        something lives — and not for anything the user said applies everywhere.
        The statement is appended to this project's standing document, inside the
        repository under `.memory-rag`, so it travels with the project.
        """
        try:
            digest = append_statement(config.local_directory, content)
        except StoreError as error:
            raise ToolError(str(error)) from error
        return _recorded(config.local_directory, digest)

    @server.tool(name="get_memory_global", annotations=READ_ONLY_ANNOTATIONS)
    def get_memory_global(
        query: QueryParameter,
        user_id: UserIdParameter = "default",
        limit: LimitParameter = DEFAULT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        """Recall what this user wants remembered, everywhere they work.

        Answer it from the user's standing document, which is returned whole
        because it is small and curated, plus the dated rounds that match the
        query, newest first. A user_id is optional and defaults to "default".
        """
        directory = _global_directory(config, user_id)
        return _recalled("global", directory, query, limit)

    @server.tool(name="get_memory_local", annotations=READ_ONLY_ANNOTATIONS)
    def get_memory_local(
        query: QueryParameter,
        limit: LimitParameter = DEFAULT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        """Recall what is true about this project.

        Answer it from this project's standing document, which is returned whole
        because it is small and curated, plus the dated rounds that match the
        query, newest first. Nothing outside this repository is read, and no other
        project's memory is reachable from here.
        """
        return _recalled("local", config.local_directory, query, limit)

    return server


def _recalled(
    scope: str,
    directory: Path,
    query: str,
    limit: int,
) -> dict[str, Any]:
    """Answer one read from a scope, and report what was searched.

    Nothing here returns a memory. The answer is the units that matched — a
    statement from the standing document, or a dated round — and nothing else, so
    the cost of a read is a lookup and a page of rows rather than a pass over
    every file the memory holds. The index is derived from those files, so a
    memory edited by hand is read correctly; the report says which file each unit
    came from so a caller can go to the original.
    """

    if not str(query or "").strip():
        raise ToolError(
            "query must not be empty: this server answers a question about a "
            "memory rather than returning one whole, because a memory grows by "
            "appending and reading it all would spend the context window on it"
        )
    capped = max(1, min(int(limit), MAX_RESULT_LIMIT))

    # Upstream creates the scope's standing document from its template on the
    # first read, and that is how a scope comes into being. The index does not
    # need the file, but the contract does, so it happens before the search.
    read_standing(directory)

    with MemoryIndex(directory) as index:
        try:
            found, mode = index.search(query, capped)
        except IndexError as error:
            raise ToolError(str(error)) from error
        state = index.sync()
        index_path = index.path

    # One row past the limit is how the index says there was more to come.
    truncated = len(found) > capped
    units = [
        {
            "kind": unit["kind"],
            "text": unit["text"],
            "source": unit["source"],
            "stamp": unit["stamp"],
        }
        for unit in found[:capped]
    ]
    return {
        "scope": scope,
        "query": str(query),
        "matched_by": mode,
        "units": units,
        "returned": len(units),
        "truncated": truncated,
        "searched": {
            "directory": str(directory),
            "index": str(index_path),
            "units_indexed": state["units"],
            "files_indexed": state["files"],
        },
    }


def _recorded(scope_directory: Path, digest: str) -> dict[str, Any]:
    """Describe one recorded statement the same way for both kinds of memory."""
    return {
        "status": "set",
        "directory": str(scope_directory),
        "standing_document": str(standing_document(scope_directory)),
        "sha256": digest,
        "rounds_directory": str(daily_rounds(scope_directory)),
    }


def _ui_port_from_environment() -> int | None:
    """Return the browser-view port the environment asks for, if any."""
    raw = os.environ.get(UI_PORT_ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(
            f"{SERVER_NAME}: {UI_PORT_ENV_VAR} must be an integer between 1 and 65535",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


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
        default=os.environ.get(PROJECT_ROOT_ENV_VAR),
        help=(
            "Repository whose local memory is served. Local memory is kept in "
            f"<project-root>/.memory-rag. Required, or {PROJECT_ROOT_ENV_VAR}."
        ),
    )
    parser.add_argument(
        "--storage-root",
        default=None,
        help=(
            "Where every user's global memory lives, as a normal directory. "
            f"Defaults to ${STORAGE_ENV_VAR}, then ${ULTRARAG_STORAGE_ENV_VAR}, "
            "then this account's home data directory."
        ),
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=_ui_port_from_environment(),
        help=(
            "Also serve a local browser view of this memory on this loopback port; "
            "the view is reported on stderr and stops with this server. Defaults to "
            f"${UI_PORT_ENV_VAR} when set."
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
        )
    except ConfigurationError as error:
        print(f"{SERVER_NAME}: {error}", file=sys.stderr)
        return 2
    if arguments.ui_port is not None and not 1 <= arguments.ui_port <= 65535:
        print(f"{SERVER_NAME}: --ui-port must be between 1 and 65535", file=sys.stderr)
        return 2
    if not fts5_available():
        print(
            f"{SERVER_NAME}: this Python's SQLite was built without FTS5, which "
            "the search index needs. A read would otherwise have to pass over "
            "every file a memory holds. Run scripts/check_sqlite_fts5.py for the "
            "detail, and use a Python whose sqlite3 module includes FTS5",
            file=sys.stderr,
        )
        return 2
    create_server(config, ui_port=arguments.ui_port).run(
        transport="stdio", show_banner=False
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
