"""The document beside the record, and the words of a statement.

The document is an export: written from the record, read by nobody, and regenerated
rather than parsed as an input. What is under test here is the shape of that export,
the parsing of a file written when the document *was* the record, and the two
normalisations a statement goes through before it is keyed or matched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_ultra_rag_mcp.store import (
    EXPORT_FILENAME,
    STOPWORDS,
    TEMPLATE,
    Statement,
    StoreError,
    normalise,
    parse_document,
    query_terms,
    read_standing,
    render_document,
    standing_document,
    statement_kind,
    unit_key,
    write_standing,
)


def _statement(text: str, kind: str = "NOTE", position: int = 0) -> Statement:
    return Statement(
        kind=kind,
        text=text,
        key=unit_key(f"{kind}: {text}"),
        position=position,
    )


def test_the_first_read_creates_the_document(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    assert read_standing(scope) == TEMPLATE
    assert standing_document(scope).read_text(encoding="utf-8") == TEMPLATE
    # A second read returns what is there rather than rewriting it.
    standing_document(scope).write_text("# MEMORY\nmine\n", encoding="utf-8")
    assert read_standing(scope) == "# MEMORY\nmine\n"


def test_the_document_keeps_upstreams_file_name(tmp_path: Path) -> None:
    """Upstream's own name, kept: a UltraRAG UI looks for exactly this path."""

    scope = tmp_path / "scope"
    read_standing(scope)
    assert standing_document(scope).name == EXPORT_FILENAME == "MEMORY.md"
    assert standing_document(scope).parent == scope


def test_a_scope_is_one_document_and_one_file(tmp_path: Path) -> None:
    """No dated file is created, and none is looked for."""

    scope = tmp_path / "scope"
    read_standing(scope)
    assert sorted(path.name for path in scope.rglob("*")) == ["MEMORY.md"]


def test_an_empty_record_is_written_out_as_a_heading(tmp_path: Path) -> None:
    """Nothing remembered is a heading and nothing else: no seed, no type, no text."""

    assert render_document([]) == "# MEMORY\n"


def test_the_export_is_plain_prose_with_no_type_in_it() -> None:
    """A file of tagged lines reads as data; a memory should read as memory."""

    document = render_document(
        [
            _statement("the decision was to pin the model", "PLAN", 0),
            _statement("the draft lives in docs/", "NOTE", 1),
        ]
    )

    assert "PLAN" not in document
    assert "NOTE" not in document
    assert "the decision was to pin the model" in document
    # Newest first, which is the order they are recorded in.
    assert document.index("the decision was to pin") < document.index(
        "the draft lives in docs/"
    )
    assert document.startswith("# MEMORY\n")
    # And nothing that is not remembered: upstream's seed is not a memory.
    assert "i am jack" not in document


def test_a_document_written_by_an_older_version_is_parsed_into_statements() -> None:
    """The upgrade path: a file with types in it becomes rows with types on them."""

    document = (
        "# MEMORY\ni am jack. i like LLMs.\n\n"
        "PLAN: the decision was to pin the model\n\n"
        "NOTE: the draft lives in docs/\n\n"
        "a line someone typed with no type at all\n"
    )

    parsed = parse_document(document)

    # A prefixed file was written by a version that appended, so it is imported
    # in reverse: the last statement in it was its newest.
    assert [item.kind for item in parsed] == ["ITEM", "NOTE", "PLAN"]
    assert [item.text for item in parsed] == [
        "a line someone typed with no type at all",
        "the draft lives in docs/",
        "the decision was to pin the model",
    ]
    # The heading and the seed are not statements.
    assert "i am jack" not in [item.text for item in parsed]


def test_a_statement_whose_own_words_look_like_a_kind_keeps_them() -> None:
    """Only a leading kind is a kind; a sentence opening with one is a sentence."""

    parsed = parse_document(
        "RULE: Cite: the commit hash\n\nRULE: Note: it failed again",
        newest_first=True,
    )

    assert [item.text for item in parsed] == [
        "Cite: the commit hash",
        "Note: it failed again",
    ]


def test_a_kind_is_not_written_into_the_file_again() -> None:
    """What the parser takes out is what the renderer leaves out."""

    parsed = parse_document("RULE: the draft lives in docs/", newest_first=True)

    assert parsed[0].kind == "RULE"
    assert "RULE" not in render_document(parsed)


def test_the_identity_of_a_statement_is_its_words_and_its_kind() -> None:
    """The same words filed under two kinds are two statements."""

    assert unit_key("RULE: a fact") != unit_key("PLAN: a fact")
    # And the same words, whatever the spacing around them, are one statement.
    assert unit_key("RULE: a  fact\n") == unit_key("RULE: a fact")


def test_normalising_collapses_spacing_and_nothing_else() -> None:
    """Forgiving about spacing, and about nothing that carries meaning."""

    assert normalise("  a fact\n\nover two lines ") == "a fact over two lines"
    assert normalise("a fact") != normalise("another fact")


def test_a_type_is_the_callers_own_and_is_not_interpreted() -> None:
    for name in ("RULE", "NOTE", "THING", "PLAN"):
        assert statement_kind(name) == name


def test_a_label_is_written_in_block_letters_whatever_case_it_arrives_in() -> None:
    assert statement_kind("rule") == "RULE"
    assert statement_kind("Rule") == "RULE"


def test_a_missing_type_becomes_item() -> None:
    """Every statement carries a type, so a caller that names none gets a real one."""

    for empty in (None, "", "   "):
        assert statement_kind(empty) == "ITEM"


def test_white_space_in_a_type_is_refused() -> None:
    with pytest.raises(StoreError, match="no white space"):
        statement_kind("two words")


def test_anything_that_is_not_block_letters_is_refused() -> None:
    with pytest.raises(StoreError, match="block letters"):
        statement_kind("rule-1")
    with pytest.raises(StoreError, match="block letters"):
        statement_kind("règle")


def test_an_oversized_type_is_refused() -> None:
    with pytest.raises(StoreError, match="at most 64 characters"):
        statement_kind("X" * 65)


def test_a_stop_word_only_query_keeps_its_words() -> None:
    """Dropping every term would report an empty memory rather than an odd question."""

    assert query_terms("the a of is it") == ("the", "a", "of", "is", "it")


def test_a_query_searches_for_the_words_that_mean_something() -> None:
    """The word side requires every term it is given, so a stop word only loses it."""

    assert query_terms("What is research-rag for?") == ("research-rag",)
    assert query_terms("why keep durable information?") == (
        "why",
        "keep",
        "durable",
        "information",
    )
    assert "the" in STOPWORDS
    # The order terms are given in is the order they are tried in, so the order
    # the caller writes them in is what the fallback follows.
    assert query_terms("draft commit") == ("draft", "commit")


def test_writing_the_document_replaces_it_and_leaves_no_temporary(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "scope"
    read_standing(scope)

    digest = write_standing(scope, "# MEMORY\n\na fact\n")

    assert (
        standing_document(scope).read_text(encoding="utf-8") == "# MEMORY\n\na fact\n"
    )
    assert len(digest) == 64
    assert [path.name for path in scope.rglob("*")] == ["MEMORY.md"]


def test_a_statement_under_the_heading_is_read_even_without_a_blank_line() -> None:
    """A page's own text puts the statement on the line under the title."""

    parsed = parse_document("# MEMORY\nRemember the thesis deadline.\n")

    assert [item.text for item in parsed] == ["Remember the thesis deadline."]
    assert [item.kind for item in parsed] == ["ITEM"]


def test_a_heading_among_blocks_is_not_a_statement() -> None:
    parsed = parse_document("# MEMORY\n\na fact\n\n# Not a title\n\nanother fact\n")

    assert [item.text for item in parsed] == ["a fact", "# Not a title", "another fact"]
