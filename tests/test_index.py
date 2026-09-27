"""The search index: what it holds, how it follows the files, and what it costs.

The memory is the record; the index is derived from it. These tests pin that
relationship, because the failure that matters is a read that answers from an
index the files have moved past.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_ultra_rag_mcp.index import (
    INDEX_FILENAME,
    MemoryIndex,
    fts5_available,
    index_path,
    rebuild,
)
from memory_ultra_rag_mcp.store import (
    append_round,
    append_statement,
    read_standing,
    standing_document,
)


def test_this_python_can_build_the_index() -> None:
    """The server refuses to start without this, so the suite states it."""

    assert fts5_available() is True


def test_the_index_lives_beside_the_files_it_indexes(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/")

    with MemoryIndex(scope) as index:
        state = index.sync()

    assert index_path(scope) == scope / INDEX_FILENAME
    assert state["files"] == 1
    assert state["units"] >= 1


def test_a_scope_with_nothing_in_it_is_empty_rather_than_missing(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "scope"
    with MemoryIndex(scope) as index:
        found, mode = index.search("anything", 10)
    assert found == []
    assert mode == "all-words"


def test_a_statement_and_a_round_are_both_searchable(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "always cite the commit")
    append_round(scope, ["where is the draft"], ["in docs/"])

    with MemoryIndex(scope) as index:
        index.sync()
        statement, _ = index.search("cite", 10)
        round_, _ = index.search("draft", 10)

    assert [unit["kind"] for unit in statement] == ["statement"]
    assert [unit["kind"] for unit in round_] == ["round"]
    assert round_[0]["source"].startswith("project/")


def test_a_write_is_visible_once_the_write_side_has_synced_it(tmp_path: Path) -> None:
    """A search answers what the index holds; the write side keeps it current."""

    scope = tmp_path / "scope"
    read_standing(scope)
    with MemoryIndex(scope) as index:
        first, _ = index.search("quixotic", 10)
    append_statement(scope, "the quixotic release is in tags")
    # The writer syncs the one file it wrote; a search is not where that happens.
    with MemoryIndex(scope) as index:
        index.sync_file(standing_document(scope))
        second, _ = index.search("quixotic", 10)

    assert first == []
    assert [unit["text"] for unit in second] == ["the quixotic release is in tags"]


def test_a_round_is_visible_once_the_write_side_has_synced_it(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    with MemoryIndex(scope) as index:
        index.sync()
    written = append_round(scope, ["where is the draft"], ["in docs/"])
    with MemoryIndex(scope) as index:
        index.sync_file(written)
        found, _ = index.search("draft", 10)
    assert [unit["text"] for unit in found] == ["where is the draft\nin docs/"]


def test_a_hand_edit_is_a_reindex_rather_than_a_silent_stale_answer(
    tmp_path: Path,
) -> None:
    """The deliberate cost: a read does not notice an edit behind its back.

    A read is bounded work, so it does not walk the scope to discover a file
    changed. It reports ``freshness`` instead, and ``reindex`` is the answer to
    that report. This test is here so the trade cannot be changed by accident.
    """

    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/")
    with MemoryIndex(scope) as index:
        index.sync()
        stale, _ = index.search("draft", 10)
        freshness = index.freshness()

    standing = standing_document(scope)
    standing.write_text(
        standing.read_text(encoding="utf-8").replace("docs/", "drafts/"),
        encoding="utf-8",
    )
    with MemoryIndex(scope) as index:
        # A search still answers the old text, and says the index is behind.
        served, _ = index.search("draft", 10)
        assert index.freshness()["files_behind"] == 1
        # A reindex re-reads the file whole, because its beginning changed.
        index.sync()
        current, _ = index.search("draft", 10)

    assert [unit["text"] for unit in stale] == ["the draft lives in docs/"]
    assert [unit["text"] for unit in served] == ["the draft lives in docs/"]
    assert freshness == {"files_behind": 0}
    assert [unit["text"] for unit in current] == ["the draft lives in drafts/"]


def test_a_deleted_file_drops_its_units_on_the_next_pass(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    written = append_round(scope, ["the draft"], ["in docs/"])
    with MemoryIndex(scope) as index:
        index.sync()
    written.unlink()
    with MemoryIndex(scope) as index:
        index.sync()
        found, _ = index.search("draft", 10)
    assert found == []


def test_deleting_the_index_rebuilds_it(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/")
    with MemoryIndex(scope) as index:
        index.sync()
    index_path(scope).unlink()

    with MemoryIndex(scope) as index:
        # Thrown away, and the answer comes back from the files.
        index.sync()
        found, _ = index.search("draft", 10)
    assert [unit["text"] for unit in found] == ["the draft lives in docs/"]


def test_rebuild_reports_what_it_holds(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "one")
    append_statement(scope, "two")
    state = rebuild(scope)
    # Two statements. Not the template the first read created: a seed is not
    # something anyone wrote, so it cannot answer a query.
    assert state["units"] == 2


def test_one_past_the_limit_is_how_the_index_says_there_was_more(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "scope"
    for index_number in range(4):
        append_round(scope, [f"draft note {index_number}"], ["noted"])
    with MemoryIndex(scope) as index:
        index.sync()
        found, _ = index.search("draft", 2)
    # The search asks the index for one row past the limit, so the caller can say
    # whether the answer was complete.
    assert len(found) == 3


def test_a_query_of_only_punctuation_finds_nothing(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/")
    with MemoryIndex(scope) as index:
        found, mode = index.search("?! ...", 10)
    assert found == []
    assert mode == "no-terms"


def test_a_query_that_fts5_cannot_parse_is_reported_not_raised(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/")
    with MemoryIndex(scope) as index, pytest.raises(Exception) as caught:
        index._match(index._open(), 'a "unbalanced', 10)
    assert "could not be run" in str(caught.value)


def test_the_index_is_not_the_record(tmp_path: Path) -> None:
    """Every unit's text is present in the file it says it came from."""

    scope = tmp_path / "scope"
    append_statement(scope, "always cite the commit")
    written = append_round(scope, ["where is the draft"], ["in docs/"])

    with MemoryIndex(scope) as index:
        state = index.sync()
        found, _ = index.search("draft", 10)

    assert state["units"] == 2
    standing = (scope / "MEMORY.md").read_text(encoding="utf-8")
    rounds = written.read_text(encoding="utf-8")
    for unit in found:
        origin = standing if unit["source"] == "MEMORY.md" else rounds
        assert unit["text"].splitlines()[0] in origin
