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
    MemoryIndex,
    fts5_available,
    index_path,
)
from memory_ultra_rag_mcp.store import (
    EXPORT_FILENAME,
    SEED,
    Statement,
    parse_document,
    read_document,
    unit_key,
)


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
        assert index.retire_superseded(render=True) == [EXPORT_FILENAME]
    assert not (scope / EXPORT_FILENAME).exists()
    with MemoryIndex(scope) as index:
        assert index.count_units() == 2


def test_a_document_beside_the_record_is_read_for_its_words_and_removed(
    tmp_path: Path,
) -> None:
    """Whatever it holds that the record lacks is kept, and then the file goes.

    A memory used to be two databases and a document, all three read into the
    record. Leaving them behind is what made a scope look like two versions of
    itself at once, so they are read and removed, and the answer says so.
    """

    scope = _scope(tmp_path)
    _add(scope, "a recorded fact", "NOTE")
    (scope / EXPORT_FILENAME).write_text(
        "# MEMORY\n\nNOTE: a statement only the document knew\n", encoding="utf-8"
    )

    with MemoryIndex(scope) as index:
        retired = index.retire_superseded()
        restored = [item.text for item in index.statements()]

    assert retired == [EXPORT_FILENAME]
    assert not (scope / EXPORT_FILENAME).exists()
    assert restored == ["a recorded fact", "a statement only the document knew"]


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
    """Two files holding one statement import it once, and the second file's is
    recognised as already held rather than arriving as a second row."""

    scope = _scope(tmp_path)
    _add(scope, "the same words in both files", "NOTE")
    (scope / EXPORT_FILENAME).write_text(
        "# MEMORY\n\nthe same words in both files\n\nonly the document knows this\n",
        encoding="utf-8",
    )

    with MemoryIndex(scope) as index:
        assert index.retire_superseded() == [EXPORT_FILENAME]
        texts = [item.text for item in index.statements()]

    assert texts == ["the same words in both files", "only the document knows this"]


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


def test_a_record_answers_through_a_document_being_retired(tmp_path: Path) -> None:
    """A document is read for what it holds, then removed, and the record answers."""

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    (scope / EXPORT_FILENAME).write_text(
        "# MEMORY\n\nNOTE: something else entirely\n", encoding="utf-8"
    )

    with MemoryIndex(scope) as index:
        found, _mode = index.search("draft", 10)
        retired = index.retire_superseded()
        statements = [item.text for item in index.statements()]

    # The file is gone, what it held is kept, and the record still answers.
    assert retired == [EXPORT_FILENAME]
    assert not (scope / EXPORT_FILENAME).exists()
    assert statements == ["the draft lives in docs/", "something else entirely"]
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

    assert parse_document(_rendered(scope))
    assert read_document(scope).startswith("# MEMORY")
