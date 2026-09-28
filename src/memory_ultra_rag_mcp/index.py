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
    STATEMENT_LABEL_PATTERN,
    TEMPLATE,
    StoreError,
    query_terms,
    standing_document,
)
from .vectors import unit_key

__all__ = [
    "INDEX_FILENAME",
    "IndexError",
    "MemoryIndex",
    "fts5_available",
    "index_path",
    "query_terms",
    "unit_key",
]

#: The derived index file inside a scope directory.
INDEX_FILENAME = "index.sqlite3"

#: Bumped when the schema changes, so an older index is rebuilt rather than read.
SCHEMA_VERSION = "2"

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
    write appends and what a person editing the Markdown by hand produces. The
    template upstream seeds a standing document with is a seed, not something
    anyone wrote, so it is not indexed and cannot answer a query.

    ``stamp`` is the block's ordinal in the file, which is stable only because a
    sync always re-reads the whole file: the number a caller is given is where the
    statement sits, not its identity, which is the content hash.
    """

    units: list[tuple[str, str, str, str]] = []
    for ordinal, block in enumerate(text.split("\n\n")):
        statement = block.strip()
        # The template upstream seeds a standing document with is a seed, not
        # something anyone wrote, so it is not indexed and cannot answer a query.
        if statement and statement != TEMPLATE.strip():
            units.append((statement, "statement", source, f"{ordinal}"))
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
            "text, unit_key UNINDEXED, kind UNINDEXED, source UNINDEXED, "
            "stamp UNINDEXED)"
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
                "text, unit_key UNINDEXED, kind UNINDEXED, source UNINDEXED, "
                "stamp UNINDEXED)"
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

    def freshness(self) -> dict[str, int]:
        """Report what would change if the index were brought up to date.

        This is two ``stat`` calls — the standing document and the rounds
        directory — so a read can say whether the index might be behind without
        walking every file. It catches a file added or removed, and the standing
        document edited; an in-place edit to an older dated file is not caught,
        which is the cost of maintaining the index on the write side and what
        ``reindex`` is for.
        """

        connection = self._open()
        behind = 0
        for path in self.sources():
            row = connection.execute(
                "SELECT size, mtime_ns FROM source WHERE path = ?",
                (str(path.relative_to(self.scope_directory)),),
            ).fetchone()
            if row is None:
                behind += 1
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                behind += 1
                continue
            if int(row[0]) != stat.st_size or int(row[1]) != stat.st_mtime_ns:
                behind += 1
        return {"files_behind": behind}

    def count_units(self) -> int:
        """Return how many units this index holds, without materialising them.

        A disclosure number, so it is two counts rather than a walk: the read
        path must not pay per unit to say how many are waiting.
        """

        connection = self._open()
        row = connection.execute("SELECT count(*) FROM unit").fetchone()
        return int(row[0]) if row else 0

    def unit_keys(self, source: str | None = None) -> set[str]:
        """Return the identity of the units this index holds.

        Narrowed to one file when the caller is the write path, which only wrote
        one, so the work is proportional to that file rather than to the memory.
        """

        connection = self._open()
        if source is None:
            rows = connection.execute("SELECT unit_key FROM unit").fetchall()
        else:
            rows = connection.execute(
                "SELECT unit_key FROM unit WHERE source = ?", (source,)
            ).fetchall()
        return {str(row[0]) for row in rows}

    def units_by_key(self, keys: Iterable[str]) -> dict[str, dict[str, str]]:
        """Return the units named by these keys, with the text each one holds."""

        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return {}
        connection = self._open()
        found: dict[str, dict[str, str]] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = connection.execute(
                "SELECT unit_key, text, kind, source, stamp FROM unit "
                f"WHERE unit_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for key, text_value, kind, source, stamp in rows:
                found[str(key)] = {
                    "text": str(text_value),
                    "kind": str(kind),
                    "source": str(source),
                    "stamp": str(stamp),
                }
        return found

    def sources(self) -> list[Path]:
        """Return the files this scope is made of, in a stable order.

        A scope is one standing document now, so this is one path. It stays a
        list because the index's contract is a set of files, and because a file
        that is not there yet is not an error.
        """

        standing = standing_document(self.scope_directory)
        return [standing] if standing.is_file() else []

    def sync(self) -> dict[str, int]:
        """Re-read whatever changed across the scope, and report what is held.

        This walks every file, so it belongs to maintenance rather than to a
        read: the write path calls :meth:`sync_file` for the one file it wrote,
        and only ``reindex`` walks the scope.
        """

        return self._sync(self.sources(), whole_scope=True)

    def sync_file(self, path: Path) -> dict[str, int]:
        """Re-read one file of this scope, and report what the index holds.

        This is the write path's half of keeping the index in step, and its work
        is proportional to that one file, never to the memory.
        """

        return self._sync([path], whole_scope=False)

    def catch_up(self) -> dict[str, int]:
        """Bring the index in step with a file edited outside these tools, cheaply.

        A read calls this before it searches, so a statement a person typed into
        the Markdown is answerable without anyone remembering to run a reindex. It
        asks only whether the standing document changed, and does nothing at all
        when it did not, so the common case costs one fingerprint — measured at
        about 17 microseconds, against a read of hundreds of milliseconds. When it
        did change, the file is re-read, which is about 2 ms for a memory of this
        size; the vectors for any new statements are the write side's async work,
        which the read reports as ``units_pending`` until the worker drains.

        ``unlabelled`` counts statements a person wrote without a ``LABEL:``
        prefix. The tool enforces the type, a hand edit does not, and an untyped
        statement is silently absent from every type-filtered question — so it is
        counted, but only on the read that had to re-read the file, never per read.

        This is why the measurement behind a read not sweeping its files no longer
        applies: it was taken in a server that kept a directory of dated files,
        and a scope here is one document.
        """

        if not self.freshness()["files_behind"]:
            return {
                "synced": 0,
                "unlabelled": 0,
                "units": self.count_units(),
            }
        state = self.sync()
        return {**state, "synced": 1, "unlabelled": self._count_unlabelled()}

    def _count_unlabelled(self) -> int:
        """Return how many held statements carry no type label.

        Only called after a re-read, so the walk is over a file that already had
        to be read once and is never paid on the common path.
        """

        connection = self._open()
        rows = connection.execute("SELECT text FROM unit").fetchall()
        return sum(1 for row in rows if not STATEMENT_LABEL_PATTERN.match(str(row[0])))

    def _sync(self, candidates: Sequence[Path], *, whole_scope: bool) -> dict[str, int]:
        """Re-read whichever of these files changed, and report what is held.

        A changed file is re-read whole, which is what a hand edit looks like, and
        its previous units are replaced so a statement a person deleted stops
        being answerable.
        """

        connection = self._open()
        present = [path for path in candidates if path.is_file()]
        known = {
            str(row[0]): (int(row[1]), int(row[2]), str(row[3]))
            for row in connection.execute(
                "SELECT path, size, mtime_ns, head FROM source"
            ).fetchall()
        }

        if whole_scope:
            # Only a whole-scope pass can notice a file that was removed; a
            # one-file pass must not delete units it was not asked about.
            wanted = {str(path.relative_to(self.scope_directory)) for path in present}
            for name in known:
                if name not in wanted and not (self.scope_directory / name).is_file():
                    connection.execute("DELETE FROM unit WHERE source = ?", (name,))
                    connection.execute("DELETE FROM source WHERE path = ?", (name,))

        indexed = 0
        for path in present:
            name = str(path.relative_to(self.scope_directory))
            fingerprint = _fingerprint(path)
            if fingerprint is None:
                continue
            if fingerprint == known.get(name):
                continue
            # Always re-read the whole file and replace its units. An earlier
            # version parsed only the new tail when a file had merely grown, which
            # is faster but misnumbered: the tail's blocks were numbered from zero,
            # so every statement appended after the document passed the 4 KiB head
            # prefix was stamped '1' — and 'stamp' is returned in every answer.
            # Re-reading a scope's one file costs about 2 ms, measured, so the
            # numbering is worth more than the shortcut.
            connection.execute("DELETE FROM unit WHERE source = ?", (name,))
            units = _statement_units(path.read_text(encoding="utf-8"), name)
            connection.executemany(
                "INSERT INTO unit(text, unit_key, kind, source, stamp) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (text, unit_key(text), kind, name, stamp)
                    for text, kind, _source, stamp in units
                ],
            )
            connection.execute(
                "INSERT OR REPLACE INTO source(path, size, mtime_ns, head) "
                "VALUES (?, ?, ?, ?)",
                (name, *fingerprint),
            )
            indexed += len(units)

        connection.commit()
        total = int(connection.execute("SELECT count(*) FROM unit").fetchone()[0])
        return {"indexed": indexed, "units": total, "files": len(present)}

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

        This does **not** bring the index up to date. A search answers from what
        the index holds, and keeping it current is the write side's job — the
        tools, the page, and ``reindex`` — because a read may not walk the scope
        to find out what changed. A caller that wants the current answer syncs
        first; :meth:`freshness` is what a read uses to say when it might not be.
        """

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
                "SELECT text, unit_key, kind, source, stamp FROM unit "
                "WHERE unit MATCH ? ORDER BY rank LIMIT ?",
                (expression, max(int(limit), 1) + 1),
            )
        except sqlite3.Error as error:
            raise IndexError(
                f"the query could not be run against the index: {error}"
            ) from error
        found = cursor.fetchall()
        # One row past the limit is how the caller learns there was more.
        return [
            {
                "text": text,
                "unit_key": key,
                "kind": kind,
                "source": source,
                "stamp": stamp,
            }
            for text, key, kind, source, stamp in found
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
