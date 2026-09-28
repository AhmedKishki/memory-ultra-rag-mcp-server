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
    append_statement,
    read_standing,
    standing_document,
)


def test_this_python_can_build_the_index() -> None:
    """The server refuses to start without this, so the suite states it."""

    assert fts5_available() is True


def test_the_index_lives_beside_the_files_it_indexes(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/", statement_type="note")

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


def test_every_statement_is_searchable_and_says_it_is_a_statement(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "always cite the commit", statement_type="RULE")
    append_statement(scope, "the draft lives in docs/", statement_type="NOTE")

    with MemoryIndex(scope) as index:
        index.sync()
        found, _ = index.search("draft", 10)

    assert [unit["kind"] for unit in found] == ["statement"]
    assert found[0]["source"] == "MEMORY.md"


def test_a_label_is_searchable_because_it_is_indexed_with_its_statement(
    tmp_path: Path,
) -> None:
    """Which is the point of recording one: a query for the category finds them."""

    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/", statement_type="CONVENTION")
    append_statement(scope, "prefer British English", statement_type="PREFERENCE")

    with MemoryIndex(scope) as index:
        index.sync()
        conventions, _ = index.search("CONVENTION", 10)
        preferences, _ = index.search("PREFERENCE", 10)

    assert [unit["text"] for unit in conventions] == [
        "CONVENTION: the draft lives in docs/"
    ]
    assert [unit["text"] for unit in preferences] == [
        "PREFERENCE: prefer British English"
    ]


def test_a_write_is_visible_once_the_write_side_has_synced_it(tmp_path: Path) -> None:
    """A search answers what the index holds; the write side keeps it current."""

    scope = tmp_path / "scope"
    read_standing(scope)
    with MemoryIndex(scope) as index:
        first, _ = index.search("quixotic", 10)
    append_statement(scope, "the quixotic release is in tags", statement_type="note")
    # The writer syncs the one file it wrote; a search is not where that happens.
    with MemoryIndex(scope) as index:
        index.sync_file(standing_document(scope))
        second, _ = index.search("quixotic", 10)

    assert first == []
    # The index holds the statement as it is stored, type line included: the type
    # is indexed on purpose, so a query naming the category can find it. A read
    # drops that line on the way out; see read.py.
    assert [unit["text"] for unit in second] == [
        "NOTE: the quixotic release is in tags"
    ]


def test_a_statement_is_visible_once_the_write_side_has_synced_it(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "scope"
    with MemoryIndex(scope) as index:
        index.sync()
    append_statement(scope, "the draft lives in docs/", statement_type="NOTE")
    with MemoryIndex(scope) as index:
        index.sync_file(standing_document(scope))
        found, _ = index.search("docs", 10)
    assert [unit["text"] for unit in found] == ["NOTE: the draft lives in docs/"]


def test_appends_keep_numbering_past_the_head_prefix(tmp_path: Path) -> None:
    """A statement's stamp is where it sits, so appends must not renumber from one.

    A sync used to parse only the new tail when a file had merely grown, which
    numbered that tail from zero: past the 4 KiB head prefix every appended
    statement was stamped '1'. `stamp` is returned in every answer, so the number
    has to be the block's real ordinal.
    """

    scope = tmp_path / "scope"
    read_standing(scope)
    document = standing_document(scope)
    filler = "x" * 400  # enough statements to push the file past the head prefix
    for number in range(20):
        append_statement(scope, f"statement {number} {filler}", statement_type="NOTE")
    with MemoryIndex(scope) as index:
        index.sync_file(document)
        rows = index._open().execute("SELECT stamp, text FROM unit").fetchall()

    stamps = [str(row[0]) for row in rows]
    assert len(rows) == 20
    assert len(set(stamps)) == 20, f"stamps collided: {stamps}"
    assert stamps == sorted(stamps, key=int)


def test_search_answers_what_the_index_holds_until_it_catches_up(
    tmp_path: Path,
) -> None:
    """``search`` is pure: it does not sync, and ``catch_up`` is what a read calls.

    A read now keeps up with a hand edit, so this is the behaviour beneath that:
    a bare search answers what the index holds, ``freshness`` reports the gap, and
    a sync — ``catch_up`` on a read path, or ``reindex`` by hand — closes it. The
    test is here so ``search`` cannot quietly start doing write work.
    """

    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/", statement_type="NOTE")
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
        # catch_up is what a read does; it re-reads the file because it changed.
        index.catch_up()
        current, _ = index.search("draft", 10)

    assert [unit["text"] for unit in stale] == ["NOTE: the draft lives in docs/"]
    assert [unit["text"] for unit in served] == ["NOTE: the draft lives in docs/"]
    assert freshness == {"files_behind": 0}
    assert [unit["text"] for unit in current] == ["NOTE: the draft lives in drafts/"]


def test_a_deleted_document_drops_its_units_on_the_next_pass(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/", statement_type="NOTE")
    with MemoryIndex(scope) as index:
        index.sync()
    standing_document(scope).unlink()
    with MemoryIndex(scope) as index:
        index.sync()
        found, _ = index.search("draft", 10)
    assert found == []


def test_deleting_the_index_rebuilds_it(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/", statement_type="note")
    with MemoryIndex(scope) as index:
        index.sync()
    index_path(scope).unlink()

    with MemoryIndex(scope) as index:
        # Thrown away, and the answer comes back from the files.
        index.sync()
        found, _ = index.search("draft", 10)
    assert [unit["text"] for unit in found] == ["NOTE: the draft lives in docs/"]


def test_rebuild_reports_what_it_holds(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "one", statement_type="note")
    append_statement(scope, "two", statement_type="note")
    state = rebuild(scope)
    # Two statements. Not the template the first read created: a seed is not
    # something anyone wrote, so it cannot answer a query.
    assert state["units"] == 2


def test_one_past_the_limit_is_how_the_index_says_there_was_more(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "scope"
    for index_number in range(4):
        append_statement(scope, f"draft note {index_number}", statement_type="NOTE")
    with MemoryIndex(scope) as index:
        index.sync()
        found, _ = index.search("draft", 2)
    # The search asks the index for one row past the limit, so the caller can say
    # whether the answer was complete.
    assert len(found) == 3


def test_a_query_of_only_punctuation_finds_nothing(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/", statement_type="note")
    with MemoryIndex(scope) as index:
        found, mode = index.search("?! ...", 10)
    assert found == []
    assert mode == "no-terms"


def test_a_query_that_fts5_cannot_parse_is_reported_not_raised(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_statement(scope, "the draft lives in docs/", statement_type="note")
    with MemoryIndex(scope) as index, pytest.raises(Exception) as caught:
        index._match(index._open(), 'a "unbalanced', 10)
    assert "could not be run" in str(caught.value)


def test_the_index_is_not_the_record(tmp_path: Path) -> None:
    """Every unit's text is present in the document it says it came from."""

    scope = tmp_path / "scope"
    append_statement(scope, "always cite the commit", statement_type="RULE")
    append_statement(scope, "the draft lives in docs/", statement_type="NOTE")

    with MemoryIndex(scope) as index:
        state = index.sync()
        found, _ = index.search("draft", 10)

    assert state["units"] == 2
    assert state["files"] == 1
    standing = (scope / "MEMORY.md").read_text(encoding="utf-8")
    for unit in found:
        assert unit["text"].splitlines()[0] in standing
