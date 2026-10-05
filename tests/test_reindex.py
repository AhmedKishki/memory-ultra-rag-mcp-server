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


def test_the_rendering_goes_where_the_flag_names(tmp_path: Path) -> None:
    """``--export-to`` names where the renderings go, and nothing else.

    The run renders both memories, and two renderings cannot share one file
    without the second losing the first, so a named path is a directory to fill.
    A name that reads as a file is refused rather than quietly turned into a
    directory of that name, which is where the flag used to put it.
    """

    project = tmp_path / "thesis"
    project.mkdir()
    target = tmp_path / "notes"

    assert (
        reindex.main(
            [
                "--project-root",
                str(project),
                "--storage-root",
                str(tmp_path / "shared"),
                "--no-embed",
                "--export-to",
                str(target),
            ]
        )
        == 0
    )

    written = sorted(path.name for path in target.iterdir())
    assert written == [".memory-rag.md", "default.md"]
    for path in target.iterdir():
        assert path.read_text(encoding="utf-8").startswith("# MEMORY")


def test_a_directory_with_a_dot_in_its_name_is_a_directory(
    tmp_path: Path,
) -> None:
    """A path the filesystem says is a directory is one whatever it is called.

    `notes.v2/` is a name somebody chose, not an instruction to write a file called
    `notes.v2`. Refusing it because of the dot would be refusing a path that is
    already the thing this flag needs.
    """

    project = tmp_path / "thesis"
    project.mkdir()
    target = tmp_path / "notes.v2"
    target.mkdir()

    assert (
        reindex.main(
            [
                "--project-root",
                str(project),
                "--storage-root",
                str(tmp_path / "shared"),
                "--no-embed",
                "--export-to",
                str(target),
            ]
        )
        == 0
    )

    assert sorted(path.name for path in target.iterdir()) == [
        ".memory-rag.md",
        "default.md",
    ]
    assert target.is_dir()


def test_export_to_refuses_a_name_that_reads_as_one_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "thesis"
    project.mkdir()
    target = tmp_path / "memories.md"

    code = reindex.main(
        [
            "--project-root",
            str(project),
            "--storage-root",
            str(tmp_path / "shared"),
            "--no-embed",
            "--export-to",
            str(target),
        ]
    )

    assert code == 2
    assert "name a directory instead" in capsys.readouterr().err
    assert not target.exists()


def test_the_version_is_the_one_the_package_declares(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A server is asked for its version, and two copies of it disagree.

    ``pyproject.toml`` is where the version is declared; a copy in the module is a
    number that drifts at every release, and the one that drifts is the one a
    client is told.
    """

    from importlib.metadata import version

    from memory_ultra_rag_mcp import __version__

    assert __version__ == version("memory-ultra-rag-mcp")

    project = tmp_path / "versioned"
    project.mkdir()
    with pytest.raises(SystemExit) as raised:
        reindex.main(["--project-root", str(project), "--version"])

    assert raised.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


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
