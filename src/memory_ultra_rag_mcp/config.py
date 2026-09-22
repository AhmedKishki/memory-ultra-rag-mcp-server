"""Where each kind of memory lives, resolved and validated before serving.

Two roots, one behaviour:

* **local memory** is the bound project's own, kept inside the repository under
  ``.memory-rag``, so a project carries its memory with it and no other project
  can see it;
* **global memory** is the user's, kept in UltraRAG's UI storage tree, so every
  instance serving that tree reads and writes the same memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

__all__ = [
    "APP_NAME",
    "LOCAL_STATE_DIRNAME",
    "STORAGE_ENV_VAR",
    "ConfigurationError",
    "ServerConfig",
    "default_workspace_root",
    "global_memory_root",
    "resolve_config",
]

APP_NAME = "memory-ultra-rag-mcp"

#: The directory a project's own memory lives in, inside the repository.
LOCAL_STATE_DIRNAME = ".memory-rag"

#: UltraRAG's own variable for the UI storage tree; honored so the two agree.
STORAGE_ENV_VAR = "ULTRARAG_UI_STORAGE_ROOT"

GLOBAL_MEMORY_DIRNAME = "memory"


class ConfigurationError(ValueError):
    """Raised when the selected roots cannot hold memory."""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """A bound project, a global tree, and a workspace."""

    project_root: Path
    local_directory: Path
    global_root: Path
    storage_root: Path
    workspace_root: Path


def default_workspace_root() -> Path:
    """Return the per-user workspace used when no storage root is given."""
    return user_data_path(APP_NAME)


def global_memory_root(storage_root: Path) -> Path:
    """Return the directory holding every user's global memory."""
    return storage_root / GLOBAL_MEMORY_DIRNAME


def resolve_config(
    project_root: str | Path,
    storage_root: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> ServerConfig:
    """Resolve and validate the two roots before any memory is touched."""
    project = Path(project_root).expanduser().resolve()
    if not project.is_dir():
        raise ConfigurationError(
            f"project root is not a directory: {project}; point it at the "
            "repository whose memory is served"
        )

    workspace = Path(workspace_root or default_workspace_root()).expanduser().resolve()
    selected_storage = storage_root or os.environ.get(STORAGE_ENV_VAR)
    storage = (
        Path(selected_storage).expanduser()
        if selected_storage
        else workspace / "ui-storage"
    )
    if not storage.is_absolute():
        storage = (workspace / storage).resolve()
    else:
        storage = storage.resolve()

    local = project / LOCAL_STATE_DIRNAME
    local.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    storage.mkdir(parents=True, exist_ok=True)

    return ServerConfig(
        project_root=project,
        local_directory=local,
        global_root=global_memory_root(storage),
        storage_root=storage,
        workspace_root=workspace,
    )
