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
import asyncio
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
from .index import IndexError, fts5_available
from .instructions import SERVER_INSTRUCTIONS
from .models import DEFAULT_EMBEDDING_MODEL, LocalEmbedder, LocalReranker, ModelError
from .retrieval import Retrieval, RetrievalSettings
from .store import (
    StoreError,
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
            "What to recall, as words. The standing document's statements and the "
            "dated rounds are searched for these words, and only what matched is "
            "returned. Required: this server answers a question rather than "
            "returning a memory whole, and no tool can return a file."
        )
    ),
]
LimitParameter = Annotated[
    int,
    Field(
        description=(
            "How many matched statements and rounds to return, at most "
            f"{MAX_RESULT_LIMIT}. Raising this raises the cap on matches, never "
            "the amount of a file that is read."
        )
    ),
]

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, idempotentHint=True, openWorldHint=False
)
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


def create_server(
    config: ServerConfig,
    *,
    ui_port: int | None = None,
    retrieval: Retrieval | None = None,
    warm: bool = True,
) -> FastMCP[Any]:
    """Create the server that serves one project's memory and the global tree.

    With ``ui_port`` the same process also serves the browser view of exactly the
    memory these tools serve. Without it, nothing of this package's view module is
    imported and no port is opened.

    Retrieval is injected, which is what lets the test suite run the whole read
    and write path without a model. When it is not supplied the local models are
    used, and they are warmed on this side of the process rather than a caller's,
    because loading one costs seconds and a read may not pay it.
    """
    holder: dict[str, Any] = {}
    engine = retrieval or _local_retrieval()

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[dict[str, Any]]:
        if warm:
            # Loading a model is seconds, so it happens here, on the serving side,
            # and never in the middle of a caller's lookup. A model that cannot be
            # fetched is reported and the read falls back to words, loudly.
            report = await asyncio.to_thread(engine.warm)
            if not report.get("embedding_loaded", False):
                print(
                    f"{SERVER_NAME}: the semantic side is unavailable "
                    f"({report.get('embedding_error', 'unknown')}); "
                    "reads match words only until the model is fetched",
                    file=sys.stderr,
                )
        if ui_port is None:
            yield {}
            return
        # Imported lazily because this module is imported by the view that imports
        # it, and because a server that serves no view needs none of its code.
        from .ui import UIPort

        view = UIPort(config, port=ui_port, retrieval=engine)
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
    def set_memory_global(content: ContentParameter) -> dict[str, Any]:
        """Remember one statement that applies everywhere this account works.

        Use it for what is true of the user, and for a standing instruction they
        gave: those belong in every project, so they go here and nowhere else. The
        statement is appended to the standing document in global memory, which the
        user and the browser view can edit. There is one global memory, so there is
        nothing to name and nothing to choose.
        """
        directory = config.global_directory
        return _recorded(engine, "global", directory, content)

    @server.tool(name="set_memory_local", annotations=WRITE_ANNOTATIONS)
    def set_memory_local(content: ContentParameter) -> dict[str, Any]:
        """Remember one statement for this project only.

        Use it for what is true here — a decision, a path, a convention, where
        something lives — and not for anything the user said applies everywhere.
        The statement is appended to this project's standing document, inside the
        repository under `.memory-rag`, so it travels with the project.
        """
        return _recorded(engine, "local", config.local_directory, content)

    @server.tool(name="get_memory_global", annotations=READ_ONLY_ANNOTATIONS)
    def get_memory_global(
        query: QueryParameter,
        limit: LimitParameter = DEFAULT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        """Recall what applies everywhere this account works.

        Answer it from the global memory's standing document and the dated rounds
        that match the query. This is the account's one global memory, shared by
        every project on this storage root.
        """
        return _answer(engine, "global", config.global_directory, query, limit)

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
        return _answer(engine, "local", config.local_directory, query, limit)

    return server


def _recorded(
    engine: Retrieval,
    scope: str,
    directory: Path,
    content: str,
) -> dict[str, Any]:
    """Record one statement, reporting what the semantic side has queued for it.

    The statement is durable before this returns, and the vector is queued rather
    than computed here, so a write costs one append and no inference. A model
    that cannot be fetched leaves the unit pending, which the read reports, and
    never fails the write.
    """

    del scope
    try:
        return engine.record(directory, content)
    except ModelError as error:
        raise ToolError(str(error)) from error


def _answer(
    engine: Retrieval,
    scope: str,
    directory: Path,
    query: str,
    limit: int,
) -> dict[str, Any]:
    """Answer one read, and refuse the one question a read must not answer.

    An empty query is refused rather than answered with everything, because a
    memory grows by appending and handing one to a model whole spends the context
    window on the file. The answer also reports when a file was edited behind our
    back, which is what ``memory-ultra-rag-reindex`` is for.
    """

    if not str(query or "").strip():
        raise ToolError(
            "query must not be empty: this server answers a question about a "
            "memory rather than returning one whole, because a memory grows by "
            "appending and reading it all would spend the context window on it"
        )
    capped = max(1, min(int(limit), MAX_RESULT_LIMIT))
    try:
        return engine.answer(
            scope=scope, directory=directory, query=str(query), limit=capped
        )
    except (IndexError, StoreError) as error:
        raise ToolError(str(error)) from error


def _local_retrieval(model: str = DEFAULT_EMBEDDING_MODEL) -> Retrieval:
    """Build this process's retrieval from the local models."""

    embedder = LocalEmbedder(model)
    policy = RetrievalSettings(embedding_model=embedder.identity.name)
    reranker = LocalReranker(policy.reranker_model) if policy.reranker_model else None
    return Retrieval(embedder=embedder, policy=policy, reranker=reranker)


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
