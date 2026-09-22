"""Two roots: a project's own memory inside the repository, a shared global tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_ultra_rag_mcp.config import (
    APP_NAME,
    LOCAL_STATE_DIRNAME,
    STORAGE_ENV_VAR,
    ULTRARAG_STORAGE_ENV_VAR,
    ConfigurationError,
    default_storage_root,
    global_memory_root,
    resolve_config,
)


@pytest.fixture(autouse=True)
def _no_ambient_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own environment out of these expectations."""
    monkeypatch.delenv(STORAGE_ENV_VAR, raising=False)
    monkeypatch.delenv(ULTRARAG_STORAGE_ENV_VAR, raising=False)


def test_a_project_root_is_required_and_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not a directory"):
        resolve_config(project_root=tmp_path / "absent")


def test_local_memory_lives_inside_the_repository(tmp_path: Path) -> None:
    project = tmp_path / "thesis"
    project.mkdir()
    resolved = resolve_config(project_root=project)

    assert resolved.local_directory == project / LOCAL_STATE_DIRNAME
    assert resolved.local_directory.is_dir()


def test_global_memory_lives_under_the_storage_root(tmp_path: Path) -> None:
    project = tmp_path / "thesis"
    project.mkdir()
    storage = tmp_path / "shared"
    resolved = resolve_config(project_root=project, storage_root=storage)

    assert resolved.storage_root == storage.resolve()
    assert resolved.global_root == storage.resolve() / "memory"
    assert resolved.global_root == global_memory_root(storage.resolve())


def test_the_storage_root_defaults_to_the_accounts_home_data_directory() -> None:
    default = default_storage_root()

    assert default.is_absolute()
    assert default.name == APP_NAME
    assert Path.home() in default.parents


def test_the_default_storage_root_is_used_without_any_setting(tmp_path: Path) -> None:
    project = tmp_path / "thesis"
    project.mkdir()

    resolved = resolve_config(project_root=project)

    assert resolved.storage_root == default_storage_root()
    assert resolved.global_root == default_storage_root() / "memory"


def test_this_projects_variable_moves_global_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = tmp_path / "elsewhere"
    monkeypatch.setenv(STORAGE_ENV_VAR, str(moved))
    project = tmp_path / "thesis"
    project.mkdir()

    resolved = resolve_config(project_root=project)

    assert resolved.storage_root == moved.resolve()
    assert resolved.global_root == moved.resolve() / "memory"


def test_ultrarags_own_variable_is_honored_for_interoperability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "UltraRAG" / "ui" / "storage"
    monkeypatch.setenv(ULTRARAG_STORAGE_ENV_VAR, str(shared))
    project = tmp_path / "thesis"
    project.mkdir()

    resolved = resolve_config(project_root=project)
    assert resolved.storage_root == shared.resolve()
    assert resolved.global_root == shared.resolve() / "memory"


def test_this_projects_variable_wins_over_ultrarags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ULTRARAG_STORAGE_ENV_VAR, str(tmp_path / "ultrarag"))
    monkeypatch.setenv(STORAGE_ENV_VAR, str(tmp_path / "own"))
    project = tmp_path / "thesis"
    project.mkdir()

    resolved = resolve_config(project_root=project)
    assert resolved.storage_root == (tmp_path / "own").resolve()


def test_an_explicit_storage_root_wins_over_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STORAGE_ENV_VAR, str(tmp_path / "from-env"))
    project = tmp_path / "thesis"
    project.mkdir()

    resolved = resolve_config(project_root=project, storage_root=tmp_path / "flag")
    assert resolved.storage_root == (tmp_path / "flag").resolve()


def test_an_empty_variable_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STORAGE_ENV_VAR, "   ")
    project = tmp_path / "thesis"
    project.mkdir()

    resolved = resolve_config(project_root=project)
    assert resolved.storage_root == default_storage_root()
