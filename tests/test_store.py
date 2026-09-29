"""What a statement is: the shape it takes in a row, and the two shapes a file takes.

The record is a table and a memory is its rows, so most of what this module has to
get right is the words: a kind is one run of block letters, an identity is a
statement's own words, and a document is a rendering of a record and a way back
into one. Nothing here writes a file, because nothing in this server does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_ultra_rag_mcp.store import (
    DEFAULT_KIND,
    EXPORT_FILENAME,
    HANDOFF_KIND,
    MAX_KIND_LENGTH,
    SEED,
    STOPWORDS,
    TEMPLATE,
    Statement,
    StoreError,
    normalise,
    parse_document,
    query_terms,
    read_document,
    render_document,
    statement_kind,
    unit_key,
)


def _statement(text: str, kind: str = "NOTE", position: int = 0) -> Statement:
    return Statement(
        kind=kind, text=text, key=unit_key(f"{kind}: {text}"), position=position
    )


def test_the_file_a_previous_version_wrote_is_named_as_it_named_it() -> None:
    """The migration reads exactly the file an earlier version left."""

    assert EXPORT_FILENAME == "MEMORY.md"


def test_upstreams_seed_is_recognised_and_never_a_statement() -> None:
    """A document this server reads may hold the line upstream seeds, and it is not
    something anyone remembered."""

    parsed = parse_document(TEMPLATE)

    assert parsed == []
    assert SEED in TEMPLATE
    assert (
        parse_document(f"# MEMORY\n{SEED}\n\na real note\n")[-1].text == "a real note"
    )


def test_reading_a_document_that_is_not_there_is_nothing() -> None:
    assert read_document(Path("/nowhere/at/all")) is None


def test_a_document_is_read_when_it_is_there(tmp_path: Path) -> None:
    (tmp_path / EXPORT_FILENAME).write_text("# MEMORY\n\na fact\n", encoding="utf-8")

    assert read_document(tmp_path) == "# MEMORY\n\na fact\n"


def test_a_record_renders_as_plain_prose_newest_first() -> None:
    """A rendering is what a person reads: no kind in the text, no seed, no tags."""

    document = render_document(
        [
            _statement("the decision was to pin the model", "PLAN", 0),
            _statement("the draft lives in docs/", "NOTE", 1),
        ]
    )

    assert "PLAN" not in document
    assert "NOTE" not in document
    assert SEED not in document
    assert document.startswith("# MEMORY\n")
    assert document.index("the decision was to pin") < document.index(
        "the draft lives in docs/"
    )


def test_a_record_with_nothing_in_it_renders_as_a_heading() -> None:
    assert render_document([]) == "# MEMORY\n"


def test_a_document_written_by_an_older_version_is_read_into_statements() -> None:
    """A prefixed block carries its kind, and the words after it are the statement."""

    document = (
        f"# MEMORY\n{SEED}\n\nPLAN: the decision was to pin the model\n\n"
        "NOTE: the draft lives in docs/\n\na line someone typed with no kind\n"
    )

    parsed = parse_document(document)

    assert [item.kind for item in parsed] == ["ITEM", "NOTE", "PLAN"]
    assert [item.text for item in parsed] == [
        "a line someone typed with no kind",
        "the draft lives in docs/",
        "the decision was to pin the model",
    ]


def test_a_prefixed_document_is_read_in_reverse() -> None:
    """A file an older version appended has its newest statement last, so importing
    it in file order would file the oldest as the most recent."""

    document = "RULE: the oldest note\n\nPLAN: the newest plan\n"

    auto = parse_document(document)
    assert [item.position for item in auto] == [0, 1]
    assert auto[0].text == "the newest plan"
    # And a caller that knows the order is not reversed behind its back.
    assert parse_document(document, newest_first=True)[0].text == "the oldest note"


def test_only_a_leading_kind_is_a_kind() -> None:
    """A sentence that opens with one and a capital is a sentence."""

    parsed = parse_document(
        "RULE: Cite: the commit hash\n\nRULE: Note: it failed again",
        newest_first=True,
    )

    assert [item.text for item in parsed] == [
        "Cite: the commit hash",
        "Note: it failed again",
    ]


def test_a_kind_is_never_written_into_a_rendering() -> None:
    """What the parser takes out is what the renderer leaves out."""

    parsed = parse_document("RULE: the draft lives in docs/", newest_first=True)

    assert parsed[0].kind == "RULE"
    assert "RULE" not in render_document(parsed)


def test_the_heading_and_the_seed_are_not_statements() -> None:
    parsed = parse_document(f"# MEMORY\n\n{SEED}\n\n# Not a title\n\na fact\n")

    assert [item.text for item in parsed] == ["# Not a title", "a fact"]


def test_a_kind_is_the_callers_own_and_is_not_interpreted() -> None:
    for name in ("RULE", "PLAN", "PREFERENCE", "HANDOFF", "DECISION"):
        assert statement_kind(name) == name


def test_a_kind_is_written_in_block_letters_whatever_case_it_arrives_in() -> None:
    assert statement_kind("rule") == "RULE"
    assert statement_kind("Rule") == "RULE"


def test_a_missing_kind_becomes_item() -> None:
    """Every statement carries a kind, so a caller that names none gets a real one."""

    for empty in (None, "", "   "):
        assert statement_kind(empty) == DEFAULT_KIND


def test_white_space_in_a_kind_is_refused() -> None:
    with pytest.raises(StoreError, match="no white space"):
        statement_kind("two words")


def test_anything_that_is_not_block_letters_is_refused() -> None:
    with pytest.raises(StoreError, match="block letters"):
        statement_kind("rule-1")
    with pytest.raises(StoreError, match="block letters"):
        statement_kind("règle")


def test_an_oversized_kind_is_refused() -> None:
    with pytest.raises(StoreError, match=f"at most {MAX_KIND_LENGTH} characters"):
        statement_kind("X" * (MAX_KIND_LENGTH + 1))


def test_the_handoff_kind_is_reserved() -> None:
    """A handoff is a statement under a kind the server owns, and the handoff tools
    remove the previous one by it."""

    assert statement_kind("handoff") == HANDOFF_KIND


def test_a_statements_identity_is_its_words_and_its_kind() -> None:
    assert unit_key("RULE: a fact") != unit_key("PLAN: a fact")
    # And the same words, whatever the spacing, are one statement.
    assert unit_key("RULE: a  fact\n") == unit_key("RULE: a fact")


def test_normalising_collapses_spacing_and_nothing_else() -> None:
    """Forgiving about spacing, and about nothing that carries meaning."""

    assert normalise("  a fact\n\nover two lines ") == "a fact over two lines"
    assert normalise("a fact") != normalise("another fact")


def test_a_statement_carries_its_own_record() -> None:
    statement = Statement(
        kind="RULE",
        text="a fact",
        key=unit_key("RULE: a fact"),
        position=0,
        added_at="2026-09-29T00:00:00.000+00:00",
        recalls=3,
        last_recalled_at="2026-09-29T01:00:00.000+00:00",
    )

    assert statement.normalized == "a fact"
    assert statement.key == unit_key("RULE: a fact")
    assert statement.recalls == 3


def test_a_stop_word_only_query_keeps_its_words() -> None:
    """Dropping every term would report an empty memory rather than an odd question."""

    assert query_terms("the a of is it") == ("the", "a", "of", "is", "it")


def test_a_query_searches_for_the_words_that_mean_something() -> None:
    assert query_terms("What is research-rag for?") == ("research-rag",)
    assert query_terms("why keep durable information?") == (
        "why",
        "keep",
        "durable",
        "information",
    )
    assert "the" in STOPWORDS
    # The order terms are given in is the order they are tried in.
    assert query_terms("draft commit") == ("draft", "commit")
