"""One scope's memory: a standing document and dated rounds, in upstream's format."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from memory_ultra_rag_mcp.store import (
    TEMPLATE,
    StoreError,
    append_round,
    daily_rounds,
    read_standing,
    standing_document,
    user_id_error,
)

# A fixed local timestamp, built without a zone because upstream stamps
# rounds with local naive time and the format has to match.
MOMENT = datetime.fromisoformat("2026-09-22T14:48:30")


def test_the_first_read_creates_the_standing_document(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    assert read_standing(scope) == TEMPLATE
    assert standing_document(scope).read_text(encoding="utf-8") == TEMPLATE
    # A second read returns what is there rather than rewriting it.
    standing_document(scope).write_text("# MEMORY\nmine\n", encoding="utf-8")
    assert read_standing(scope) == "# MEMORY\nmine\n"


def test_a_round_is_written_exactly_as_upstream_writes_it(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    written = append_round(
        scope,
        ["What do you remember about me?"],
        ["Only what you asked me to keep."],
        now=MOMENT,
    )

    assert written == daily_rounds(scope) / "2026-09-22.md"
    assert written.read_text(encoding="utf-8") == (
        "# Project Memory 2026-09-22\n"
        "\n"
        "## 2026-09-22 14:48:30\n"
        "- user: What do you remember about me?\n"
        "- assistant: Only what you asked me to keep.\n"
    )


def test_later_rounds_append_to_the_same_day(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_round(scope, ["first"], ["one"], now=MOMENT)
    written = append_round(scope, ["second"], ["two"], now=MOMENT.replace(hour=15))

    text = written.read_text(encoding="utf-8")
    assert text.count("# Project Memory 2026-09-22") == 1
    assert text.index("- user: first") < text.index("- user: second")
    assert "## 2026-09-22 15:48:30" in text


def test_a_new_day_gets_its_own_file(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    append_round(scope, ["today"], ["yes"], now=MOMENT)
    tomorrow = append_round(scope, ["tomorrow"], ["later"], now=MOMENT.replace(day=23))

    assert tomorrow.name == "2026-09-23.md"
    assert len(sorted(daily_rounds(scope).glob("*.md"))) == 2


def test_an_empty_message_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="user_message cannot be empty"):
        append_round(tmp_path / "scope", [""], ["answer"])
    with pytest.raises(StoreError, match="assistant_message cannot be empty"):
        append_round(tmp_path / "scope", ["question"], [""])


def test_two_scopes_keep_two_memories(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    append_round(first, ["mine"], ["yours"], now=MOMENT)

    assert read_standing(first) == TEMPLATE
    assert not standing_document(second).exists()
    assert read_standing(second) == TEMPLATE
    assert list(daily_rounds(first).glob("*.md"))
    assert not daily_rounds(second).exists()


@pytest.mark.parametrize("user_id", ["../escape", "a/b", "with space", "dot.name"])
def test_an_identifier_that_cannot_name_a_directory_is_refused(user_id: str) -> None:
    assert user_id_error(user_id) is not None


@pytest.mark.parametrize("user_id", ["default", "ahmed", "team_1", "team-1", "A1"])
def test_a_usable_identifier_is_accepted(user_id: str) -> None:
    assert user_id_error(user_id) is None
