"""One scope's memory, as a table, with the document written out beside it.

``memory.sqlite3`` is the record. Every statement is a row in ``unit``: its text,
the kind it was filed under, its place in the document, and the four things a
sentence cannot hold — when it was added, how often it has been recalled, and
when it was last recalled. The same table is the FTS5 word index, so a statement
is stored once and found by words without a second copy to keep in step.

``MEMORY.md`` is an **export** of that record: the same statements as plain prose,
newest first, regenerated whenever the memory changes and on a reindex. It exists
to be read, diffed, and carried, and it is not read as an input — a hand edit to
it is overwritten by the next export and the read that notices says so, rather
than leaving an edit that looks as though it worked.

The vectors are in the same file, keyed by the same statement digest, and are the
one derived part a rebuild cannot restore cheaply: they cost one embedding each.
A file whose table is not the shape this version writes is dropped and rebuilt
rather than half-read.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from .store import (
    Statement,
    StoreError,
    normalise,
    parse_document,
    query_terms,
    read_standing,
    render_document,
    unit_key,
    write_standing,
)

__all__ = [
    "INDEX_FILENAME",
    "LEGACY_VECTORS_FILENAME",
    "IndexError",
    "MemoryIndex",
    "fts5_available",
    "index_path",
    "query_terms",
    "unit_key",
]

#: The one file inside a scope directory, holding everything derived from the
#: document: the word index, the vectors, and the history no rebuild can produce.
INDEX_FILENAME = "memory.sqlite3"

#: The file a version before 5 kept its vectors in. Its rows are copied into the
#: one file and it is then removed, so upgrading costs no re-embedding.
LEGACY_VECTORS_FILENAME = "index-vectors.sqlite3"

#: Bumped when the schema changes, so an older file is read as a previous version
#: rather than as this one. 3 replaced the ``kind`` column, which held one value
#: for every row, with the statement's own type. 4 added a separate history table.
#: 5 brought the vectors in from their own file. 6 made ``unit`` the record
#: itself — it gained the four columns the history table held, plus the normalised
#: text that an exact match compares — and dropped that table, because a statement
#: is one row and not two. 7 renamed that field from ``type`` to ``kind``.
SCHEMA_VERSION = "7"

#: The vector table, and the columns this version writes. A table that does not
#: have exactly these is dropped and rebuilt rather than half-read.
VECTOR_COLUMNS = (
    "unit_key",
    "stamp",
    "kind",
    "model",
    "dimension",
    "components",
)
VECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS vector(
    unit_key TEXT PRIMARY KEY,
    stamp TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    components BLOB NOT NULL
);
"""

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _now() -> str:
    """Return the current time, as text a person can read and a string can order.

    Milliseconds rather than seconds because a burst of statements is written in
    the same second, and the preference for new information is between statements
    recorded moments apart. A second of resolution would make the newest of a
    burst indistinguishable from the first, which is the case it exists for.
    """

    return datetime.now(UTC).isoformat(timespec="milliseconds")


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


class MemoryIndex:
    """One scope's memory: the record, the search over it, and the document it exports."""

    def __init__(self, scope_directory: Path) -> None:
        self.scope_directory = scope_directory
        self.path = index_path(scope_directory)
        self._connection: sqlite3.Connection | None = None
        #: Statements read out of a file of an older shape, kept until the
        #: document has been asked whether it can supply them instead.
        self._legacy_rows: list[tuple[str, str, str, str]] = []

    # -- lifecycle ---------------------------------------------------------

    def _adopt_legacy_vectors(self, connection: sqlite3.Connection) -> int:
        """Copy a version-4 vector file into this one, then remove it.

        A memory's vectors cost one embedding each, so an upgrade that dropped
        them would be an upgrade that re-pays for the whole memory. The rows are
        keyed by the same statement digest either way, so the move is a copy.
        """

        legacy = self.scope_directory / LEGACY_VECTORS_FILENAME
        if not legacy.is_file():
            return 0
        moved = 0
        try:
            connection.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))
            exists = connection.execute(
                "SELECT count(*) FROM legacy.sqlite_master "
                "WHERE type = 'table' AND name = 'vector'"
            ).fetchone()
            if exists and int(exists[0] or 0):
                present = {
                    str(row[1])
                    for row in connection.execute("PRAGMA legacy.table_info(vector)")
                }
                # The columns are named rather than taken in order, and the kind is
                # read from whichever name the file has: a version-5 file also had
                # a `source` column and called the kind `type`. The copy has to work
                # from either shape or the upgrade costs a re-embedding.
                kind_column = "kind" if "kind" in present else "type"
                if {
                    "unit_key",
                    "stamp",
                    kind_column,
                    "model",
                    "dimension",
                    "components",
                } <= present:
                    connection.execute(
                        "INSERT OR IGNORE INTO vector"
                        "(unit_key, stamp, kind, model, dimension, components) "
                        f"SELECT unit_key, stamp, {kind_column}, model, dimension, "
                        "components FROM legacy.vector"
                    )
                    moved = int(
                        connection.execute("SELECT changes()").fetchone()[0] or 0
                    )
            connection.commit()
        except sqlite3.DatabaseError:
            # A file this version cannot read is not worth failing a read over: it
            # is left where it is and the vectors are simply rebuilt.
            pass
        finally:
            try:
                connection.execute("DETACH DATABASE legacy")
            except sqlite3.DatabaseError:
                pass
        if moved:
            self.migrated_vectors = moved
        for suffix in ("", "-wal", "-shm"):
            Path(f"{legacy}{suffix}").unlink(missing_ok=True)
        return moved

    def _vector_table_is_older_shape(self, connection: sqlite3.Connection) -> bool:
        """Report whether the vector table is not the shape this version writes."""

        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'vector'"
        ).fetchone()
        if row is None:
            return False
        present = {
            str(record[1]) for record in connection.execute("PRAGMA table_info(vector)")
        }
        return present != set(VECTOR_COLUMNS)

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

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        """Bring one connection's file to this version's shape.

        Exposed for the vector store, which opens the same file and must not
        create or judge a schema of its own.
        """

        self._create_schema(connection)

    def drop_vectors(self) -> int:
        """Drop every vector in this scope, and return how many went."""

        connection = self._open()
        present = connection.execute("SELECT count(*) FROM vector").fetchone()
        count = int(present[0] or 0) if present else 0
        connection.execute("DROP TABLE IF EXISTS vector")
        connection.executescript(VECTOR_SCHEMA)
        connection.commit()
        return count

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS unit USING fts5("
            "text, unit_key UNINDEXED, kind UNINDEXED, stamp UNINDEXED, "
            "normalized UNINDEXED, added_at, recalls, last_recalled_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)"
        )
        connection.executescript(VECTOR_SCHEMA)
        if self._vector_table_is_older_shape(connection):
            # A table from a version whose columns differ is not read. It is
            # dropped, which also collects the vectors of statements that have
            # since been reworded, and the write side rebuilds what is missing.
            connection.execute("DROP TABLE vector")
            connection.executescript(VECTOR_SCHEMA)
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is not None and str(row[0]) != SCHEMA_VERSION:
            # A record from another shape is not read. The statements in it are
            # the memory, so they are not dropped: what is in the document beside
            # the file is imported instead, which is the only copy a version-5
            # file has. Anything that file held and the document does not is a
            # statement this version cannot see, and the version is bumped again
            # in the release notes when that is a loss.
            self._legacy_rows = self._read_legacy_unit(connection)
            connection.execute("DROP TABLE IF EXISTS unit")
            connection.execute("DELETE FROM meta")
            connection.execute(
                "CREATE VIRTUAL TABLE unit USING fts5("
                "text, unit_key UNINDEXED, kind UNINDEXED, stamp UNINDEXED, "
                "normalized UNINDEXED, added_at, recalls, last_recalled_at)"
            )
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._adopt_legacy_vectors(connection)
        if self._legacy_rows:
            # This file's own rows, not the document beside it. An export holds
            # the statements and nothing else — no kinds, no dates, no counts — so
            # restoring from it would quietly file every statement as `ITEM` and
            # lose the history. The document is the fallback for a file that has no
            # rows of its own, which is a genuine recovery.
            self._adopt_legacy_unit(connection, self._legacy_rows)
            self._legacy_rows = []
        connection.commit()

    def _read_legacy_unit(
        self, connection: sqlite3.Connection
    ) -> list[tuple[str, ...]]:
        """Return this file's statements as they were, in the shape it held them."""

        columns = {
            str(record[1]) for record in connection.execute("PRAGMA table_info(unit)")
        }
        if not columns:
            return []
        # A file from before 7 held the field as `type`; it is the same value
        # under the name it is now read as, so either column is accepted. The
        # history columns came with the record itself, so they are read where they
        # are there and defaulted where an older file has none.
        source = "kind" if "kind" in columns else "type"
        wanted = [
            name for name in ("text", "unit_key", source, "stamp") if name in columns
        ]
        for name in ("added_at", "recalls", "last_recalled_at"):
            if name in columns:
                wanted.append(name)
        query = ", ".join(wanted)
        try:
            rows = connection.execute(f"SELECT {query} FROM unit").fetchall()
        except sqlite3.DatabaseError:
            return []
        return [tuple(str(value) for value in row) for row in rows]

    def _adopt_legacy_unit(
        self,
        connection: sqlite3.Connection,
        rows: list[tuple[str, ...]],
    ) -> int:
        """Restore this file's statements, with the history they carried.

        A file of an older shape is a better source than the document beside it,
        because an export holds the statements and nothing else. The kinds, the
        dates, and the counts come back with the words; a file that had no history
        columns gets an empty one, which is the truth about it.
        """

        moved = 0
        for row in rows:
            text, key, kind, stamp = row[0], row[1], row[2], row[3]
            history = row[4:]
            connection.execute(
                "INSERT OR IGNORE INTO unit"
                "(text, unit_key, kind, stamp, normalized, added_at, recalls, "
                "last_recalled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    text,
                    key,
                    kind,
                    stamp,
                    normalise(text),
                    history[0] if len(history) > 0 and history[0] else None,
                    int(history[1]) if len(history) > 1 and history[1] else 0,
                    history[2] if len(history) > 2 and history[2] else None,
                ),
            )
            moved += 1
        return moved

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

    # -- the record ------------------------------------------------------------

    def statements(self) -> list[Statement]:
        """Return every statement in this scope, newest first, as the record holds it.

        This is the memory. Nothing reads the document to know what is in it, and
        nothing derives the row list from anything else.
        """

        connection = self._open()
        rows = connection.execute(
            "SELECT text, unit_key, kind, stamp, added_at, recalls, "
            "last_recalled_at FROM unit ORDER BY CAST(stamp AS INTEGER)"
        ).fetchall()
        return [
            Statement(
                kind=str(kind),
                text=str(text),
                key=str(key),
                position=int(stamp or 0),
                added_at=added_at or None,
                recalls=int(recalls or 0),
                last_recalled_at=last_recalled or None,
            )
            for text, key, kind, stamp, added_at, recalls, last_recalled in rows
        ]

    def statements_by_kind(self, kind: str) -> set[str]:
        """Return the identity of every statement filed under this kind."""

        connection = self._open()
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT unit_key FROM unit WHERE kind = ?", (kind,)
            )
        }

    def insert(
        self,
        text: str,
        kind: str,
        *,
        when: str | None = None,
        replace_kind: str | None = None,
    ) -> tuple[str, int]:
        """Put one statement at the top of the record, and return its key and what it replaced.

        A statement's place is its ordinal counting from the top, so inserting
        renumbers the ones below it. Renumbering is one update per statement
        rather than a rewrite of the file, and it is the price of a document that
        reads newest first.

        ``replace_kind`` removes every statement already filed under it first, which
        is how a handoff replaces the previous one without a second call and
        without the caller having to know a handoff exists.
        """

        connection = self._open()
        # Re-recording the same words is one statement, not two, so the first date
        # it was recorded is the date it keeps: how long something has been known
        # is not reset by a caller saying it again.
        previous = connection.execute(
            "SELECT added_at, recalls FROM unit WHERE unit_key = ?",
            (unit_key(f"{kind}: {text}"),),
        ).fetchone()
        replaced = 0
        if replace_kind is not None:
            cursor = connection.execute(
                "DELETE FROM unit WHERE kind = ?", (replace_kind,)
            )
            replaced = int(cursor.rowcount or 0)
        connection.execute("UPDATE unit SET stamp = CAST(stamp AS INTEGER) + 1")
        key = unit_key(f"{kind}: {text}")
        # FTS5 has no unique constraint to enforce identity on, so it is enforced
        # here: without the delete, recording the same words twice would leave two
        # rows of one statement rather than one row and one count.
        connection.execute("DELETE FROM unit WHERE unit_key = ?", (key,))
        connection.execute(
            "INSERT INTO unit"
            "(text, unit_key, kind, stamp, normalized, added_at, recalls, "
            "last_recalled_at) VALUES (?, ?, ?, 0, ?, ?, ?, NULL)",
            (
                text,
                key,
                kind,
                normalise(text),
                when or (previous[0] if previous else None) or _now(),
                int(previous[1] or 0) if previous else 0,
            ),
        )
        connection.commit()
        return key, replaced

    def forget(self, normalized: str) -> list[Statement]:
        """Remove the statements whose text is exactly this, and return them.

        Nothing is removed unless it matched exactly, and a text that matches more
        than one statement removes none of them: the whole point of an exact match
        is that a caller cannot lose two statements by asking vaguely.
        """

        found = [
            statement
            for statement in self.statements()
            if statement.normalized == normalized
        ]
        if not found or len(found) > 1:
            return found
        connection = self._open()
        connection.execute("DELETE FROM unit WHERE unit_key = ?", (found[0].key,))
        connection.commit()
        return found

    def replace_all(self, statements: list[Statement]) -> dict[str, int]:
        """Make the record hold exactly these statements, in this order.

        What a person editing the document means. A statement whose words are
        unchanged keeps its date and its count, because it is the same statement
        however the file around it was arranged; one that is new is dated now; and
        one that is gone is dropped with its history, which is the point of the
        edit. The number of each is returned, so a caller can say what the edit
        did rather than only that it was saved.
        """

        connection = self._open()
        before = {
            str(row[0]): (row[1], int(row[2] or 0))
            for row in connection.execute(
                "SELECT unit_key, added_at, recalls FROM unit"
            )
        }
        keep = {statement.key for statement in statements}
        removed = [key for key in before if key not in keep]
        for key in removed:
            connection.execute("DELETE FROM unit WHERE unit_key = ?", (key,))
        for position, statement in enumerate(statements):
            added_at, recalls = before.get(statement.key, (None, 0))
            connection.execute("DELETE FROM unit WHERE unit_key = ?", (statement.key,))
            connection.execute(
                "INSERT INTO unit"
                "(text, unit_key, kind, stamp, normalized, added_at, recalls, "
                "last_recalled_at) VALUES (?, ?, ?, ?, ?, ?, ?, "
                "(SELECT last_recalled_at FROM unit WHERE unit_key = ?))",
                (
                    statement.text,
                    statement.key,
                    statement.kind,
                    str(position),
                    normalise(statement.text),
                    added_at or _now(),
                    recalls,
                    statement.key,
                ),
            )
        connection.commit()
        return {
            "kept": len(before) - len(removed),
            "added": len(statements) - (len(before) - len(removed)),
            "removed": len(removed),
            "vectors_removed": self.collect_vectors(),
        }

    def collect_vectors(self) -> int:
        """Drop the vectors of statements the record no longer holds.

        The record is the memory, so a vector whose statement is gone is not about
        anything that exists, and the file would otherwise grow with them. A set
        difference over keys rather than a pass, and it is the reindex that runs it
        because deciding it is a whole-scope call.
        """

        connection = self._open()
        keys = {
            str(row[0]) for row in connection.execute("SELECT unit_key FROM vector")
        }
        stale = [key for key in keys if key not in self.unit_keys()]
        if not stale:
            return 0
        connection.execute("BEGIN")
        for start in range(0, len(stale), 500):
            chunk = stale[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            connection.execute(
                f"DELETE FROM vector WHERE unit_key IN ({placeholders})", chunk
            )
        connection.commit()
        return len(stale)

    def _renumber(self) -> None:
        """Rewrite every position from the rows' current order, newest first."""

        connection = self._open()
        rows = connection.execute(
            "SELECT unit_key FROM unit ORDER BY CAST(stamp AS INTEGER)"
        ).fetchall()
        for position, (key,) in enumerate(rows):
            connection.execute(
                "UPDATE unit SET stamp = ? WHERE unit_key = ?",
                (str(position), str(key)),
            )
        connection.commit()

    def export_digest(self) -> str:
        """Return the digest of the document this record would be written out as.

        What the page is handed, so it can tell whether the memory changed while
        the page was open rather than overwriting somebody else's edit.
        """

        return hashlib.sha256(self.export().encode("utf-8")).hexdigest()

    def export(self) -> str:
        """Return the document this record would be written out as."""

        return render_document(self.statements())

    def write_export(self) -> bool:
        """Write the document out, and report whether it was out of date.

        The document is an export, so a hand edit to it is not read and not kept:
        the record is written over it and the caller is told, because an edit that
        appeared to work and then vanished is worse than one that never took.
        """

        wanted = self.export()
        if read_standing(self.scope_directory) == wanted:
            return False
        write_standing(self.scope_directory, wanted)
        return True

    def _adopt_document(self, connection: sqlite3.Connection, document: str) -> bool:
        """Import the statements a document holds, and return whether it held any.

        This is the upgrade path and the recovery path both: a file from a version
        that kept the document as the record, read once and then written back out
        by the first export. It is not a hand edit, because it runs only when the
        record has nothing in it.
        """

        if connection.execute("SELECT count(*) FROM unit").fetchone()[0]:
            return False
        adopted = False
        for parsed in parse_document(document):
            connection.execute(
                "INSERT OR IGNORE INTO unit"
                "(text, unit_key, kind, stamp, normalized, added_at, recalls, "
                "last_recalled_at) VALUES (?, ?, ?, ?, ?, NULL, 0, NULL)",
                (
                    parsed.text,
                    parsed.key,
                    parsed.kind,
                    str(parsed.position),
                    normalise(parsed.text),
                ),
            )
            adopted = True
        return adopted

    def adopt_document_if_empty(self) -> int:
        """Read the document into an empty record, and return how many came in.

        What the document holds is the statements: their words and their order. The
        kind of a recovered statement is ``ITEM`` and its date and count are empty,
        because those were in the record and not in the file. This runs when the
        record has nothing in it, which is both an upgrade and a recovery, and
        never over a record that has something.
        """

        connection = self._open()
        moved = self._adopt_document(connection, read_standing(self.scope_directory))
        connection.commit()
        return (
            int(connection.execute("SELECT count(*) FROM unit").fetchone()[0] or 0)
            if moved
            else 0
        )

    # -- a statement's history ------------------------------------------------

    def note_recalled(self, keys: Sequence[str], *, when: str | None = None) -> None:
        """Count that these statements were handed to a caller.

        One ``UPDATE`` per key inside the connection the read already holds, so
        the work is proportional to the answer rather than to the memory, and
        SQLite's own transaction makes it atomic. A statement with a date is dated
        by the write that recorded it; one this server never recorded is counted
        without a date, because a re-read is not a time of writing.
        """

        wanted = [key for key in dict.fromkeys(keys) if key]
        if not wanted:
            return
        stamp = when or _now()
        connection = self._open()
        for key in wanted:
            connection.execute(
                "UPDATE unit SET recalls = CAST(recalls AS INTEGER) + 1, "
                "last_recalled_at = ? WHERE unit_key = ?",
                (stamp, key),
            )
        connection.commit()

    def history(self, keys: Sequence[str]) -> dict[str, dict[str, object]]:
        """Return these statements' histories, and nothing for the rest."""

        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return {}
        connection = self._open()
        found: dict[str, dict[str, object]] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = connection.execute(
                f"SELECT unit_key, added_at, recalls, last_recalled_at FROM unit "
                f"WHERE unit_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for key, added_at, recalls, last_recalled in rows:
                found[str(key)] = {
                    "added_at": added_at or None,
                    "recalls": int(recalls or 0),
                    "last_recalled_at": last_recalled,
                }
        return found

    def count_units(self) -> int:
        """Return how many units this index holds, without materialising them.

        A disclosure number, so it is two counts rather than a walk: the read
        path must not pay per unit to say how many are waiting.
        """

        connection = self._open()
        row = connection.execute("SELECT count(*) FROM unit").fetchone()
        return int(row[0]) if row else 0

    def unit_keys(self) -> set[str]:
        """Return the identity of every statement this record holds."""

        connection = self._open()
        return {str(row[0]) for row in connection.execute("SELECT unit_key FROM unit")}

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
                "SELECT unit_key, text, kind, stamp FROM unit "
                f"WHERE unit_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for key, text_value, kind, stamp in rows:
                found[str(key)] = {
                    "text": str(text_value),
                    "kind": str(kind),
                    "stamp": str(stamp),
                }
        return found

    def search(
        self,
        query: str,
        limit: int,
        kind: str | None = None,
    ) -> tuple[list[dict[str, object]], str]:
        """Return the statements matching a query, and how they were matched.

        The query's words are required to be present together first, which is
        what a question like "where does ref000137 keep the draft" means and what
        an index answers without visiting the rest of the memory. When nothing
        holds every word, the rarer words are tried on their own, because an
        over-strict conjunction returning nothing is worse than a broader one.

        A ``kind`` narrows the answer to one category. The kind is a column
        rather than a word in the text, so a caller that wants a category asks for
        it and a query that merely names one finds nothing — which is the honest
        answer, because the text does not contain it.
        """

        terms = [term for term in query_terms(query) if term]
        if not terms:
            return [], "no-terms"

        connection = self._open()
        rows = self._match(
            connection,
            " ".join(f'"{term}"' for term in terms),
            limit,
            kind,
        )
        mode = "all-words"
        if not rows:
            rarer = self._rarest(connection, terms)[:2]
            if rarer:
                rows = self._match(
                    connection,
                    " OR ".join(f'"{term}"' for term in rarer),
                    limit,
                    kind,
                )
                mode = "rarest-words" if rows else "all-words"
        return rows, mode

    def _match(
        self,
        connection: sqlite3.Connection,
        expression: str,
        limit: int,
        kind: str | None = None,
    ) -> list[dict[str, object]]:
        """Run one FTS5 query, ordered by the index's own ranking.

        A kind narrows the query rather than being part of it. The kind is not in
        the text — it is a column, and a file of tagged lines is not what a memory
        should be — so ``PLAN`` as a word finds nothing and the column is what a
        caller asking for a category gets.
        """

        clause = "unit MATCH ?"
        parameters: list[object] = [expression]
        if kind is not None:
            clause += " AND kind = ?"
            parameters.append(kind)
        try:
            cursor = connection.execute(
                f"SELECT text, unit_key, kind, stamp FROM unit "
                f"WHERE {clause} ORDER BY rank LIMIT ?",
                (*parameters, max(int(limit), 1) + 1),
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
                "stamp": stamp,
            }
            for text, key, kind, stamp in found
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
