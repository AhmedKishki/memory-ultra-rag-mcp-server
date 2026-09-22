"""Configuration and validation for the UltraRAG memory MCP server.

The upstream memory server hard-codes its storage root relative to the UltraRAG
checkout it lives in, which is exactly right when a pipeline runs it and wrong
for a standalone server installed anywhere. The only thing this module decides
is *where that root is* and *which checkout is being served*; both are validated
before anything is started, so a misconfigured server fails loudly instead of
writing memory into an unexpected directory.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

from .manifest import (
    BASELINE_COMMIT,
    BASELINE_VERSION,
    MEMORY_SERVER_ENTRYPOINT,
)

__all__ = [
    "ConfigurationError",
    "ServerConfig",
    "default_workspace_root",
    "resolve_config",
]

APP_NAME = "memory-ultra-rag-mcp"
UI_STORAGE_DIRNAME = "ui-storage"


class ConfigurationError(ValueError):
    """Raised when the selected UltraRAG checkout cannot be served."""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """A validated UltraRAG checkout and the storage it will write to."""

    ultrarag_root: Path
    workspace_root: Path
    storage_root: Path
    python_executable: Path
    log_level: str = "warn"


def default_workspace_root() -> Path:
    """Return the per-user workspace that holds the child log and the storage."""
    return user_data_path(APP_NAME)


def _read_package_version(ultrarag_root: Path) -> str:
    pyproject = ultrarag_root / "pyproject.toml"
    try:
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        return str(data["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(
            f"cannot read the UltraRAG version from {pyproject}"
        ) from error


def _read_source_revision(ultrarag_root: Path) -> str:
    """Return the checkout's commit, refusing a tree with tracked changes.

    A modified tree would make "the pinned revision" a claim rather than a fact,
    and this package exists to serve one exact upstream revision.
    """
    try:
        commit = subprocess.run(
            ["git", "-C", str(ultrarag_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(ultrarag_root),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ConfigurationError(
            f"cannot read the UltraRAG revision at {ultrarag_root}; "
            "serve a Git checkout of the pinned commit"
        ) from error
    if tracked.strip():
        raise ConfigurationError(
            f"the UltraRAG checkout at {ultrarag_root} has tracked modifications; "
            "serve an unmodified checkout"
        )
    return commit


def resolve_config(
    ultrarag_root: str | Path,
    workspace_root: str | Path | None = None,
    storage_root: str | Path | None = None,
    python_executable: str | Path | None = None,
    log_level: str = "warn",
) -> ServerConfig:
    """Resolve and validate everything the server needs before it starts."""
    root = Path(ultrarag_root).expanduser().resolve()
    workspace = Path(workspace_root or default_workspace_root()).expanduser().resolve()
    # Do not resolve the interpreter symlink: a virtual environment's `python`
    # commonly points at the system binary, and resolving it would discard the
    # environment that has the UltraRAG dependencies installed.
    python = Path(python_executable or sys.executable).expanduser().absolute()

    if not root.is_dir():
        raise ConfigurationError(f"UltraRAG root is not a directory: {root}")
    if not python.is_file():
        raise ConfigurationError(f"Python executable does not exist: {python}")

    version = _read_package_version(root)
    if version != BASELINE_VERSION:
        raise ConfigurationError(
            f"unsupported UltraRAG version {version!r}; expected {BASELINE_VERSION!r}"
        )
    commit = _read_source_revision(root)
    if commit != BASELINE_COMMIT:
        raise ConfigurationError(
            f"unsupported UltraRAG revision {commit}; expected {BASELINE_COMMIT}"
        )
    entrypoint = root / MEMORY_SERVER_ENTRYPOINT
    if not entrypoint.is_file():
        raise ConfigurationError(f"missing UltraRAG memory server: {entrypoint}")

    storage = Path(storage_root or workspace / UI_STORAGE_DIRNAME).expanduser()
    if not storage.is_absolute():
        storage = (workspace / storage).resolve()
    else:
        storage = storage.resolve()

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "logs").mkdir(exist_ok=True)
    storage.mkdir(parents=True, exist_ok=True)

    return ServerConfig(
        ultrarag_root=root,
        workspace_root=workspace,
        storage_root=storage,
        python_executable=python,
        log_level=log_level,
    )
