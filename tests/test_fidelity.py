"""This package writes the standing document UltraRAG's own server writes.

The fixture was captured from `servers/memory/src/memory.py` at the pinned
revision; see `tests/fixtures/upstream/README.md` for how. The comparison is
byte-for-byte, so the format cannot drift without failing here.

The same revision also keeps a dated exchange log beside that document, captured
as `daily_round.md`. This package does not write one: a memory here is a set of
statements, and anything worth keeping is recorded as one. That is a deliberate
divergence from upstream and it is recorded in the README's choice table, which
is why this file compares one format rather than two.
"""

from __future__ import annotations

from pathlib import Path

from memory_ultra_rag_mcp.store import TEMPLATE, read_standing, standing_document

FIXTURES = Path(__file__).parent / "fixtures" / "upstream"


def test_the_template_is_upstreams(tmp_path: Path) -> None:
    upstream = (FIXTURES / "standing_memory.md").read_text(encoding="utf-8")
    assert TEMPLATE == upstream
    assert read_standing(tmp_path / "scope") == upstream


def test_the_standing_document_is_named_as_upstream_names_it(
    tmp_path: Path,
) -> None:
    """Upstream's own file name, kept: MEMORY.md, in the scope directory."""

    scope = tmp_path / "scope"
    read_standing(scope)
    assert standing_document(scope).name == "MEMORY.md"
    assert standing_document(scope).parent == scope


def test_a_scope_is_one_document_and_nothing_else(tmp_path: Path) -> None:
    """No dated directory is created, and none is looked for."""

    from memory_ultra_rag_mcp.index import MemoryIndex

    scope = tmp_path / "scope"
    with MemoryIndex(scope) as index:
        index.insert("a fact", "NOTE")
        index.write_export()

    assert [path.name for path in scope.rglob("*")] == ["MEMORY.md", "memory.sqlite3"]
    assert not (scope / "project").exists()
