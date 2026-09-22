"""Configuration is validated before anything is served.

The happy path needs a Git checkout whose commit equals the pinned revision, so
these tests build a throwaway checkout and patch the two constants that identify
the pin. That keeps the validation logic under test without pretending a
different revision is the pinned one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memory_ultra_rag_mcp import config
from memory_ultra_rag_mcp.config import ConfigurationError, resolve_config


def _fake_checkout(
    tmp_path: Path,
    version: str = config.BASELINE_VERSION,
    *,
    entrypoint: bool = True,
) -> Path:
    """Create a minimal, committed UltraRAG-shaped checkout."""
    root = tmp_path / "UltraRAG"
    root.mkdir(parents=True, exist_ok=True)
    if entrypoint:
        path = root / "servers" / "memory" / "src" / "memory.py"
        path.parent.mkdir(parents=True)
        path.write_text("# upstream memory server\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "ultrarag"\nversion = "{version}"\n', encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "x",
        ],
        cwd=root,
        check=True,
    )
    return root


@pytest.fixture()
def pinned_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _fake_checkout(tmp_path)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(config, "BASELINE_COMMIT", commit)
    return root


def test_a_missing_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not a directory"):
        resolve_config(ultrarag_root=tmp_path / "absent")


def test_an_unsupported_version_is_refused(tmp_path: Path) -> None:
    root = _fake_checkout(tmp_path, version="9.9.9")
    with pytest.raises(ConfigurationError, match="unsupported UltraRAG version"):
        resolve_config(ultrarag_root=root)


def test_a_missing_entrypoint_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout without the memory server is refused rather than served."""
    root = _fake_checkout(tmp_path, entrypoint=False)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(config, "BASELINE_COMMIT", commit)
    with pytest.raises(ConfigurationError, match="missing UltraRAG memory server"):
        resolve_config(ultrarag_root=root)


def test_a_modified_checkout_is_refused(pinned_checkout: Path) -> None:
    entrypoint = pinned_checkout / "servers" / "memory" / "src" / "memory.py"
    entrypoint.write_text("# patched\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="tracked modifications"):
        resolve_config(ultrarag_root=pinned_checkout)


def test_resolution_creates_the_workspace_and_default_storage(
    tmp_path: Path, pinned_checkout: Path
) -> None:
    workspace = tmp_path / "workspace"
    resolved = resolve_config(ultrarag_root=pinned_checkout, workspace_root=workspace)

    assert resolved.storage_root == (workspace / "ui-storage").resolve()
    assert (workspace / "logs").is_dir()
    assert resolved.storage_root.is_dir()
    assert resolved.python_executable.is_file()


def test_an_explicit_storage_root_is_honoured(
    tmp_path: Path, pinned_checkout: Path
) -> None:
    existing = tmp_path / "UltraRAG" / "ui" / "storage"
    existing.mkdir(parents=True)
    resolved = resolve_config(ultrarag_root=pinned_checkout, storage_root=existing)
    assert resolved.storage_root == existing.resolve()
