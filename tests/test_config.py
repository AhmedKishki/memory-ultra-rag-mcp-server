"""Two roots: a project's own memory inside the repository, a shared global tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_ultra_rag_mcp.config import (
    LOCAL_STATE_DIRNAME,
    ConfigurationError,
    global_memory_root,
    resolve_config,
)


def test_a_project_root_is_required_and_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not a directory"):
        resolve_config(project_root=tmp_path / "absent")


def test_local_memory_lives_inside_the_repository(tmp_path: Path) -> None:
    project = tmp_path / "thesis"
    project.mkdir()
    resolved = resolve_config(project_root=project, workspace_root=tmp_path / "ws")

    assert resolved.local_directory == project / LOCAL_STATE_DIRNAME
    assert resolved.local_directory.is_dir()


def test_global_memory_lives_under_the_storage_root(tmp_path: Path) -> None:
    project = tmp_path / "thesis"
    project.mkdir()
    storage = tmp_path / "shared" / "ui-storage"
    resolved = resolve_config(
        project_root=project, storage_root=storage, workspace_root=tmp_path / "ws"
    )

    assert resolved.storage_root == storage.resolve()
    assert resolved.global_root == storage.resolve() / "memory"
    assert resolved.global_root == global_memory_root(storage.resolve())


def test_the_storage_root_defaults_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ULTRARAG_UI_STORAGE_ROOT", raising=False)
    project = tmp_path / "thesis"
    project.mkdir()
    workspace = tmp_path / "ws"
    resolved = resolve_config(project_root=project, workspace_root=workspace)

    assert resolved.storage_root == (workspace / "ui-storage").resolve()


def test_the_storage_root_can_come_from_ultrarags_own_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "UltraRAG" / "ui" / "storage"
    monkeypatch.setenv("ULTRARAG_UI_STORAGE_ROOT", str(shared))
    project = tmp_path / "thesis"
    project.mkdir()

    resolved = resolve_config(project_root=project, workspace_root=tmp_path / "ws")
    assert resolved.storage_root == shared.resolve()
