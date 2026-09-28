"""One scope's memory: a standing document in upstream's format, and nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_ultra_rag_mcp.store import (
    STOPWORDS,
    TEMPLATE,
    StoreError,
    append_statement,
    query_terms,
    read_standing,
    standing_document,
    write_standing,
)


def test_the_first_read_creates_the_standing_document(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    assert read_standing(scope) == TEMPLATE
    assert standing_document(scope).read_text(encoding="utf-8") == TEMPLATE
    # A second read returns what is there rather than rewriting it.
    standing_document(scope).write_text("# MEMORY\nmine\n", encoding="utf-8")
    assert read_standing(scope) == "# MEMORY\nmine\n"


def test_a_scope_is_one_document_and_writes_no_dated_file(tmp_path: Path) -> None:
    """A memory here is statements, so nothing creates a dated exchange log."""

    scope = tmp_path / "scope"
    append_statement(scope, "a fact", statement_type="NOTE")

    assert sorted(path.name for path in scope.rglob("*")) == ["MEMORY.md"]


def test_two_scopes_keep_two_memories(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    append_statement(first, "mine", statement_type="NOTE")

    assert read_standing(first).startswith(TEMPLATE)
    assert "mine" in read_standing(first)
    assert read_standing(second) == TEMPLATE
    assert not standing_document(second).read_text(encoding="utf-8").count("mine")


def test_a_stop_word_only_query_keeps_its_words(tmp_path: Path) -> None:
    """Dropping every term would report an empty memory rather than an odd question."""

    assert query_terms("the a of is it") == ("the", "a", "of", "is", "it")


def test_a_query_searches_for_the_words_that_mean_something() -> None:
    """The word side requires every term it is given, so a stop word only loses it."""

    assert query_terms("What is research-rag for?") == ("research-rag",)
    assert query_terms("why keep durable information?") == (
        "keep",
        "durable",
        "information",
    )
    assert {"what", "is", "the", "for", "why"} <= STOPWORDS


def test_replacing_the_standing_document_needs_the_digest_it_was_read_at(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "scope"
    read_standing(scope)
    written = write_standing(scope, "# MEMORY\nmine\n")
    assert read_standing(scope) == "# MEMORY\nmine\n"

    # A document that changed since it was read is never overwritten.
    standing_document(scope).write_text("# MEMORY\ntheirs\n", encoding="utf-8")
    with pytest.raises(StoreError, match="changed since it was read"):
        write_standing(scope, "# MEMORY\nmine\n", expected_sha256=written)


def test_a_statement_is_appended_to_the_standing_document(tmp_path: Path) -> None:
    """A statement is a typed block, with no speaker invented for it."""

    scope = tmp_path / "scope"
    read_standing(scope)
    digest = append_statement(scope, "The draft lives in docs/", statement_type="note")

    standing = read_standing(scope)
    assert standing.startswith(TEMPLATE)
    assert "The draft lives in docs/" in standing
    assert "user:" not in standing
    assert digest and len(digest) == 64
    # A second statement lands below the first, and nothing is rewritten.
    append_statement(scope, "always cite the commit", statement_type="rule")
    twice = read_standing(scope)
    assert twice.index("The draft lives in docs/") < twice.index("always cite")
    assert standing.rstrip("\n") in twice


def test_a_statement_records_its_label_inline(tmp_path: Path) -> None:
    """The label is the block's own first words, so the document needs no field."""

    scope = tmp_path / "scope"
    read_standing(scope)
    append_statement(scope, "The draft lives in docs/", statement_type="note")

    standing = read_standing(scope)
    assert standing.endswith("NOTE: The draft lives in docs/\n")
    assert "type:" not in standing


def test_a_label_is_written_in_block_letters_whatever_case_it_arrives_in(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "scope"
    read_standing(scope)
    append_statement(scope, "a fact", statement_type="plan")
    append_statement(scope, "another fact", statement_type="Rule")

    standing = read_standing(scope)
    assert "PLAN: a fact" in standing
    assert "RULE: another fact" in standing


def test_a_type_is_the_callers_own_and_is_not_interpreted(tmp_path: Path) -> None:
    """Nothing here knows what a label means, so any block letters are accepted."""

    scope = tmp_path / "scope"
    read_standing(scope)
    append_statement(scope, "a fact", statement_type="ZEBRAFISH")

    assert "ZEBRAFISH: a fact" in read_standing(scope)


def test_white_space_in_a_type_is_refused(tmp_path: Path) -> None:
    """A type is one category; a phrase would be part of the statement."""

    with pytest.raises(StoreError, match="must be one word"):
        append_statement(tmp_path / "scope", "a fact", statement_type="standing rule")
    with pytest.raises(StoreError, match="must be one word"):
        append_statement(tmp_path / "scope", "a fact", statement_type="rule\nsecond")


def test_anything_that_is_not_block_letters_is_refused(tmp_path: Path) -> None:
    for bad in ("rule:extra", "rule-2", "rule_2", "rule!", "rule2"):
        with pytest.raises(StoreError, match="block letters only"):
            append_statement(tmp_path / "scope", "a fact", statement_type=bad)


def test_an_empty_type_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="type must not be empty"):
        append_statement(tmp_path / "scope", "a fact", statement_type="   ")


def test_an_oversized_type_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="at most 64 characters"):
        append_statement(tmp_path / "scope", "a fact", statement_type="X" * 65)


def test_a_statement_whose_text_looks_like_a_label_still_reads_back(
    tmp_path: Path,
) -> None:
    """Only the block's own label is dropped; the statement is returned as written."""

    from memory_ultra_rag_mcp.read import _answered_text

    assert _answered_text("statement", "RULE: Cite: the commit hash") == (
        "Cite: the commit hash"
    )
    # Mixed case is not a label, so a sentence opening with one is not cut either.
    assert _answered_text("statement", "RULE: Note: it failed again") == (
        "Note: it failed again"
    )


def test_text_of_another_kind_is_returned_untouched() -> None:
    """Only what this store wrote is treated as labelled; nothing else is trimmed."""

    from memory_ultra_rag_mcp.read import _answered_text

    assert _answered_text("round", "RULE: I never reproduced it") == (
        "RULE: I never reproduced it"
    )


def test_an_empty_statement_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="content must not be empty"):
        append_statement(tmp_path / "scope", "   \n ", statement_type="note")
