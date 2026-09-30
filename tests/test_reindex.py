"""The reindex command: what it settles, and what it refuses to be asked for."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_ultra_rag_mcp import reindex


def test_printing_the_configuration_settles_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--print-config`` says what is in force without touching a memory.

    It is the flag a user runs to find out where a number came from, and it must
    answer without fetching a model, without embedding, and without leaving a
    directory in the project it was pointed at.
    """

    project = tmp_path / "thesis"
    project.mkdir()

    assert reindex.main(["--project-root", str(project), "--print-config"]) == 0

    printed = capsys.readouterr().out
    assert "retrieval.cosine_floor" in printed
    assert not (project / ".memory-rag").exists()
    assert not (tmp_path / "shared").exists()


def test_a_named_user_is_not_a_scope_this_server_serves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """There is one global memory, so there is no flag to name another.

    A value appended to the global directory would name any directory at all,
    which is a way out of the storage tree rather than a way to reach a memory.
    """

    project = tmp_path / "thesis"
    project.mkdir()

    with pytest.raises(SystemExit):
        reindex.main(["--project-root", str(project), "--user", "someone"])

    assert "unrecognized arguments" in capsys.readouterr().err


def test_the_project_root_is_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert reindex.main([]) == 2
    assert "--project-root is required" in capsys.readouterr().err
