"""Where each kind of memory lives, resolved and validated before serving.

Two roots, one behaviour:

* **local memory** is the bound project's own, kept inside the repository under
  ``.memory-rag``, so a project carries its memory with it and no other project
  can see it;
* **global memory** is the user's, kept under the storage root, which defaults to
  this account's home data directory and moves with ``--storage-root`` or
  ``MEMORY_ULTRARAG_STORAGE_ROOT`` — or points at UltraRAG's UI storage tree
  through ``ULTRARAG_UI_STORAGE_ROOT``, so a UltraRAG UI shows the same memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

__all__ = [
    "APP_NAME",
    "GLOBAL_MEMORY_DIRNAME",
    "LOCAL_STATE_DIRNAME",
    "PROJECT_ROOT_ENV_VAR",
    "STORAGE_ENV_VAR",
    "ULTRARAG_STORAGE_ENV_VAR",
    "ConfigurationError",
    "ServerConfig",
    "default_storage_root",
    "global_memory_root",
    "resolve_config",
]

APP_NAME = "memory-ultra-rag-mcp"

#: The directory a project's own memory lives in, inside the repository.
LOCAL_STATE_DIRNAME = ".memory-rag"

#: The project this server is bound to, when the host passes it in the environment.
PROJECT_ROOT_ENV_VAR = "MEMORY_ULTRARAG_PROJECT_ROOT"

#: This package's own switch for where a user's global memory lives.
STORAGE_ENV_VAR = "MEMORY_ULTRARAG_STORAGE_ROOT"

#: UltraRAG's own variable for its UI storage tree, honored for interoperability.
ULTRARAG_STORAGE_ENV_VAR = "ULTRARAG_UI_STORAGE_ROOT"

#: The directory inside the storage root that holds every user's global memory.
GLOBAL_MEMORY_DIRNAME = "memory"


class ConfigurationError(ValueError):
    """Raised when the selected roots cannot hold memory."""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """A bound project, the storage root global memory lives under, and its tree."""

    project_root: Path
    local_directory: Path
    storage_root: Path
    global_root: Path


def default_storage_root() -> Path:
    """Return the home data directory that holds global memory by default.

    The default belongs to the account, not to a project, so every project this
    user opens reads the same global memory. ``--storage-root``,
    ``MEMORY_ULTRARAG_STORAGE_ROOT``, or ``ULTRARAG_UI_STORAGE_ROOT`` move it.
    """
    return Path(user_data_path(APP_NAME, appauthor=False))


def global_memory_root(storage_root: Path) -> Path:
    """Return the directory holding every user's global memory."""
    return storage_root / GLOBAL_MEMORY_DIRNAME


def resolve_config(
    project_root: str | Path,
    storage_root: str | Path | None = None,
) -> ServerConfig:
    """Resolve and validate the two roots before any memory is touched.

    The storage root comes from the argument, then from
    ``MEMORY_ULTRARAG_STORAGE_ROOT``, then from ``ULTRARAG_UI_STORAGE_ROOT``, and
    otherwise from the home data directory. A relative root is resolved against
    the working directory, and an absolute one is used as given.
    """
    project = Path(project_root).expanduser().resolve()
    if not project.is_dir():
        raise ConfigurationError(
            f"project root is not a directory: {project}; point it at the "
            "repository whose memory is served"
        )

    storage = _selected_storage_root(storage_root)
    local = project / LOCAL_STATE_DIRNAME
    local.mkdir(parents=True, exist_ok=True)
    storage.mkdir(parents=True, exist_ok=True)

    return ServerConfig(
        project_root=project,
        local_directory=local,
        storage_root=storage,
        global_root=global_memory_root(storage),
    )


def _selected_storage_root(selected: str | Path | None) -> Path:
    """Return the storage root the argument or the environment selects."""
    for candidate in (
        selected,
        os.environ.get(STORAGE_ENV_VAR),
        os.environ.get(ULTRARAG_STORAGE_ENV_VAR),
    ):
        if candidate is None:
            continue
        text = str(candidate).strip()
        if not text:
            continue
        return Path(text).expanduser().resolve()
    return default_storage_root()
