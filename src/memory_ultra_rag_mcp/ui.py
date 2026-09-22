"""Browser view for this server's memory, built on the shared UI package.

The view is a thin adapter over the four memory tools: it lists the scopes this
server can see — the bound project's own memory and every user's global memory in
the shared tree — reads a scope's standing document and its recorded rounds, and,
when the host enables writes, appends a round or replaces a standing document
through the same store the tools use.

The shared UI never reads ``.memory-rag`` or the storage tree itself; every answer
here comes from this server, and a scope string that arrives from the browser is
authorized against the two directories this server owns before any path is built
from it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
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
from .config import APP_NAME, ConfigurationError, ServerConfig, resolve_config
from .server import create_server
from .store import (
    StoreError,
    count_rounds,
    latest_round_date,
    list_rounds,
    read_standing,
    standing_digest,
    user_id_error,
    write_standing,
)

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
GLOBAL_SCOPE_PREFIX = "global:"


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
    source_types_label="recorded rounds",
    ingest_intro=(
        "This server serves memory, not documents; there is no generation to build."
    ),
    ingest_busy_message="Working…",
    footer_text=(
        "Memory records what was said, written in the format this server also "
        "writes as the agent's tools are called."
    ),
    memory_label="Memory",
    memory_standing_label="Standing memory",
    memory_rounds_label="Recorded rounds",
    memory_note=(
        "The bound project's memory is kept inside that project at .memory-rag; "
        "global memory is kept in the UltraRAG UI storage tree. This page and the "
        "agent's memory tools write the same files."
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
        reranking=False,
        memory=True,
        memory_writes=True,
    ),
)


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


def global_scopes(config: ServerConfig) -> list[str]:
    """Return the user scopes the shared tree holds, the default one first.

    Listing the tree is this server's business: the browser never reads a
    directory, it only sends back a scope this function reported.
    """
    names: set[str] = set()
    if config.global_root.is_dir():
        names = {
            entry.name
            for entry in config.global_root.iterdir()
            if entry.is_dir() and user_id_error(entry.name) is None
        }
    if "default" not in names:
        names.add("default")
    return sorted(names, key=lambda name: (name != "default", name))


def global_user_id(scope: str) -> str:
    """Return the user a global scope names, refusing one that could escape.

    A scope arrives from the browser, so the identifier is checked here before it
    becomes a directory name, exactly as it is checked for the tools.
    """
    if not scope.startswith(GLOBAL_SCOPE_PREFIX):
        raise UIRequestError(
            f"Unknown memory scope: {scope or '(missing)'}", status_code=404
        )
    user_id = scope[len(GLOBAL_SCOPE_PREFIX) :].strip()
    problem = user_id_error(user_id)
    if problem is not None:
        raise UIRequestError(problem)
    return str(user_id or "default")


class MemoryToolClient(Protocol):
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> Any: ...


def _required_text(arguments: Mapping[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise UIRequestError(f"{field} must not be empty")
    return value.strip()


class MemoryUIAdapter:
    """Map the shared UI contract to this server's memory tools.

    Reads go through the same store the tools use, so the page shows exactly what
    an agent reads. A write that the tools already perform is sent to the tool, so
    the round is validated and formatted in one place; replacing the standing
    document has no tool, and is the one write this adapter performs itself.
    """

    def __init__(self, config: ServerConfig, client: MemoryToolClient) -> None:
        self.config = config
        self.client = client

    async def health(self) -> Mapping[str, Any]:
        return {
            "project_root": str(self.config.project_root),
            "local_memory": str(self.config.local_directory),
            "global_memory_root": str(self.config.global_root),
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
        if scope == LOCAL_SCOPE:
            return self.config.local_directory
        return self.config.global_root / global_user_id(scope)

    def scopes(self) -> list[dict[str, Any]]:
        """Describe every scope this server can show, local memory first."""
        described = [
            {
                "scope": LOCAL_SCOPE,
                "label": "This project",
                "directory": self.config.local_directory,
            }
        ]
        described += [
            {
                "scope": f"{GLOBAL_SCOPE_PREFIX}{user_id}",
                "label": f"Global · {user_id}",
                "directory": self.config.global_root / user_id,
            }
            for user_id in global_scopes(self.config)
        ]
        for entry in described:
            directory = entry["directory"]
            entry["directory"] = str(directory)
            entry["standing_present"] = standing_digest(directory) is not None
            entry["round_count"] = count_rounds(directory)
            entry["latest_round_date"] = latest_round_date(directory)
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
        directory = self.scope_directory(arguments)
        limit = arguments.get("limit", 20)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise UIRequestError("limit must be an integer between 1 and 200")
        rounds, truncated = list_rounds(directory, limit)
        return {
            "scope": arguments.get("scope"),
            "round_count": count_rounds(directory),
            "truncated": truncated,
            "rounds": rounds,
        }

    async def memory_standing(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        directory = self.scope_directory(arguments)
        # The read creates the standing document from UltraRAG's template when it
        # is missing, exactly as the memory tools do.
        return {
            "scope": arguments.get("scope"),
            "content": read_standing(directory),
            "sha256": standing_digest(directory),
        }

    async def memory_append(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        scope = str(arguments.get("scope") or "").strip()
        directory = self.scope_directory(arguments)
        payload: dict[str, Any] = {
            "q_ls": [_required_text(arguments, "user_message")],
            "ans_ls": [_required_text(arguments, "assistant_message")],
        }
        if scope == LOCAL_SCOPE:
            tool = "save_local_memory"
        else:
            tool = "save_memory"
            payload["user_id"] = global_user_id(scope)
        saved = await self.call_tool(tool, payload)
        return {
            "status": "saved",
            "scope": scope,
            "written": saved.get("written"),
            "round_count": count_rounds(directory),
        }

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
        digest = write_standing(directory, content, expected_sha256=expected)
        return {
            "status": "saved",
            "scope": arguments.get("scope"),
            "sha256": digest,
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


def _adapter_factory(config: ServerConfig) -> AdapterFactory:
    """Build the adapter around this server's own tools, in this process.

    No child process is spawned: the memory tools are plain functions of this
    package, so the view drives the public tools in memory instead of over a pipe.
    """

    @asynccontextmanager
    async def adapter_context() -> AsyncIterator[MemoryUIAdapter]:
        async with Client(create_server(config), name=UI_NAME) as client:
            yield MemoryUIAdapter(config, client)

    return adapter_context


def create_ui_app(
    config: ServerConfig,
    *,
    memory_client: MemoryToolClient | None = None,
) -> Starlette:
    """Create the shared UI with the memory adapter."""
    if memory_client is not None:
        return create_shared_ui_app(
            profile=MEMORY_UI_PROFILE,
            adapter=MemoryUIAdapter(config, memory_client),
        )
    return create_shared_ui_app(
        profile=MEMORY_UI_PROFILE,
        adapter_factory=_adapter_factory(config),
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

    def __init__(self, config: ServerConfig, *, port: int) -> None:
        if not 1 <= port <= 65535:
            raise ConfigurationError("--ui-port must be between 1 and 65535")
        self.config = config
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
        app = create_ui_app(self.config, memory_client=memory_client)
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
        default=None,
        help=(
            "Repository whose memory is shown. Its own memory is read from "
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
            workspace_root=arguments.workspace_root,
        )
    except ConfigurationError as error:
        print(f"{UI_NAME}: {error}", file=sys.stderr)
        return 2
    print(
        f"{UI_NAME}: serving {config.local_directory} and {config.global_root} at "
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
