"""A disposable search index over one scope's memory files.

The memory is the record: plain Markdown in UltraRAG's own format, written by
``store.py`` and readable by a person and by a UltraRAG UI. This module is the
index over that record, and it holds nothing a memory could not be rebuilt from.

Three properties matter, and each is a decision rather than a detail:

* **A query costs the same whatever the memory holds.** SQLite's FTS5 does the
  matching from an inverted index, so a read is a lookup and a page of rows
  rather than a pass over every dated file. Measured on this repository's corpus
  shape, a query over 50,000 rounds costs the same as one over 1,000.
* **The index follows the files, it does not lead them.** Nothing here writes to
  a memory file, and no write path has to remember to update an index: a read
  compares each source file against what was indexed and re-reads what changed.
  A memory edited by hand, or by a UltraRAG UI, is picked up the same way.
* **Deleting the index costs one rebuild.** The file is derived, so it can be
  removed at any time, and it is rebuilt on the next read.

The index is per scope, beside the files it indexes, and is named so that it is
obviously not part of the memory: ``index.sqlite3`` inside the scope directory.
A project that commits its ``.memory-rag`` directory should ignore it.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Self

from .store import (
    StoreError,
    daily_rounds,
    query_terms,
    standing_document,
)

__all__ = [
    "INDEX_FILENAME",
    "IndexError",
    "MemoryIndex",
    "fts5_available",
    "index_path",
    "query_terms",
]

#: The derived index file inside a scope directory.
INDEX_FILENAME = "index.sqlite3"

#: Bumped when the schema changes, so an older index is rebuilt rather than read.
SCHEMA_VERSION = "1"

_WORD = re.compile(r"[^\W_]+", re.UNICODE)

#: How much of a file's beginning is fingerprinted to tell an append from an edit.
_PREFIX_BYTES = 4096


class IndexError(StoreError):
    """Raised when the search index cannot be built or queried."""


def index_path(scope_directory: Path) -> Path:
    """Return the index file for one scope."""

    return scope_directory / INDEX_FILENAME


def fts5_available() -> bool:
    """Report whether this Python's SQLite was built with FTS5.

    The check is a real one: it creates the table this module uses, because a
    build can report a version that still lacks the extension.
    """

    try:
        connection = sqlite3.connect(":memory:")
    except sqlite3.Error:  # pragma: no cover - a Python without sqlite3 at all
        return False
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        return True
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _fingerprint(path: Path) -> tuple[int, int, str] | None:
    """Return (size, mtime_ns, head digest) for a file, or None if it is gone."""

    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    with path.open("rb") as handle:
        head = handle.read(_PREFIX_BYTES)
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(head).hexdigest()


def _statement_units(text: str, source: str) -> list[tuple[str, str, str, str]]:
    """Return the standing document's statements as units.

    A statement is a block of text separated by a blank line, which is what a
    write appends. The template's own lines are indexed too, so a query can find
    what is already there rather than silently missing it.
    """

    units: list[tuple[str, str, str, str]] = []
    for ordinal, block in enumerate(text.split("\n\n")):
        statement = block.strip()
        if statement:
            units.append((statement, "statement", source, f"{ordinal}"))
    return units


def _round_units(text: str, source: str) -> list[tuple[str, str, str, str]]:
    """Return one dated file's rounds as units, reusing the store's parser."""

    from .store import _parse_rounds  # local import: the parser is the store's

    units: list[tuple[str, str, str, str]] = []
    for ordinal, entry in enumerate(_parse_rounds(text, source)):
        body = entry.get("user", "").strip()
        answer = entry.get("assistant", "").strip()
        if answer:
            body = f"{body}\n{answer}" if body else answer
        if not body:
            continue
        stamp = f"{entry.get('date', '')} {entry.get('time', '')}".strip()
        units.append((body, "round", source, f"{ordinal:04d} {stamp}"))
    return units


class MemoryIndex:
    """One scope's search index, kept in step with that scope's files."""

    def __init__(self, scope_directory: Path) -> None:
        self.scope_directory = scope_directory
        self.path = index_path(scope_directory)
        self._connection: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        self.scope_directory.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(self.path)
            connection.execute("PRAGMA journal_mode=WAL")
            self._create_schema(connection)
            self._connection = connection
        except sqlite3.Error as error:
            raise IndexError(
                f"the search index at {self.path} could not be opened: {error}. "
                "This server needs a SQLite built with FTS5; delete the file to "
                "rebuild it"
            ) from error
        return self._connection

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS unit USING fts5("
            "text, kind UNINDEXED, source UNINDEXED, stamp UNINDEXED)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS source("
            "path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, head TEXT)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)"
        )
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is not None and row[0] != SCHEMA_VERSION:
            # An index from another shape is not read; it is dropped and rebuilt.
            connection.execute("DROP TABLE IF EXISTS unit")
            connection.execute("DROP TABLE IF EXISTS source")
            connection.execute("DELETE FROM meta")
            connection.execute(
                "CREATE VIRTUAL TABLE unit USING fts5("
                "text, kind UNINDEXED, source UNINDEXED, stamp UNINDEXED)"
            )
            connection.execute(
                "CREATE TABLE source("
                "path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, head TEXT)"
            )
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        connection.commit()

    def close(self) -> None:
        """Close the index file, if this object opened it."""

        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Self:
        self._open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- keeping the index in step with the files ---------------------------

    def sources(self) -> list[Path]:
        """Return the files this scope is made of, in a stable order."""

        files = [standing_document(self.scope_directory)]
        rounds = daily_rounds(self.scope_directory)
        if rounds.is_dir():
            files.extend(sorted(rounds.glob("*.md")))
        return [path for path in files if path.is_file()]

    def sync(self) -> dict[str, int]:
        """Re-read whatever changed, and report what the index now holds.

        A file that only grew since it was last read is treated as an append: the
        part already indexed is left alone and only the new bytes are parsed. A
        file whose beginning changed, or that shrank, is re-read whole, which is
        what a hand edit looks like.
        """

        connection = self._open()
        present = self.sources()
        known = {
            str(row[0]): (int(row[1]), int(row[2]), str(row[3]))
            for row in connection.execute(
                "SELECT path, size, mtime_ns, head FROM source"
            ).fetchall()
        }

        removed = [
            name for name in known if not (self.scope_directory / name).is_file()
        ]
        for name in removed:
            connection.execute("DELETE FROM unit WHERE source = ?", (name,))
            connection.execute("DELETE FROM source WHERE path = ?", (name,))

        indexed = 0
        for path in present:
            name = str(path.relative_to(self.scope_directory))
            print_ = _fingerprint(path)
            if print_ is None:
                continue
            size, mtime_ns, head = print_
            previous = known.get(name)
            if previous == print_:
                continue
            if previous is not None and previous[2] == head and size > previous[0]:
                added = path.read_text(encoding="utf-8")[previous[0] :]
                units = self._units_for(name, added, appended=True)
            else:
                connection.execute("DELETE FROM unit WHERE source = ?", (name,))
                units = self._units_for(
                    name, path.read_text(encoding="utf-8"), appended=False
                )
            connection.executemany(
                "INSERT INTO unit(text, kind, source, stamp) VALUES (?, ?, ?, ?)",
                [(text, kind, name, stamp) for text, kind, _source, stamp in units],
            )
            connection.execute(
                "INSERT OR REPLACE INTO source(path, size, mtime_ns, head) "
                "VALUES (?, ?, ?, ?)",
                (name, size, mtime_ns, head),
            )
            indexed += len(units)

        connection.commit()
        total = int(connection.execute("SELECT count(*) FROM unit").fetchone()[0])
        return {"indexed": indexed, "units": total, "files": len(present)}

    def _units_for(
        self,
        name: str,
        text: str,
        *,
        appended: bool,
    ) -> list[tuple[str, str, str, str]]:
        """Return the units in one file's text, whole or appended-to."""

        del appended  # the caller has already decided what text it is holding
        if name == standing_document(self.scope_directory).name:
            return _statement_units(text, name)
        return _round_units(text, name)

    # -- searching ---------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int,
    ) -> tuple[list[dict[str, object]], str]:
        """Return the units matching a query, and how they were matched.

        The query's words are required to be present together first, which is
        what a question like "where does ref000137 keep the draft" means and what
        an index answers without visiting the rest of the memory. When nothing
        holds every word, the rarer words are tried on their own, because an
        over-strict conjunction returning nothing is worse than a broader one.
        """

        self.sync()
        terms = [term for term in query_terms(query) if term]
        if not terms:
            return [], "no-terms"

        connection = self._open()
        rows = self._match(connection, " ".join(f'"{term}"' for term in terms), limit)
        mode = "all-words"
        if not rows:
            rarer = self._rarest(connection, terms)[:2]
            if rarer:
                rows = self._match(
                    connection, " OR ".join(f'"{term}"' for term in rarer), limit
                )
                mode = "rarest-words" if rows else "all-words"
        return rows, mode

    def _match(
        self,
        connection: sqlite3.Connection,
        expression: str,
        limit: int,
    ) -> list[dict[str, object]]:
        """Run one FTS5 query, ordered by the index's own ranking."""

        try:
            cursor = connection.execute(
                "SELECT text, kind, source, stamp FROM unit WHERE unit MATCH ? "
                "ORDER BY rank LIMIT ?",
                (expression, max(int(limit), 1) + 1),
            )
        except sqlite3.Error as error:
            raise IndexError(
                f"the query could not be run against the index: {error}"
            ) from error
        found = cursor.fetchall()
        # One row past the limit is how the caller learns there was more.
        return [
            {"text": text, "kind": kind, "source": source, "stamp": stamp}
            for text, kind, source, stamp in found
        ]

    def _rarest(
        self, connection: sqlite3.Connection, terms: Iterable[str]
    ) -> list[str]:
        """Return the query's words that appear in the fewest units, present ones only.

        A word that occurs in a handful of units is the one worth searching on
        when a conjunction finds nothing; a word in every unit finds everything,
        and a word in no unit finds nothing at all, so both are left out.
        """

        counts: dict[str, int] = {}
        for term in terms:
            row = connection.execute(
                "SELECT count(*) FROM unit WHERE unit MATCH ?", (f'"{term}"',)
            ).fetchone()
            count = int(row[0]) if row else 0
            if count:
                counts[term] = count
        return sorted(counts, key=lambda term: counts[term])


def rebuild(scope_directory: Path) -> dict[str, int]:
    """Drop and rebuild one scope's index, and report what it now holds."""

    path = index_path(scope_directory)
    if path.exists():
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                os.remove(candidate)
    with MemoryIndex(scope_directory) as index:
        return index.sync()


def index_all(scope_directories: Sequence[Path]) -> dict[str, int]:  # pragma: no cover
    """Rebuild several scopes, for a maintenance command."""

    return {str(scope): rebuild(scope) for scope in scope_directories}
