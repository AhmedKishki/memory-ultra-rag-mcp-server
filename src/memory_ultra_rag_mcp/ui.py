"""Browser view for this server's memory, built on the shared UI package.

The view is a thin adapter over the four memory tools: it lists the scopes this
server can see — the bound project's own memory and the account's global memory
in the shared tree — reads and replaces a scope's standing document, and answers
the shared package's round actions with an empty answer, because this memory has
no dated rounds.

The shared UI still asks for rounds: it fetches them under the same ``memory``
capability that shows the memory view, and its page text names them. Answering
with an empty list is what keeps that page working without a change to a package
this one does not own, and an empty list is the true answer here. What the page
words make of it is a rough edge in the shared package, not in this adapter.

The shared UI never reads ``.memory-rag`` or the storage tree itself; every answer
here comes from this server, and a scope string that arrives from the browser is
authorized against the two directories this server owns before any path is built
from it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import socket
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import uvicorn
from fastmcp import Client
from ui_ultra_rag_mcp import (
    AdapterFactory,
    UICapabilities,
    UIProfile,
    UIRequestError,
    run_ui,
)
from ui_ultra_rag_mcp import create_ui_app as create_shared_ui_app

from . import __version__
from .config import (
    APP_NAME,
    PROJECT_ROOT_ENV_VAR,
    STORAGE_ENV_VAR,
    ULTRARAG_STORAGE_ENV_VAR,
    ConfigurationError,
    ServerConfig,
    resolve_config,
)
from .index import MemoryIndex
from .maintenance import embed_pending
from .retrieval import Retrieval
from .server import create_server
from .store import EXPORT_FILENAME, StoreError, parse_document

if TYPE_CHECKING:
    from starlette.applications import Starlette

__all__ = [
    "MEMORY_UI_PROFILE",
    "UIPort",
    "create_ui_app",
    "main",
    "version_label",
]

UI_NAME = "memory-ultra-rag-ui"
UI_DISTRIBUTION_NAME = "ui-ultra-rag-mcp"
MAX_ERROR_LENGTH = 1200
DEFAULT_PORT = 5052

#: The bound project's own memory, as the browser names it.
LOCAL_SCOPE = "local"

#: A user's global memory, as the browser names it; the identifier follows.
GLOBAL_SCOPE = "global"


def version_label() -> str:
    """Return the header label, naming both distributions the page runs on."""
    parts = [f"{APP_NAME} {__version__}"]
    try:
        ui_version = _distribution_version(UI_DISTRIBUTION_NAME)
    except PackageNotFoundError:  # the UI package is not installed
        ui_version = None
    if ui_version is not None:
        parts.append(f"UI {ui_version}")
    return " · ".join(parts)


MEMORY_UI_PROFILE = UIProfile(
    application_name="Memory UltraRAG",
    version_label=version_label(),
    project_label="Project",
    project_fallback_name="Project memory",
    navigation_label="Memory views",
    source_types_label="statements",
    ingest_intro=(
        "This server serves memory, not documents; there is no generation to build."
    ),
    ingest_busy_message="Working…",
    footer_text=(
        "Memory records the statements that were chosen to keep, written in the "
        "format this server also writes as the agent's tools are called."
    ),
    memory_label="Memory",
    memory_standing_label="Standing memory",
    memory_rounds_label="Statements",
    memory_note=(
        "The bound project's memory is kept inside that project at .memory-rag; "
        "global memory is kept wherever this server's storage root points, which "
        "is this account's home data directory by default. This page and the "
        "agent's memory tools write the same file. There are no dated exchanges: "
        "a memory is a set of statements, and anything worth keeping is recorded "
        "as one."
    ),
    capabilities=UICapabilities(
        # This server serves memory, not a corpus: the document workspace and the
        # ingestion controls would be questions it cannot answer.
        documents=False,
        sources=False,
        passage_context=False,
        ingestion=False,
        metadata=False,
        source_inclusion=False,
        source_files=False,
        metadata_filters=False,
        # Reranking is on for the memory tools, and this flag belongs to the
        # document search console, which this server does not serve.
        reranking=False,
        memory=True,
        memory_writes=True,
    ),
)


def require_global_scope(scope: str) -> str:
    """Return the scope if it is the one global scope, and refuse any other.

    A scope arrives from the browser, so it is checked here before it names
    anything. There is one global memory, so there is exactly one global scope:
    nothing a page sends can reach a second one.
    """
    if scope != GLOBAL_SCOPE:
        raise UIRequestError(
            f"Unknown memory scope: {scope or '(missing)'}", status_code=404
        )
    return GLOBAL_SCOPE


class MemoryToolClient(Protocol):
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> Any: ...


class MemoryUIAdapter:
    """Map the shared UI contract to this server's memory tools.

    Reads go through the same store the tools use, so the page shows exactly what
    an agent reads. Replacing the standing document has no tool and is the one
    write this adapter performs itself; the page's other write, a dated exchange,
    this memory does not keep.
    """

    def __init__(
        self,
        config: ServerConfig,
        client: MemoryToolClient,
        retrieval: Retrieval | None = None,
    ) -> None:
        self.config = config
        self.client = client
        # The page and the tools share one retrieval, so a statement the page saves
        # keeps the derived state in step exactly as one a tool records does.
        # Without it a page write would be invisible to the semantic side until a
        # reindex.
        self.retrieval = retrieval

    async def health(self) -> Mapping[str, Any]:
        return {
            "project_root": str(self.config.project_root),
            "local_memory": str(self.config.local_directory),
            "global_directory": str(self.config.global_directory),
        }

    async def call(
        self,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        handlers: dict[str, Any] = {
            "status": self.status,
            "memory_status": self.memory_status,
            "memory_rounds": self.memory_rounds,
            "memory_standing": self.memory_standing,
            "memory_append": self.memory_append,
            "memory_standing_save": self.memory_standing_save,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise UIRequestError(
                f"Unsupported memory operation: {operation}", status_code=404
            )
        try:
            return await handler(arguments)
        except UIRequestError:
            raise
        except (StoreError, ConfigurationError) as error:
            raise UIRequestError(str(error)[:MAX_ERROR_LENGTH]) from error

    def scope_directory(self, arguments: Mapping[str, Any]) -> Path:
        """Return the directory a request names, refusing any other scope."""
        scope = arguments.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            raise UIRequestError("A memory scope is required")
        return self.directory_for(scope.strip())

    def directory_for(self, scope: str) -> Path:
        """Return the directory a scope names, refusing any other scope.

        A scope arrives from the browser, so the one global scope is checked
        rather than assumed: with no per-user directories, a fallback would
        quietly hand the global memory to anything that asked.
        """
        if scope == LOCAL_SCOPE:
            return self.config.local_directory
        require_global_scope(scope)
        return self.config.global_directory

    def scopes(self) -> list[dict[str, Any]]:
        """Describe every scope this server can show, local memory first."""
        described = [
            {
                "scope": LOCAL_SCOPE,
                "label": "This project",
                "directory": self.config.local_directory,
            }
        ]
        described.append(
            {
                "scope": GLOBAL_SCOPE,
                "label": "Global",
                "directory": self.config.global_directory,
            }
        )
        for entry in described:
            directory = entry["directory"]
            entry["directory"] = str(directory)
            entry["standing_present"] = (directory / EXPORT_FILENAME).is_file()
            # The shared page shows a round count per scope. There are none, and
            # the honest zero is what keeps its wording honest rather than wrong.
            entry["round_count"] = 0
            entry["latest_round_date"] = None
        return described

    async def status(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Answer the workspace identity; this server has no generation.

        The shared host takes the project name and path in the header from here,
        and hides the generation summary because the profile reports no documents.
        """
        return {
            "ready": False,
            "project_root": str(self.config.project_root),
            "project_name": self.config.project_root.name,
            "message": (
                "This server serves memory, not documents, so there is no "
                "generation to build."
            ),
        }

    async def memory_status(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"scopes": self.scopes()}

    async def memory_rounds(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Answer the shared package's round action with nothing, because there is none.

        The page asks for rounds under the same capability that shows the memory
        view, and an action that refused would take the whole view down with it.
        A memory here is a set of statements, so the true answer is an empty list
        and the page's own wording is what says otherwise.
        """

        return {
            "scope": arguments.get("scope"),
            "round_count": 0,
            "truncated": False,
            "rounds": [],
        }

    async def memory_standing(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """The memory as the page edits it: statements, rendered, never a file.

        The page works in text because that is what an editor is for, and the text
        is rendered from the record rather than read from a document, so what it
        shows is always what is remembered.
        """

        directory = self.scope_directory(arguments)
        with MemoryIndex(directory) as index:
            index.adopt_document_if_empty()
            index.retire_superseded()
            return {
                "scope": arguments.get("scope"),
                "content": index.export(),
                "sha256": index.export_digest(),
            }

    async def memory_append(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Refuse the page's round write, because this memory records statements.

        A statement is recorded through ``record_memory``, and an exchange is not
        a statement. The page's standing-document editor still writes, and that is
        the one write it has.
        """

        del arguments
        raise UIRequestError(
            "This memory records statements, not dated exchanges. Edit the standing "
            "document to add to it, or use record_memory with a scope.",
            status_code=409,
        )

    async def memory_standing_save(
        self, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        directory = self.scope_directory(arguments)
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise UIRequestError("content must not be empty")
        expected = arguments.get("expected_sha256")
        if expected is not None and not isinstance(expected, str):
            raise UIRequestError("expected_sha256 must be a string")
        # The record is the memory, so the page's editor is a request to change
        # the set of statements rather than a file to write. A statement whose
        # words are unchanged keeps its date and its count.
        with MemoryIndex(directory) as index:
            if expected is not None and index.export_digest() != expected:
                raise UIRequestError(
                    "the memory changed since this page was loaded; reload it and "
                    "edit again",
                    status_code=409,
                )
            report = index.replace_all(parse_document(content))
            digest = index.export_digest()
        # A statement the page added or reworded needs a vector as much as one a
        # tool recorded, so the same queue takes them; without a retrieval in this
        # process there is nothing to embed with, and a reindex settles it.
        queued = 0
        if self.retrieval is not None:
            queued = embed_pending(
                directory,
                self.retrieval.embedder,
                self.retrieval.worker_for(directory),
            )
        return {
            "status": "saved",
            "scope": arguments.get("scope"),
            "sha256": digest,
            "units_queued": queued,
            **report,
        }

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Mapping[str, Any]:
        """Call one public memory tool and return its object response."""
        try:
            result = await self.client.call_tool(name, arguments, raise_on_error=True)
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            raise UIRequestError(message[:MAX_ERROR_LENGTH]) from exc
        data = getattr(result, "data", result)
        if not isinstance(data, Mapping):
            raise UIRequestError(
                f"Memory tool {name!r} returned a non-object response",
                status_code=502,
            )
        return data


def _adapter_factory(
    config: ServerConfig, retrieval: Retrieval | None = None
) -> AdapterFactory:
    """Build the adapter around this server's own tools, in this process.

    No child process is spawned: the memory tools are plain functions of this
    package, so the view drives the public tools in memory instead of over a pipe.
    """

    @asynccontextmanager
    async def adapter_context() -> AsyncIterator[MemoryUIAdapter]:
        async with Client(
            create_server(config, retrieval=retrieval), name=UI_NAME
        ) as client:
            yield MemoryUIAdapter(config, client, retrieval)

    return adapter_context


def create_ui_app(
    config: ServerConfig,
    *,
    memory_client: MemoryToolClient | None = None,
    retrieval: Retrieval | None = None,
) -> Starlette:
    """Create the shared UI with the memory adapter."""
    if memory_client is not None:
        return create_shared_ui_app(
            profile=MEMORY_UI_PROFILE,
            adapter=MemoryUIAdapter(config, memory_client),
        )
    return create_shared_ui_app(
        profile=MEMORY_UI_PROFILE,
        adapter_factory=_adapter_factory(config, retrieval),
    )


UI_HOST = "127.0.0.1"


def _port_is_available(host: str, port: int) -> bool:
    """Whether the loopback port can still be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


class UIPort:
    """An opt-in browser view served inside the MCP server process.

    The server has already resolved the project and the shared storage tree, so
    hosting the view here means the page shows exactly the memory the tools serve,
    and the view stops with the server instead of outliving it. The host is fixed
    to loopback, no browser is opened, and the URL — or the reason it could not
    start — goes to stderr, the only channel a stdio server may use.
    """

    def __init__(
        self,
        config: ServerConfig,
        *,
        port: int,
        retrieval: Retrieval | None = None,
    ) -> None:
        if not 1 <= port <= 65535:
            raise ConfigurationError("--ui-port must be between 1 and 65535")
        self.config = config
        self.retrieval = retrieval
        self.host = UI_HOST
        self.port = port
        self.error: str | None = None
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def ready(self) -> bool:
        """Whether the view is serving right now.

        uvicorn sets ``Server.started`` once and never clears it, so the live task
        is part of the test: after ``stop`` and after a bind failure the task is
        finished and the view is not serving.
        """
        return (
            self._server is not None
            and bool(self._server.started)
            and self._task is not None
            and not self._task.done()
        )

    async def start(self, *, memory_client: MemoryToolClient | None = None) -> None:
        """Serve the view for the life of the MCP server."""
        if not _port_is_available(self.host, self.port):
            self.error = (
                f"port {self.port} is already in use on {self.host}, so the "
                "browser view was not started; choose another --ui-port"
            )
            return
        app = create_ui_app(
            self.config, memory_client=memory_client, retrieval=self.retrieval
        )
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
            )
        )
        self._task = asyncio.create_task(self._server.serve())

    async def stop(self) -> None:
        """Ask the view to stop and wait for its task to finish."""
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            task, self._task = self._task, None
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=UI_NAME,
        description=(
            "Serve a local browser view of one project's memory and the shared "
            "global memory."
        ),
    )
    parser.add_argument(
        "--project-root",
        default=os.environ.get(PROJECT_ROOT_ENV_VAR),
        help=(
            "Repository whose memory is shown. Its own memory is read from "
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
        "--host",
        choices=sorted(("127.0.0.1", "localhost", "::1")),
        default=UI_HOST,
        help="Loopback address only (default: 127.0.0.1).",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Serve the browser view until interrupted."""
    arguments = _parser().parse_args(argv)
    if not arguments.project_root:
        print(
            f"{UI_NAME}: --project-root is required; point it at the repository "
            "whose memory is shown",
            file=sys.stderr,
        )
        return 2
    if not 1 <= arguments.port <= 65535:
        print(f"{UI_NAME}: --port must be between 1 and 65535", file=sys.stderr)
        return 2
    try:
        config = resolve_config(
            project_root=arguments.project_root,
            storage_root=arguments.storage_root,
        )
    except ConfigurationError as error:
        print(f"{UI_NAME}: {error}", file=sys.stderr)
        return 2
    if not _port_is_available(arguments.host, arguments.port):
        print(
            f"{UI_NAME}: port {arguments.port} is already in use on "
            f"{arguments.host}; choose another --port",
            file=sys.stderr,
        )
        return 2
    print(
        f"{UI_NAME}: serving {config.local_directory} and {config.global_directory} at "
        f"http://{arguments.host}:{arguments.port}",
        file=sys.stderr,
    )
    run_ui(
        create_ui_app(config),
        host=arguments.host,
        port=arguments.port,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
