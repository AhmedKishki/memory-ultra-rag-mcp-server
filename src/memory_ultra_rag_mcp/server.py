"""One stdio MCP server serving UltraRAG's memory, global and local.

Both kinds work the same way and have the same shape: one standing document,
``MEMORY.md``, of statements in UltraRAG's own format. They differ in where they
live and therefore who can see them.

* **global memory** belongs to a user and lives in UltraRAG's UI storage tree, so
  every instance serving that tree reads the same memory.
* **local memory** belongs to the project this server is bound to and lives inside
  that repository, under ``.memory-rag``, so it travels with the project.

The surface is seven tools, named by what they do and which kind of memory they
touch: **set** records one statement, **get** answers a question about what was
remembered, **forget** removes one statement, and **set_memory_handoff** replaces
this project's previous handoff. The only decision an agent makes is which kind of
memory it is writing — this project's or the account's — because that is the one
thing that decides who can read it.

No tool returns a whole memory. A read takes a query and answers it from the
statements that matched, because a memory grows and handing one to a model whole
would spend the context window on the file instead of on the work.
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
    DEFAULT_KIND,
    StoreError,
)

__all__ = ["SERVER_NAME", "create_server", "main"]

SERVER_NAME = "memory-ultra-rag-mcp"

#: The environment variable that turns the browser view on for every start.
UI_PORT_ENV_VAR = "MEMORY_ULTRARAG_UI_PORT"

#: How many statements one read may return. A memory grows by appending, so a read
#: is capped and says so; the cap is a parameter so a caller that needs more can
#: ask for more deliberately.
DEFAULT_RESULT_LIMIT = 10
MAX_RESULT_LIMIT = 50

ContentParameter = Annotated[
    str,
    Field(
        description=(
            "The one statement to remember, in the user's own words where it is "
            "theirs. A line or a short paragraph."
        )
    ),
]
KindParameter = Annotated[
    str,
    Field(
        default=DEFAULT_KIND,
        description=(
            "The kind to file it under: one word in block letters, no white "
            "space. RULE for an instruction or a standing fact, PLAN for what the "
            "project is meant to be or achieve, PREFERENCE for what the user "
            "likes, CORRECTION for something to stop doing, and any other word if "
            "the statement fits one better. The same word for the same kind of "
            "thing, because a read filtered by kind returns everything filed under "
            "it. If nothing fits, ITEM: it is a kind like any other and a "
            "statement filed under it is found by asking for it. Case is recorded "
            "in block letters. Nothing here interprets the word."
        ),
    ),
]
ForgetParameter = Annotated[
    str,
    Field(
        description=(
            "The exact `text` of the one statement to remove, as a read returned "
            "it: not a summary, not a fragment, and not the kind. The match is on "
            "those words and never on meaning, so it removes that statement or "
            "nothing."
        )
    ),
]
HandoffParameter = Annotated[
    str,
    Field(
        description=(
            "This session's handoff, in a few sentences: what is done, what is in "
            "flight, and what the next session does first. It replaces the previous "
            "handoff in this project."
        )
    ),
]
QueryParameter = Annotated[
    str,
    Field(
        description=(
            "What to recall, as words. Answered from the statements that match, by "
            "their words and by their meaning. Required: this server answers a "
            "question rather than returning a memory whole."
        )
    ),
]
KindFilterParameter = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Return only the statements filed under this kind, one word in block "
            "letters. A kind is a column of the record and not part of a "
            "statement's words, so it is asked for here rather than searched for."
        ),
    ),
]
LimitParameter = Annotated[
    int,
    Field(
        description=(
            f"How many matched statements to return, at most {MAX_RESULT_LIMIT}. "
            "This caps matches, not the size of a file read."
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
    def set_memory_global(
        content: ContentParameter, kind: KindParameter = DEFAULT_KIND
    ) -> dict[str, Any]:
        """Records one statement that applies everywhere on this account.

        What is true of the user, and an instruction they gave once and mean
        everywhere, go here. It goes to the top of the account's memory, and every
        project on this account can read it.
        """
        return _recorded(engine, "global", config.global_directory, content, kind)

    @server.tool(name="set_memory_local", annotations=WRITE_ANNOTATIONS)
    def set_memory_local(
        content: ContentParameter, kind: KindParameter = DEFAULT_KIND
    ) -> dict[str, Any]:
        """Records one statement about this project.

        A decision, a path, a convention, or anything else that is true here and
        would be wrong in another project goes here. It goes to the top of this
        project's memory, inside the repository under `.memory-rag`, and travels
        with the project.
        """
        return _recorded(engine, "local", config.local_directory, content, kind)

    @server.tool(name="get_memory_global", annotations=READ_ONLY_ANNOTATIONS)
    def get_memory_global(
        query: QueryParameter,
        limit: LimitParameter = DEFAULT_RESULT_LIMIT,
        kind: KindFilterParameter = None,
    ) -> dict[str, Any]:
        """Returns the account's statements that match a query.

        It matches by words and by meaning, and returns the statements themselves
        rather than an answer to a question about them. Each one comes with its
        kind, when it was added, and how often it has been recalled.
        """
        return _answer(
            engine,
            "global",
            config.global_directory,
            query,
            limit,
            kind,
        )

    @server.tool(name="get_memory_local", annotations=READ_ONLY_ANNOTATIONS)
    def get_memory_local(
        query: QueryParameter,
        limit: LimitParameter = DEFAULT_RESULT_LIMIT,
        kind: KindFilterParameter = None,
    ) -> dict[str, Any]:
        """Returns this project's statements that match a query.

        It matches by words and by meaning, and returns the statements themselves
        rather than an answer to a question about them. Each one comes with its
        kind, when it was added, and how often it has been recalled. No other
        project's memory is reachable from here.
        """
        return _answer(
            engine,
            "local",
            config.local_directory,
            query,
            limit,
            kind,
        )

    @server.tool(name="forget_memory_global", annotations=WRITE_ANNOTATIONS)
    def forget_memory_global(text: ForgetParameter) -> dict[str, Any]:
        """Removes one statement from the account's memory.

        Pass the exact `text` a read returned. The match is on those words and
        never on meaning, so it removes that statement or nothing; if the text
        matches more than one statement, nothing is removed and the answer names
        them.
        """
        return _forgotten(engine, config.global_directory, text)

    @server.tool(name="forget_memory_local", annotations=WRITE_ANNOTATIONS)
    def forget_memory_local(text: ForgetParameter) -> dict[str, Any]:
        """Removes one statement from this project's memory.

        Pass the exact `text` a read returned. The match is on those words and
        never on meaning, so it removes that statement or nothing; if the text
        matches more than one statement, nothing is removed and the answer names
        them.
        """
        return _forgotten(engine, config.local_directory, text)

    @server.tool(name="set_memory_handoff", annotations=WRITE_ANNOTATIONS)
    def set_memory_handoff(content: HandoffParameter) -> dict[str, Any]:
        """Records this session's handoff, replacing the previous one.

        One statement at the top of this project's memory, under the kind
        `HANDOFF`. The previous handoff is removed as part of the same call, so a
        project holds one rather than a list, and `replaced` in the answer is how
        many went. Write what the next session needs to pick the work up: what is
        done, what is in flight, and what to do first.
        """
        return _handed_off(engine, config.local_directory, content)

    return server


def _recorded(
    engine: Retrieval,
    scope: str,
    directory: Path,
    content: str,
    kind: str,
) -> dict[str, Any]:
    """Record one typed statement, reporting what the semantic side has queued.

    The statement is durable before this returns, and the vector is queued rather
    than computed here, so a write costs one append and no inference. A model
    that cannot be fetched leaves the unit pending, which the read reports, and
    never fails the write.
    """

    del scope
    try:
        return engine.record(directory, content, kind)
    except ModelError as error:
        raise ToolError(str(error)) from error


def _forgotten(engine: Retrieval, directory: Path, text: str) -> dict[str, Any]:
    """Remove the one statement this text is exactly, or refuse to remove any."""

    try:
        return engine.forget(directory, text)
    except ModelError as error:
        raise ToolError(str(error)) from error


def _handed_off(engine: Retrieval, directory: Path, content: str) -> dict[str, Any]:
    """Put this session's handoff on top, and report the one it replaced."""

    try:
        return engine.handoff(directory, content)
    except ModelError as error:
        raise ToolError(str(error)) from error


def _answer(
    engine: Retrieval,
    scope: str,
    directory: Path,
    query: str,
    limit: int,
    kind: str | None = None,
) -> dict[str, Any]:
    """Answer one read, and refuse the one question a read must not answer.

    An empty query is refused rather than answered with everything, because a
    memory grows and handing one to a model whole spends the context window on it.
    The answer also reports when the document was rewritten from the record, which
    is how a hand edit to an export is disclosed rather than quietly lost.
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
            scope=scope,
            directory=directory,
            query=str(query),
            limit=capped,
            kind=kind,
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
