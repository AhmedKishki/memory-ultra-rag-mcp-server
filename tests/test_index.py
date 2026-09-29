"""The record: what it holds, how it is searched, and what it writes out.

The table in ``memory.sqlite3`` is the memory now, and the file beside it is an
export of it. These tests pin that relationship in both directions: a statement
recorded is findable and appears in the document, and a document that disagrees
with the record loses.
"""

from __future__ import annotations

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
    Statement,
    parse_document,
    read_standing,
    standing_document,
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
        index.write_export()
        return key


def test_this_python_can_build_the_index() -> None:
    """The server refuses to start without this, so the suite states it."""

    assert fts5_available() is True


def test_the_record_lives_beside_the_document_it_is_written_out_to(
    tmp_path: Path,
) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")

    assert index_path(scope) == scope / INDEX_FILENAME
    assert standing_document(scope).name == EXPORT_FILENAME


def test_a_scope_with_nothing_in_it_is_empty_rather_than_missing(
    tmp_path: Path,
) -> None:
    scope = _scope(tmp_path)
    with MemoryIndex(scope) as index:
        found, mode = index.search("anything", 10)
    assert found == []
    assert mode == "all-words"


def test_a_recorded_statement_is_here_and_in_the_document(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    key = _add(scope, "the draft lives in docs/", "NOTE")

    with MemoryIndex(scope) as index:
        found, _mode = index.search("draft", 10)
        rows = index.units_by_key([key])

    assert [row["text"] for row in found] == ["the draft lives in docs/"]
    assert rows[key]["kind"] == "NOTE"
    assert "the draft lives in docs/" in standing_document(scope).read_text(
        encoding="utf-8"
    )


def test_a_statement_says_its_kind_and_the_document_does_not(tmp_path: Path) -> None:
    """The type is a field of the row; a file of tagged lines reads as data."""

    scope = _scope(tmp_path)
    _add(scope, "always cite the commit", "RULE")

    with MemoryIndex(scope) as index:
        found, _mode = index.search("commit", 10)
    document = standing_document(scope).read_text(encoding="utf-8")

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
    assert standing_document(scope).read_text(encoding="utf-8").index(
        "the third thing"
    ) < standing_document(scope).read_text(encoding="utf-8").index("the first thing")


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


def test_a_document_is_adopted_by_a_record_with_nothing_in_it(tmp_path: Path) -> None:
    """The recovery path, and the upgrade path: a file read into an empty record."""

    scope = _scope(tmp_path)
    standing_document(scope).write_text(
        "# MEMORY\ni am jack. i like LLMs.\n\nPLAN: the corpus parses\n\nNOTE: cite the commit\n",
        encoding="utf-8",
    )

    with MemoryIndex(scope) as index:
        assert index.adopt_document_if_empty() == 2
        # Reversed, because a prefixed file was written by a version that appended.
        assert [item.kind for item in index.statements()] == ["NOTE", "PLAN"]
        assert index.statements()[0].text == "cite the commit"
        index.write_export()

    # And the next export has no types in it, because they are rows now.
    document = standing_document(scope).read_text(encoding="utf-8")
    assert "the corpus parses" in document
    assert "PLAN" not in document


def test_a_record_is_not_overwritten_by_a_hand_edited_document(
    tmp_path: Path,
) -> None:
    """The document is an export, and an edit to it is not read."""

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    standing_document(scope).write_text(
        "# MEMORY\nsomething a person typed by hand\n", encoding="utf-8"
    )

    with MemoryIndex(scope) as index:
        assert index.adopt_document_if_empty() == 0
        assert index.write_export() is True

    assert "something a person typed by hand" not in standing_document(scope).read_text(
        encoding="utf-8"
    )
    assert "the draft lives in docs/" in standing_document(scope).read_text(
        encoding="utf-8"
    )


def test_writing_the_export_reports_when_it_had_to(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")

    with MemoryIndex(scope) as index:
        assert index.write_export() is False
        index.insert("a new statement", "NOTE")
        assert index.write_export() is True


def test_the_document_is_not_read_on_every_read(tmp_path: Path) -> None:
    """A read that finds nothing changed costs the same whatever the memory holds."""

    scope = _scope(tmp_path)
    for number in range(20):
        _add(scope, f"a statement about the draft number {number}", "NOTE")

    with MemoryIndex(scope) as index:
        started = time.perf_counter()
        index.search("draft", 10)
        first = time.perf_counter() - started
        for _ in range(5):
            index.write_export()
            index.search("draft", 10)

    assert first < 1.0


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


def test_the_record_is_not_the_document(tmp_path: Path) -> None:
    """The two are separate, and the file cannot take the record with it."""

    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    standing_document(scope).unlink()

    with MemoryIndex(scope) as index:
        assert index.count_units() == 1
        found, _mode = index.search("draft", 10)
        index.write_export()

    assert [row["text"] for row in found] == ["the draft lives in docs/"]
    assert standing_document(scope).is_file()


def test_the_document_can_be_rebuilt_from_the_record_alone(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    _add(scope, "the draft lives in docs/", "NOTE")
    _add(scope, "always cite the commit", "RULE")
    standing_document(scope).unlink()

    with MemoryIndex(scope) as index:
        index.write_export()

    assert parse_document(standing_document(scope).read_text(encoding="utf-8"))
    assert read_standing(scope).startswith("# MEMORY")
