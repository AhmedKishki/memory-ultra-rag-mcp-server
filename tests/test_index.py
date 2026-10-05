"""The record: what it holds, how it is searched, and what it writes out.

The table in ``memory.sqlite3`` is the memory now, and the file beside it is an
export of it. These tests pin that relationship in both directions: a statement
recorded is findable and appears in the document, and a document that disagrees
with the record loses.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from memory_ultra_rag_mcp.index import (
    INDEX_FILENAME,
    SUPERSEDED_FILENAMES,
    MemoryIndex,
    fts5_available,
    index_path,
)
from memory_ultra_rag_mcp.store import (
    EXPORT_FILENAME,
    SEED,
    Statement,
    parse_document,
    unit_key,
)
from memory_ultra_rag_mcp.vectors import VectorStore


def _scope(tmp_path: Path) -> Path:
    scope = tmp_path / "scope"
    scope.mkdir()
    return scope


def _add(scope: Path, text: str, kind: str = "NOTE") -> str:
    """Record one statement the way the write path does, through the record."""

    with MemoryIndex(scope) as index:
        key, _replaced = index.insert(text, kind)
        return key


def test_this_python_can_build_the_index() -> None:
    """The server refuses to start without this, so the suite states it."""

    assert fts5_available() is True


def test_the_record_is_the_one_file_a_scope_holds(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")

    assert index_path(scope) == scope / INDEX_FILENAME
    # The rendering is not written for it: a memory is the record and nothing else.
    assert not (scope / EXPORT_FILENAME).exists()


def test_a_scope_with_nothing_in_it_is_empty_rather_than_missing(
    tmp_path: Path,
) -> None:
    scope = _scope(tmp_path)
    with MemoryIndex(scope) as index:
        found, mode = index.search("anything", 10)
    assert found == []
    assert mode == "all-words"


def test_a_recorded_statement_is_here_and_renders(tmp_path: Path) -> None:
    """A statement is a row, and what a person reads is rendered from it."""

    scope = _scope(tmp_path)
    key = _add(scope, "the draft lives in docs/", "NOTE")

    with MemoryIndex(scope) as index:
        found, _mode = index.search("draft", 10)
        rows = index.units_by_key([key])
        rendered = index.export()

    assert [row["text"] for row in found] == ["the draft lives in docs/"]
    assert rows[key]["kind"] == "NOTE"
    assert "the draft lives in docs/" in rendered
    # The rendering is rendered, not written: a memory is the record and a file
    # beside it is something somebody asked for with `--export`.
    assert not (scope / EXPORT_FILENAME).exists()


def test_a_statement_says_its_kind_and_the_document_does_not(tmp_path: Path) -> None:
    """The type is a field of the row; a file of tagged lines reads as data."""

    scope = _scope(tmp_path)
    _add(scope, "always cite the commit", "RULE")

    with MemoryIndex(scope) as index:
        found, _mode = index.search("commit", 10)
    document = _rendered(scope)

    assert found[0]["kind"] == "RULE"
    assert "always cite the commit" in document
    assert "RULE:" not in document


def test_a_category_is_asked_for_rather_than_searched_for(tmp_path: Path) -> None:
    """The type is a column, so it narrows the answer and is not in the words.

    It used to be written into each block as a prefix and indexed with the text,
    so a query naming the category found it. Now the text holds the statement
    alone, and a caller that wants a category says so.
    """

    scope = _scope(tmp_path)
    _add(scope, "the corpus parses PDFs and EPUBs", "PLAN")
    _add(scope, "the archive holds a fixture", "TEST")

    with MemoryIndex(scope) as index:
        by_words, _mode = index.search("PLAN", 10)
        by_type, _mode = index.search("the", 10, kind="TEST")
        by_text, _mode = index.search("fixture", 10)

    # The word is not in the text, so it finds nothing rather than pretending to.
    assert by_words == []
    assert [row["text"] for row in by_type] == ["the archive holds a fixture"]
    assert [row["text"] for row in by_text] == ["the archive holds a fixture"]


def test_a_kind_that_is_its_own_text_is_not_a_second_statement(tmp_path: Path) -> None:
    """The words of a statement and the words of its type are the same row."""

    scope = _scope(tmp_path)
    _add(scope, "RULE: cite the commit", "RULE")

    with MemoryIndex(scope) as index:
        found, _mode = index.search("cite", 10)

    assert len(found) == 1
    assert found[0]["text"] == "RULE: cite the commit"


def test_a_new_statement_goes_to_the_top_and_renumbers_the_rest(
    tmp_path: Path,
) -> None:
    """Newest first, which is the order the document is written out in."""

    scope = _scope(tmp_path)
    _add(scope, "the first thing", "NOTE")
    _add(scope, "the second thing", "NOTE")
    _add(scope, "the third thing", "NOTE")

    with MemoryIndex(scope) as index:
        statements = index.statements()

    assert [item.text for item in statements] == [
        "the third thing",
        "the second thing",
        "the first thing",
    ]
    assert [item.position for item in statements] == [0, 1, 2]
    assert _rendered(scope).index("the third thing") < _rendered(scope).index(
        "the first thing"
    )


def test_recording_the_same_words_twice_is_one_statement(tmp_path: Path) -> None:
    """One statement, recorded twice, is not two statements."""

    scope = _scope(tmp_path)
    first = _add(scope, "the draft lives in docs/", "NOTE")
    second = _add(scope, "the draft lives in docs/", "NOTE")

    with MemoryIndex(scope) as index:
        assert index.count_units() == 1
        assert index.unit_keys() == {first}
    assert first == second


def test_the_same_words_under_two_types_are_two_statements(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "the draft lives in docs/", "PLAN")

    with MemoryIndex(scope) as index:
        assert index.count_units() == 2


def test_a_replacement_of_one_type_removes_the_others(tmp_path: Path) -> None:
    """What a handoff is: the same type, and the previous one goes."""

    scope = _scope(tmp_path)
    _add(scope, "session one: the corpus is a stub", "HANDOFF")
    _add(scope, "the corpus parses", "PLAN")

    with MemoryIndex(scope) as index:
        _key, replaced = index.insert(
            "session two: the corpus parses", "HANDOFF", replace_kind="HANDOFF"
        )

        assert replaced == 1
        assert [item.text for item in index.statements()] == [
            "session two: the corpus parses",
            "the corpus parses",
        ]


def test_forgetting_removes_exactly_one_statement(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "always cite the commit", "RULE")

    with MemoryIndex(scope) as index:
        removed = index.forget("the draft lives in docs/")

        assert [item.text for item in removed] == ["the draft lives in docs/"]
        assert index.count_units() == 1


def test_forgetting_nothing_removes_nothing(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")

    with MemoryIndex(scope) as index:
        assert index.forget("the draft lives somewhere else") == []
        assert index.count_units() == 1


def test_forgetting_an_ambiguous_text_removes_nothing(tmp_path: Path) -> None:
    """One vague call must not lose two statements."""

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "the draft lives in docs/", "PLAN")

    with MemoryIndex(scope) as index:
        assert len(index.forget("the draft lives in docs/")) == 2
        assert index.count_units() == 2


def test_editing_the_record_through_replace_all_keeps_what_did_not_change(
    tmp_path: Path,
) -> None:
    """What a person editing the document means, counted in what changed."""

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "always cite the commit", "RULE")

    with MemoryIndex(scope) as index:
        index.note_recalled(list(index.unit_keys()))
        kept = next(
            item
            for item in index.statements()
            if item.text == "the draft lives in docs/"
        )
        moved = next(
            item for item in index.statements() if item.text == "always cite the commit"
        )
        fresh = "the corpus parses now"

        report = index.replace_all(
            [
                Statement(kind="RULE", text=moved.text, key=moved.key, position=0),
                Statement(kind="NOTE", text=kept.text, key=kept.key, position=1),
                Statement(
                    kind="PLAN",
                    text=fresh,
                    key=unit_key(f"PLAN: {fresh}"),
                    position=2,
                ),
            ]
        )
        after = index.history([kept.key])[kept.key]
        order = [item.text for item in index.statements()]

    assert report["added"] == 1
    assert report["removed"] == 0
    # The statement that did not change keeps its count, whichever position the
    # person put it in, and the order is the one the edit was written in.
    assert after["recalls"] == 1
    assert order == ["always cite the commit", "the draft lives in docs/", fresh]


def test_a_document_is_adopted_by_a_record_with_nothing_in_it(
    tmp_path: Path,
) -> None:
    """The upgrade path, and the recovery path: a file read into an empty record."""

    scope = _scope(tmp_path)
    (scope / EXPORT_FILENAME).write_text(
        f"# MEMORY\n{SEED}\n\nPLAN: the corpus parses\n\nNOTE: cite the commit\n",
        encoding="utf-8",
    )

    with MemoryIndex(scope) as index:
        assert index.adopt_document_if_empty() == 2
        # Reversed, because a prefixed file was written by a version that appended.
        assert [item.kind for item in index.statements()] == ["NOTE", "PLAN"]
        assert index.statements()[0].text == "cite the commit"
        # The record now holds everything the file did, so the file is a rendering
        # the two agree on and it stays.
        assert index.retire_superseded() == []

    rendered = _rendered(scope)
    assert "the corpus parses" in rendered
    assert "PLAN" not in rendered
    assert (scope / EXPORT_FILENAME).exists()

    # Asked to, it goes — and a file that still held a statement would not.
    with MemoryIndex(scope) as index:
        assert index.retire_superseded(retire_render=True) == [EXPORT_FILENAME]
    assert not (scope / EXPORT_FILENAME).exists()
    with MemoryIndex(scope) as index:
        assert index.count_units() == 2


def test_a_rendering_beside_a_live_record_is_left_alone(tmp_path: Path) -> None:
    """A rendering is not a second copy of the memory, and a read leaves it be.

    A rendering is written from the record and nothing re-renders it on a write or
    a forget, so every statement the record no longer holds is missing from it.
    Reading it would put every forgotten statement back, and removing it would
    throw away words the record never had.
    """

    scope = _scope(tmp_path)
    _add(scope, "a recorded fact", "NOTE")
    (scope / EXPORT_FILENAME).write_text(
        "# MEMORY\n\nNOTE: a statement only the document knew\n", encoding="utf-8"
    )

    with MemoryIndex(scope) as index:
        assert index.retire_superseded() == []
        assert [item.text for item in index.statements()] == ["a recorded fact"]
        assert index.retire_superseded(retire_render=True) == [EXPORT_FILENAME]

    assert not (scope / EXPORT_FILENAME).exists()


def test_rendering_is_stable_for_an_unchanged_record(tmp_path: Path) -> None:
    """What a person reads is a rendering, and rendering twice changes nothing."""

    scope = _scope(tmp_path)
    _add(scope, "a fact", "NOTE")

    with MemoryIndex(scope) as index:
        first = index.export()
        second = index.export()
        assert index.export_digest() == index.export_digest()

    assert first == second
    assert first.endswith("a fact\n")


def test_a_read_does_not_depend_on_a_document(tmp_path: Path) -> None:
    """There is no document, so there is nothing for a read to fall behind on."""

    scope = _scope(tmp_path)
    for number in range(20):
        _add(scope, f"a statement about the draft number {number}", "NOTE")

    with MemoryIndex(scope) as index:
        started = time.perf_counter()
        index.search("draft", 10)
        elapsed = time.perf_counter() - started

    assert elapsed < 1.0


def test_a_query_of_only_punctuation_finds_nothing(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    with MemoryIndex(scope) as index:
        found, _mode = index.search("--- ...", 10)
    assert found == []


def test_a_query_that_fts5_cannot_parse_is_reported_not_raised(
    tmp_path: Path,
) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")

    with MemoryIndex(scope) as index, pytest.raises(Exception) as caught:
        index._match(index._open(), 'a "unbalanced', 10)

    assert "could not be run" in str(caught.value)


def test_one_past_the_limit_is_how_the_index_says_there_was_more(
    tmp_path: Path,
) -> None:
    scope = _scope(tmp_path)
    for number in range(5):
        _add(scope, f"a note {number} about the draft", "NOTE")

    with MemoryIndex(scope) as index:
        found, _mode = index.search("draft", 2)

    # The search asks for one row past the limit, so the caller can say whether
    # the answer was the whole match or a selection of it.
    assert len(found) == 3


def test_a_document_holding_one_statement_twice_stores_it_once(tmp_path: Path) -> None:
    """A file written by hand repeats itself, and a repeat is not two statements.

    ``unit`` is an FTS5 table, which has no constraint for ``INSERT OR IGNORE`` to
    ignore against, so the check has to be made while the statements are read in.
    """

    scope = _scope(tmp_path)
    (scope / EXPORT_FILENAME).write_text(
        "# MEMORY\n\nprefers tabs over spaces\n\nprefers tabs over spaces\n",
        encoding="utf-8",
    )

    with MemoryIndex(scope) as index:
        assert index.adopt_document_if_empty() == 1
        assert index.count_units() == 1
        assert index.export().count("prefers tabs over spaces") == 1


def test_saving_a_document_unchanged_keeps_every_statement_as_it_was(
    tmp_path: Path,
) -> None:
    """The page's own rendering must not re-file what it was handed.

    A rendering carries no kinds, so the statements the record filed under `NOTE`
    and `RULE` come back as plain prose and are parsed as `ITEM`. A save that took
    that at face value would turn every statement in the memory into `ITEM` and
    reset every date and count in it, without the person having changed a word —
    and the diff would be empty, because the file they were editing never held a
    kind in the first place.
    """

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "always cite the commit", "RULE")

    with MemoryIndex(scope) as index:
        index.note_recalled(list(index.unit_keys()))
        before = {
            item.text: (item.kind, item.added_at, item.recalls)
            for item in index.statements()
        }
        rendered = index.export()

        report = index.replace_all(parse_document(rendered))

        after = {
            item.text: (item.kind, item.added_at, item.recalls)
            for item in index.statements()
        }

    assert report == {"kept": 2, "added": 0, "removed": 0, "vectors_removed": 0}
    assert after == before


def test_a_kind_the_document_names_is_honoured_over_the_one_the_record_holds(
    tmp_path: Path,
) -> None:
    """Preserving the record's kind is for a document that says nothing.

    A rendering carries no kind, so the record's own is the truth about the
    statement. A document written with `KIND: ` prefixes is a person naming the
    kind, and that is the one the record takes.
    """

    scope = _scope(tmp_path)
    _add(scope, "cite the commit", "NOTE")

    with MemoryIndex(scope) as index:
        report = index.replace_all(
            parse_document("# MEMORY\n\nRULE: cite the commit\n")
        )

        assert report["kept"] == 0
        assert report["added"] == 1
        assert report["removed"] == 1
        assert [item.kind for item in index.statements()] == ["RULE"]


def test_a_document_holding_one_statement_twice_is_edited_into_one_row(
    tmp_path: Path,
) -> None:
    """A repeated line in the editor is one statement, and the counts say so.

    The count is what a page shows after a save, so a document holding two lines of
    one statement must not report two statements added: the row is written once,
    and the numbers have to describe the record rather than the text that arrived.
    """

    scope = _scope(tmp_path)
    _add(scope, "prefers tabs over spaces", "NOTE")

    with MemoryIndex(scope) as index:
        report = index.replace_all(
            parse_document(
                "# MEMORY\n\nprefers tabs over spaces\n\nprefers tabs over spaces\n"
            )
        )

        assert index.count_units() == 1
        assert report["kept"] == 1
        assert report["added"] == 0
        assert index.export().count("prefers tabs over spaces") == 1


def test_a_vector_file_this_version_cannot_read_is_left_where_it_is(
    tmp_path: Path,
) -> None:
    """An unreadable file is not deleted on a guess: it holds the only copies.

    A version-4 vector file holds embeddings this version cannot rebuild itself,
    and the upgrade that cannot read it is exactly the case where removing it
    would destroy them.
    """

    scope = _scope(tmp_path)
    legacy = scope / "index-vectors.sqlite3"
    connection = sqlite3.connect(legacy)
    connection.execute("CREATE TABLE something_else(x INTEGER)")
    connection.commit()
    connection.close()

    with MemoryIndex(scope) as index:
        index.insert("a fact", "NOTE")

    assert legacy.is_file()


def test_a_vector_file_this_version_can_read_is_removed(tmp_path: Path) -> None:
    """The upgrade still does what it is for: copy the vectors, then let it go."""

    scope = _scope(tmp_path)
    legacy = scope / "index-vectors.sqlite3"
    connection = sqlite3.connect(legacy)
    connection.execute(
        "CREATE TABLE vector(unit_key TEXT, stamp TEXT, kind TEXT, model TEXT, "
        "dimension INTEGER, components BLOB)"
    )
    connection.execute(
        "INSERT INTO vector VALUES ('key', '0', 'NOTE', 'fake/model', 4, ?)",
        (sqlite3.Binary(b"\x00\x00\x00\x00" * 4),),
    )
    connection.commit()
    connection.close()

    with MemoryIndex(scope) as index:
        index.insert("a fact", "NOTE")
        moved = index.migrated_vectors

    assert moved == 1
    assert not legacy.exists()


def test_a_retired_file_does_not_import_a_statement_the_record_already_holds(
    tmp_path: Path,
) -> None:
    """A statement the record already holds is recognised, not imported again."""

    scope = _scope(tmp_path)
    words = "the same words in both files"
    _add(scope, words, "NOTE")
    stale = scope / SUPERSEDED_FILENAMES[0]
    only_the_file = "only the old index knows this"
    with sqlite3.connect(stale) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE unit USING fts5("
            "text, unit_key UNINDEXED, type UNINDEXED, stamp UNINDEXED)"
        )
        connection.executemany(
            "INSERT INTO unit(text, unit_key, type, stamp) VALUES (?, ?, ?, ?)",
            [
                (words, unit_key(f"NOTE: {words}"), "NOTE", "0"),
                (only_the_file, unit_key(f"PLAN: {only_the_file}"), "PLAN", "1"),
            ],
        )
        connection.commit()

    with MemoryIndex(scope) as index:
        assert index.retire_superseded() == [SUPERSEDED_FILENAMES[0]]
        texts = [item.text for item in index.statements()]

    assert texts == [words, only_the_file]


def test_a_statement_imported_from_a_retired_file_keeps_its_identity(
    tmp_path: Path,
) -> None:
    """An imported statement is the statement the old file described.

    Identity is the words *and* the kind, and a retired database carries both, so
    its own key is the one the record takes. Recomputing a digest from the words
    alone produced a key no write would ever produce again, and recording those
    words a second time then arrived as a second row of one statement: a recall
    answered it twice and a forget refused it as ambiguous.
    """

    scope = _scope(tmp_path)
    _add(scope, "a statement the record already holds", "NOTE")
    stale = scope / SUPERSEDED_FILENAMES[0]
    words = "a statement only the old index knows"
    with sqlite3.connect(stale) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE unit USING fts5("
            "text, unit_key UNINDEXED, type UNINDEXED, stamp UNINDEXED)"
        )
        connection.execute(
            "INSERT INTO unit(text, unit_key, type, stamp) VALUES (?, ?, ?, ?)",
            (words, unit_key(f"PLAN: {words}"), "PLAN", "9"),
        )
        connection.commit()

    with MemoryIndex(scope) as index:
        assert index.retire_superseded() == [SUPERSEDED_FILENAMES[0]]
        assert index.unit_keys() == {
            unit_key("NOTE: a statement the record already holds"),
            unit_key(f"PLAN: {words}"),
        }

        index.insert(words, "PLAN")

        assert index.count_units() == 2
        assert [item.text for item in index.statements()].count(words) == 1


def test_a_retired_file_of_vectors_is_not_read_as_statements(tmp_path: Path) -> None:
    """A vector row is a digest, and a digest is not a statement.

    A file of the superseded name could be a vector table rather than a word index.
    Reading its rows as statements would file a 64-character hex string in the
    memory as though a person had written it, so a file with no words in it is
    read as holding nothing.
    """

    scope = _scope(tmp_path)
    _add(scope, "a statement the record already holds", "NOTE")
    stale = scope / SUPERSEDED_FILENAMES[0]
    with sqlite3.connect(stale) as connection:
        connection.execute(
            "CREATE TABLE vector(unit_key TEXT, stamp TEXT, kind TEXT, model TEXT, "
            "dimension INTEGER, components BLOB)"
        )
        connection.execute(
            "INSERT INTO vector VALUES (?, ?, ?, ?, ?, ?)",
            (
                unit_key("PLAN: a statement only the old index holds"),
                "0",
                "PLAN",
                "fake/model",
                4,
                sqlite3.Binary(b"\x00\x00\x00\x00" * 4),
            ),
        )
        connection.commit()

    with MemoryIndex(scope) as index:
        index.retire_superseded()

        assert [item.text for item in index.statements()] == [
            "a statement the record already holds"
        ]


def test_a_retired_file_lacking_an_identity_column_is_read_by_position(
    tmp_path: Path,
) -> None:
    """A file that is missing a column has nothing in it, not a shifted column.

    The rows are read by position, so a file of the shape `text, type, stamp` used
    to put the stamp where the kind belongs and filed the statement under its own
    place in the document. Every row comes back in one shape now, whatever the file
    holds, and the statement keeps the kind the file gave it.
    """

    scope = _scope(tmp_path)
    _add(scope, "a statement the record already holds", "NOTE")
    stale = scope / SUPERSEDED_FILENAMES[0]
    words = "a statement from a file with no identity column"
    with sqlite3.connect(stale) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE unit USING fts5(text, type UNINDEXED, stamp UNINDEXED)"
        )
        connection.execute(
            "INSERT INTO unit(text, type, stamp) VALUES (?, ?, ?)", (words, "NOTE", "4")
        )
        connection.commit()

    with MemoryIndex(scope) as index:
        assert index.retire_superseded() == [SUPERSEDED_FILENAMES[0]]
        imported = next(item for item in index.statements() if item.text == words)

        assert imported.kind == "NOTE"
        assert imported.key == unit_key(f"NOTE: {words}")

        index.insert(words, "NOTE")

        assert index.count_units() == 2


def test_an_emptied_record_is_not_read_again_from_the_rendering(
    tmp_path: Path,
) -> None:
    """An empty record and an uninitialised one are different states.

    A record whose last statement was forgotten is empty and authoritative. A
    rendering beside it still holds those words, because nothing re-renders it after
    a forget, so re-reading it would put back what was just removed — and the record
    file is what tells the two states apart, since nothing else is written down.
    """

    scope = _scope(tmp_path)
    _add(scope, "a doomed fact", "NOTE")
    with MemoryIndex(scope) as index:
        index.write_render()
        index.drop(unit_key("NOTE: a doomed fact"))

    with MemoryIndex(scope) as index:
        assert index.count_units() == 0
        assert index.adopt_document_if_empty() == 0
        assert [item.text for item in index.statements()] == []

    assert (scope / EXPORT_FILENAME).is_file()


def test_a_memory_with_no_record_yet_is_recovered_from_its_file(
    tmp_path: Path,
) -> None:
    """First initialisation still reads the file beside it, and makes it durable."""

    scope = _scope(tmp_path)
    (scope / EXPORT_FILENAME).write_text(
        "# MEMORY\n\nthe draft lives in docs/\n\nalways cite the commit\n",
        encoding="utf-8",
    )

    with MemoryIndex(scope) as index:
        assert index.adopt_document_if_empty() == 2

    # A second process, with nothing of the first one's connection left.
    with MemoryIndex(scope) as index:
        assert [item.text for item in index.statements()] == [
            "the draft lives in docs/",
            "always cite the commit",
        ]
        assert index.adopt_document_if_empty() == 0


def test_a_commit_that_fails_leaves_the_source_file_alone(tmp_path: Path) -> None:
    """The rows are committed before the file is removed, not after.

    SQLite holds a write until something commits it, and closing throws the rest
    away. A commit wrapped in a handler would swallow the failure and unlink the
    file anyway, so the only copy of those statements would be gone and nothing
    would say so.
    """

    scope = _scope(tmp_path)
    words = "a statement only the old index knows"
    stale = scope / SUPERSEDED_FILENAMES[0]
    with sqlite3.connect(stale) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE unit USING fts5("
            "text, unit_key UNINDEXED, type UNINDEXED, stamp UNINDEXED)"
        )
        connection.execute(
            "INSERT INTO unit(text, unit_key, type, stamp) VALUES (?, ?, ?, ?)",
            (words, unit_key(f"PLAN: {words}"), "PLAN", "0"),
        )
        connection.commit()

    with (
        pytest.raises(sqlite3.DatabaseError),
        MemoryIndex(scope) as index,
        refusing_commits(index),
    ):
        index.retire_superseded()

    # The rows went back with the exception, and the only copy of those statements
    # is still there for the next read to import.
    assert stale.is_file()
    with MemoryIndex(scope) as reopened:
        assert reopened.count_units() == 0
        assert reopened.retire_superseded() == [SUPERSEDED_FILENAMES[0]]
        assert reopened.count_units() == 1


def test_a_commit_that_fails_on_close_is_raised(tmp_path: Path) -> None:
    """A write that cannot be made durable is reported, not discarded quietly."""

    scope = _scope(tmp_path)
    index = MemoryIndex(scope)
    index.insert("a fact", "NOTE")

    with refusing_commits(index), pytest.raises(sqlite3.DatabaseError):
        index.close()

        assert index._connection is None


def test_an_operation_that_raises_is_rolled_back_and_the_file_closed(
    tmp_path: Path,
) -> None:
    """A half-finished operation leaves nothing behind, and nothing locked."""

    scope = _scope(tmp_path)
    _add(scope, "a recorded fact", "NOTE")

    with pytest.raises(RuntimeError), MemoryIndex(scope) as index:
        # An uncommitted write, which is the state a failed operation leaves.
        index._open().execute(
            "INSERT INTO unit(text, unit_key, kind, stamp) VALUES (?, ?, ?, ?)",
            ("half-finished", unit_key("NOTE: half-finished"), "NOTE", "0"),
        )
        raise RuntimeError("the operation did not finish")

    with MemoryIndex(scope) as index:
        assert [item.text for item in index.statements()] == ["a recorded fact"]


def test_a_write_is_durable_once_the_block_that_made_it_is_closed(
    tmp_path: Path,
) -> None:
    """Closing without a commit throws the write away, so close commits."""

    scope = _scope(tmp_path)
    index = MemoryIndex(scope)
    index.insert("a fact", "NOTE")
    index.close()

    with MemoryIndex(scope) as reopened:
        assert [item.text for item in reopened.statements()] == ["a fact"]


class _RefusingCommit:
    """One connection whose commits fail, the way a full disk makes them fail."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)

    def commit(self) -> None:
        raise sqlite3.DatabaseError("database or disk is full")


class refusing_commits:
    """Stand a failing connection in front of one index for the length of a block.

    SQLite's connection object refuses attribute assignment, so the failure is
    injected by wrapping it rather than by patching the method on it.
    """

    def __init__(self, index: MemoryIndex) -> None:
        self.index = index
        self.real = index._open()

    def __enter__(self) -> MemoryIndex:
        self.index._connection = _RefusingCommit(self.real)  # type: ignore[assignment]
        return self.index

    def __exit__(self, *_: object) -> None:
        self.index._connection = self.real  # type: ignore[assignment]


def test_a_rebuild_keeps_the_statements_the_history_and_the_vectors(
    tmp_path: Path,
) -> None:
    """A rebuild rebuilds the index over the words, which is the only derived part.

    The record is the file, and the file holds the words, the kinds, the positions,
    the dates, the counts and the vectors. Dropping it to rebuild would lose the
    memory rather than recover it, and nothing beside it could put it back.
    """

    from memory_ultra_rag_mcp.index import rebuild

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "always cite the commit", "RULE")
    with MemoryIndex(scope) as index:
        index.note_recalled(list(index.unit_keys()))
        before = [
            (item.text, item.kind, item.added_at, item.recalls)
            for item in index.statements()
        ]
        key = index.unit_keys().pop()
    with VectorStore(scope, "fake/model", 4) as store:
        store.put(key, (0.1, 0.2, 0.3, 0.4), stamp="0", kind="NOTE")

    assert rebuild(scope) == {"units": 2}

    with MemoryIndex(scope) as index:
        after = [
            (item.text, item.kind, item.added_at, item.recalls)
            for item in index.statements()
        ]
        found, _mode = index.search("draft commit", 10)
        assert [item.text for item in index.statements()] == [
            text for text, *_rest in after
        ]
    with VectorStore(scope, "fake/model", 4) as store:
        assert store.count() == 1
    assert after == before
    assert {row["text"] for row in found} == {
        "the draft lives in docs/",
        "always cite the commit",
    }


def test_a_search_falls_back_when_the_conjunction_is_too_strict(
    tmp_path: Path,
) -> None:
    """A query whose every term is required would otherwise answer with nothing."""

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "the archive holds a fixture", "NOTE")

    with MemoryIndex(scope) as index:
        all_words, mode = index.search("draft archive", 10)

    # Nothing holds both words, so the rarer ones are tried alone rather than the
    # query answering with nothing.
    assert mode == "rarest-words"
    assert all_words != []


def test_a_record_answers_through_a_retired_file(tmp_path: Path) -> None:
    """A database is read and removed, and the record answers the same either way."""

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    only_the_file = "something only the old file knew"
    stale = scope / SUPERSEDED_FILENAMES[0]
    with sqlite3.connect(stale) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE unit USING fts5("
            "text, unit_key UNINDEXED, type UNINDEXED, stamp UNINDEXED)"
        )
        connection.execute(
            "INSERT INTO unit(text, unit_key, type, stamp) VALUES (?, ?, ?, ?)",
            (only_the_file, unit_key(f"PLAN: {only_the_file}"), "PLAN", "9"),
        )
        connection.commit()

    with MemoryIndex(scope) as index:
        found, _mode = index.search("draft", 10)
        retired = index.retire_superseded()
        statements = [item.text for item in index.statements()]

    assert retired == [SUPERSEDED_FILENAMES[0]]
    assert not stale.exists()
    assert statements == ["the draft lives in docs/", only_the_file]
    assert [row["text"] for row in found] == ["the draft lives in docs/"]


def test_a_record_renders_itself_with_nothing_else(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "always cite the commit", "RULE")

    with MemoryIndex(scope) as index:
        rendered = index.export()

    assert parse_document(rendered, newest_first=True)
    assert rendered.index("always cite the commit") < rendered.index(
        "the draft lives in docs/"
    )


def _rendered(scope: Path) -> str:
    """Return the document a scope's record renders as, which is what is read."""

    with MemoryIndex(scope) as index:
        return index.export()
