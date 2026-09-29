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

#: The two memories. There is one tool for each verb and a `scope` on the two that
#: can be told apart, so an agent chooses what to remember and where it goes, and
#: never has to pick between two tools that differ only by a word in their name.
LOCAL_SCOPE = "local"
GLOBAL_SCOPE = "global"

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
            "The kind to file it under, chosen from what the statement means. "
            "RULE for an instruction or a standing fact, PLAN for what the project "
            "is meant to be or achieve, PREFERENCE for what the user likes, "
            "CORRECTION for something to stop doing, and any other word in block "
            "letters if the statement fits one better. The same word for the same "
            "kind of thing, because a recall filtered by kind returns everything "
            "filed under it. If nothing fits, ITEM: it is a kind like any other, "
            "and a statement filed under it is found by asking for it. Nothing "
            "here interprets the word."
        ),
    ),
]
ScopeParameter = Annotated[
    str,
    Field(
        default=LOCAL_SCOPE,
        description=(
            'Where the statement belongs. "local" is this project, inside the '
            'repository, and is the default. "global" is across projects: the '
            "account's memory, shared by every project on this machine. Read it "
            "off what the user means and how far it reaches rather than off the "
            "wording, because most prompts carry no marker of where a statement "
            "should go."
        ),
    ),
]
ForgetParameter = Annotated[
    str,
    Field(
        description=(
            "The exact `text` of the one statement to remove, as a recall "
            "returned it: not a summary, not a fragment, and not the kind. The "
            "match is on those words and never on meaning, so it removes that "
            "statement or nothing."
        )
    ),
]
HandoffParameter = Annotated[
    str,
    Field(
        description=(
            "This session's handoff, in a few sentences: what is done, what is in "
            "flight, and what the next session does first. It replaces the previous "
            "handoff in this project, which is a project's own state."
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
            "letters. A kind is a column of the record rather than part of a "
            "statement's words, so it is asked for here rather than searched for."
        ),
    ),
]
LimitParameter = Annotated[
    int,
    Field(
        description=(
            f"How many statements to return across both memories, at most {MAX_RESULT_LIMIT}. "
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

    @server.tool(name="record_memory", annotations=WRITE_ANNOTATIONS)
    def record_memory(
        content: ContentParameter,
        kind: KindParameter = DEFAULT_KIND,
        scope: ScopeParameter = LOCAL_SCOPE,
    ) -> dict[str, Any]:
        """Records one statement in this account's or this project's memory.

        Call it for something meant to hold beyond this reply: a rule, a
        principle, a decision, a correction, a preference, a path. Not for a
        request that is finished when it is answered.

        Recall first, with words covering the same thing. If a statement comes
        back that says the same, record nothing. If one comes back that
        contradicts it, forget that one first.

        `scope` decides where it goes. "local" is this project, inside the
        repository. "global" is across projects, in the account's memory. Choose
        it from what the user means and how far it reaches — a fact about this
        code, a path, a convention here is local; a preference about how they
        want to be spoken to, or a rule about their own work rather than this
        repository, is global. Prompts rarely say so, so judge the substance and
        not the wording.
        """
        return _recorded(engine, content, kind, scope, config)

    @server.tool(name="recall_memory", annotations=READ_ONLY_ANNOTATIONS)
    def recall_memory(
        query: QueryParameter,
        kind: KindFilterParameter = None,
        limit: LimitParameter = DEFAULT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        """Returns what is remembered that matches these words.

        It searches this project's memory and the account's memory together and
        ranks the results by how well they match, so the best statement wins
        whichever memory it is in. Each one names its scope.

        Give it a few words, not a sentence. When nothing comes back, that is
        what the search found: try other words, or pass a kind.
        """
        return _recalled(engine, query, kind, limit, config)

    @server.tool(name="forget_memory", annotations=WRITE_ANNOTATIONS)
    def forget_memory(
        text: ForgetParameter, scope: ScopeParameter | None = None
    ) -> dict[str, Any]:
        """Removes one statement that is no longer true.

        Pass the exact `text` a recall returned. It matches words and never
        meaning, so it removes that statement or nothing: if the text matches
        more than one, nothing is removed and the answer names them.

        Use it when a recalled statement is contradicted, and when a statement
        has simply stopped being true.
        """
        return _forgotten(engine, text, scope, config)

    @server.tool(name="record_handoff", annotations=WRITE_ANNOTATIONS)
    def record_handoff(content: HandoffParameter) -> dict[str, Any]:
        """Records this session's handoff for the next one, replacing the last.

        One statement at the top of this project's memory. The previous handoff
        is removed as part of the same call, so a project holds one handoff
        rather than a list of them, and `replaced` says how many went.

        Write what the next session needs to pick the work up: what is done,
        what is in flight, and what to do first.
        """
        return _handed_off(engine, content, config)

    return server


def _scope_directory(config: ServerConfig, scope: str) -> tuple[str, Path]:
    """Return the memory a scope names, refusing any other value."""

    if scope == LOCAL_SCOPE:
        return scope, config.local_directory
    if scope == GLOBAL_SCOPE:
        return scope, config.global_directory
    raise ToolError(f"scope must be {LOCAL_SCOPE!r} or {GLOBAL_SCOPE!r}, not {scope!r}")


def _recorded(
    engine: Retrieval,
    content: str,
    kind: str | None,
    scope: str,
    config: ServerConfig,
) -> dict[str, Any]:
    """Record one statement in the memory the scope names."""

    name, directory = _scope_directory(config, scope)
    try:
        recorded = engine.record(content=content, directory=directory, kind=kind)
    except ModelError as error:
        raise ToolError(str(error)) from error
    return {"scope": name, **recorded}


def _recalled(
    engine: Retrieval,
    query: str,
    kind: str | None,
    limit: int,
    config: ServerConfig,
) -> dict[str, Any]:
    """Answer one read from both memories, ranked together."""

    if not str(query or "").strip():
        raise ToolError(
            "query must not be empty: this server answers a question about what is "
            "remembered rather than returning a memory whole, because a memory "
            "grows and returning all of it would spend the context window on it"
        )
    capped = max(1, min(int(limit), MAX_RESULT_LIMIT))
    try:
        return engine.answer_both(
            directories={
                LOCAL_SCOPE: config.local_directory,
                GLOBAL_SCOPE: config.global_directory,
            },
            query=str(query),
            limit=capped,
            kind=kind,
        )
    except (IndexError, StoreError) as error:
        raise ToolError(str(error)) from error


def _forgotten(
    engine: Retrieval,
    text: str,
    scope: str | None,
    config: ServerConfig,
) -> dict[str, Any]:
    """Remove the one statement a text is exactly, from one memory or either."""

    directories = {
        LOCAL_SCOPE: config.local_directory,
        GLOBAL_SCOPE: config.global_directory,
    }
    if scope is not None:
        _scope_directory(config, scope)
    try:
        return engine.forget(text=text, directories=directories, scope=scope)
    except ModelError as error:
        raise ToolError(str(error)) from error


def _handed_off(
    engine: Retrieval, content: str, config: ServerConfig
) -> dict[str, Any]:
    """Put this session's handoff at the top of this project's memory."""

    try:
        return engine.handoff(config.local_directory, content)
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
