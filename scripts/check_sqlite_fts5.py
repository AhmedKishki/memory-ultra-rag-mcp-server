"""Verify the SQLite FTS5 features required by the memory ledger."""

from __future__ import annotations

import json
import platform
import sqlite3
import sys


def _ids(rows: list[tuple[object, ...]]) -> list[str]:
    return [str(row[0]) for row in rows]


def main() -> None:
    with sqlite3.connect(":memory:") as database:
        compile_options = {
            str(row[0]) for row in database.execute("PRAGMA compile_options")
        }
        database.execute(
            "CREATE VIRTUAL TABLE memory_fts "
            "USING fts5(memory_id UNINDEXED, content, tokenize='unicode61')"
        )
        database.executemany(
            "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
            [
                ("memory-1", "Maintain project continuity between sessions."),
                ("memory-2", "Use British English for every report."),
                ("memory-3", "The parser migration is complete."),
            ],
        )

        phrase_rows = database.execute(
            "SELECT memory_id, bm25(memory_fts) FROM memory_fts "
            "WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts), memory_id",
            ('"project continuity"',),
        ).fetchall()
        prefix_rows = database.execute(
            "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ?",
            ("migrat*",),
        ).fetchall()

        database.execute(
            "UPDATE memory_fts SET content = ? WHERE memory_id = ?",
            ("The parser replacement is complete.", "memory-3"),
        )
        old_term_rows = database.execute(
            "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ?",
            ("migration",),
        ).fetchall()
        new_term_rows = database.execute(
            "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ?",
            ("replacement",),
        ).fetchall()

    checks = {
        "phrase_and_bm25": _ids(phrase_rows) == ["memory-1"],
        "prefix": _ids(prefix_rows) == ["memory-3"],
        "update_removes_old_terms": old_term_rows == [],
        "update_adds_new_terms": _ids(new_term_rows) == ["memory-3"],
    }
    result = {
        "checks": checks,
        "fts5_compile_option_reported": "ENABLE_FTS5" in compile_options,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "system": platform.system(),
    }
    print(json.dumps(result, sort_keys=True))
    if not all(checks.values()):
        raise RuntimeError("SQLite FTS5 behavior did not match the required contract")


if __name__ == "__main__":
    try:
        main()
    except sqlite3.OperationalError as error:
        print(f"SQLite FTS5 is unavailable: {error}", file=sys.stderr)
        raise SystemExit(1) from error
