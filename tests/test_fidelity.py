"""This package writes what UltraRAG's own server writes.

Both fixtures were captured from `servers/memory/src/memory.py` at the pinned
revision; see `tests/fixtures/upstream/README.md` for how. The comparison is
byte-for-byte, so the format cannot drift without failing here.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from memory_ultra_rag_mcp.store import TEMPLATE, append_round, read_standing

FIXTURES = Path(__file__).parent / "fixtures" / "upstream"
MOMENT = datetime.fromisoformat("2026-09-22T14:48:30")
TIMESTAMP = re.compile(r"^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", re.MULTILINE)


def _normalize(text: str) -> str:
    """Replace the round's timestamp, which is written when it is written."""
    return TIMESTAMP.sub("## <timestamp>", text)


def test_the_template_is_upstreams(tmp_path: Path) -> None:
    upstream = (FIXTURES / "standing_memory.md").read_text(encoding="utf-8")
    assert TEMPLATE == upstream
    assert read_standing(tmp_path / "scope") == upstream


def test_a_written_round_matches_upstreams_bytes(tmp_path: Path) -> None:
    upstream = (FIXTURES / "daily_round.md").read_text(encoding="utf-8")
    written = append_round(
        tmp_path / "scope",
        ["What do you remember about me?"],
        ["Only what you asked me to keep."],
        now=MOMENT,
    )

    assert _normalize(written.read_text(encoding="utf-8")) == _normalize(upstream)


def test_the_day_header_matches_upstreams_bytes(tmp_path: Path) -> None:
    upstream_first_line = (
        (FIXTURES / "daily_round.md").read_text(encoding="utf-8").splitlines()[0]
    )
    written = append_round(
        tmp_path / "scope",
        ["question"],
        ["answer"],
        now=MOMENT,
    )
    assert written.read_text(encoding="utf-8").splitlines()[0] == upstream_first_line
