"""A document written by UltraRAG's own server is still readable here.

Upstream's memory is one `MEMORY.md` per user, seeded from a template that holds
a line of placeholder text. This package no longer writes that file — the record
is the memory — but a memory of an older version still has one beside it, and
reading it is a migration, so the shape of what upstream wrote is still
something this server has to understand. The fixture was captured from
`servers/memory/src/memory.py` at the pinned revision; see
`tests/fixtures/upstream/README.md` for how.

The same revision also keeps a dated exchange log beside that document, captured
as `daily_round.md`. This package reads none: a memory here is a set of
statements, and anything worth keeping is recorded as one. That is a deliberate
divergence from upstream and it is recorded in the README's choice table, which
is why this file compares one format rather than two.
"""

from __future__ import annotations

from pathlib import Path

from memory_ultra_rag_mcp.index import MemoryIndex
from memory_ultra_rag_mcp.store import (
    EXPORT_FILENAME,
    SEED,
    TEMPLATE,
    parse_document,
    read_document,
)

FIXTURES = Path(__file__).parent / "fixtures" / "upstream"


def test_the_seed_is_upstreams_byte_for_byte() -> None:
    """The line upstream writes is recognised, so it is never a statement."""

    upstream = (FIXTURES / "standing_memory.md").read_text(encoding="utf-8")

    assert TEMPLATE == upstream
    assert SEED in TEMPLATE
    assert parse_document(upstream) == []


def test_a_scope_with_no_document_has_none_to_read(tmp_path: Path) -> None:
    """Nothing is written beside the record, so there is nothing to read."""

    assert read_document(tmp_path / "scope") is None


def test_the_file_a_previous_version_wrote_is_named_as_upstream_named_it() -> None:
    """The migration reads exactly the file upstream's server left, by its name."""

    assert EXPORT_FILENAME == "MEMORY.md"


def test_a_scope_is_one_file_and_nothing_else(tmp_path: Path) -> None:
    """No document beside it, and no dated directory anywhere under it."""

    scope = tmp_path / "scope"
    with MemoryIndex(scope) as index:
        index.insert("a fact", "NOTE")

    names = {
        path.name
        for path in scope.rglob("*")
        if not path.name.endswith(("-wal", "-shm"))
    }

    assert names == {"memory.sqlite3"}
    assert not (scope / "project").exists()
    assert not (scope / EXPORT_FILENAME).exists()
