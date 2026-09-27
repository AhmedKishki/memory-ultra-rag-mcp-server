"""One scope's vectors, in a file of their own.

The lexical index and this one are separated on purpose, because their
economics are opposites. ``index.sqlite3`` is rebuilt in seconds and can be
thrown away freely; this file costs one embedding per unit, so deleting it means
re-embedding everything that was recorded, and it is therefore never deleted as
part of anything else.

The record is still the Markdown. This file holds a vector per unit, keyed by a
digest of the unit's text, and a fingerprint of the model that produced it. A
statement whose words are edited is a different digest, so its old vector
becomes collectable rather than wrong, and an index built by one model is
refused rather than mixed with another's.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Self

__all__ = [
    "VECTORS_FILENAME",
    "VectorStore",
    "unit_key",
    "vector_path",
]

#: The vector file inside a scope directory, named so that it is obviously not
#: part of the memory.
VECTORS_FILENAME = "index-vectors.sqlite3"

#: Floats are stored as 32-bit, which is what the models produce and what a
#: cosine needs; keeping them wider would double the file for no gain.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS vector(
    unit_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    stamp TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    components BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def vector_path(scope_directory: Path) -> Path:
    """Return the vector file for one scope."""

    return scope_directory / VECTORS_FILENAME


def unit_key(text: str) -> str:
    """Return the stable identity of one unit.

    A digest of the unit's own words, not of its position: a block ordinal
    shifts when a hand edit inserts a block above it, and a vector keyed on
    position would then be silently attached to the wrong statement. Two units
    with the same words are the same unit as far as an answer is concerned, so
    they share one vector.
    """

    normalised = " ".join(text.casefold().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class VectorStore:
    """One scope's vectors: put, look up by key, and search by cosine."""

    def __init__(self, scope_directory: Path, model: str, dimension: int) -> None:
        self.scope_directory = scope_directory
        self.model = model
        self.dimension = dimension
        self.path = vector_path(scope_directory)
        self._connection: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        self.scope_directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(_SCHEMA)
        self._connection = connection
        return connection

    def close(self) -> None:
        """Close the file, if this object opened it."""

        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Self:
        self._open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def put(
        self,
        text: str,
        vector: Sequence[float],
        *,
        source: str,
        stamp: str,
        kind: str,
    ) -> str:
        """Store one unit's vector, and return its key.

        The recorded model and width are checked before the write, so a stale
        file from another model is never appended to silently.
        """

        if len(vector) != self.dimension:
            raise ValueError(
                f"vector has {len(vector)} dimensions, this store holds "
                f"{self.dimension}"
            )
        connection = self._open()
        self._refuse_foreign_model(connection)
        key = unit_key(text)
        connection.execute(
            "INSERT OR REPLACE INTO vector"
            "(unit_key, source, stamp, kind, model, dimension, components) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                source,
                stamp,
                kind,
                self.model,
                self.dimension,
                _pack(vector),
            ),
        )
        connection.commit()
        return key

    def _refuse_foreign_model(self, connection: sqlite3.Connection) -> None:
        """Raise if this file holds vectors from a different model."""

        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'embedding_model'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('embedding_model', ?)",
                (self.model,),
            )
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('dimension', ?)",
                (str(self.dimension),),
            )
            return
        if row[0] != self.model:
            raise ValueError(
                f"{self.path} holds vectors from {row[0]!r}, not {self.model!r}; "
                "delete the file to rebuild it with this model"
            )
        width = connection.execute(
            "SELECT value FROM meta WHERE key = 'dimension'"
        ).fetchone()
        if width is not None and int(width[0]) != self.dimension:
            raise ValueError(
                f"{self.path} holds {width[0]}-dimensional vectors, this model "
                f"produces {self.dimension}"
            )

    def model_is_foreign(self) -> bool:
        """Report whether this file was built by a different model."""

        connection = self._open()
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'embedding_model'"
        ).fetchone()
        return row is not None and row[0] != self.model

    # -- reading -----------------------------------------------------------

    def count(self) -> int:
        """Return how many vectors this store holds."""

        connection = self._open()
        row = connection.execute("SELECT count(*) FROM vector").fetchone()
        return int(row[0]) if row else 0

    def keys(self) -> set[str]:
        """Return the keys of the units this store can answer from."""

        connection = self._open()
        return {
            str(row[0]) for row in connection.execute("SELECT unit_key FROM vector")
        }

    def has(self, keys: Iterable[str]) -> set[str]:
        """Return which of these keys this store already holds."""

        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return set()
        connection = self._open()
        found: set[str] = set()
        # SQLite's variable limit is high but finite, so the lookup is chunked.
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = connection.execute(
                f"SELECT unit_key FROM vector WHERE unit_key IN ({placeholders})",
                chunk,
            )
            found.update(str(row[0]) for row in rows)
        return found

    def forget(self, keys: Iterable[str]) -> int:
        """Drop these units' vectors, and return how many were dropped.

        Used when a unit's text changed: the old digest is not the unit any more,
        so its vector is not about anything that exists.
        """

        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return 0
        connection = self._open()
        dropped = 0
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            cursor = connection.execute(
                f"DELETE FROM vector WHERE unit_key IN ({placeholders})",
                chunk,
            )
            dropped += int(cursor.rowcount or 0)
        connection.commit()
        return dropped

    def search(
        self,
        query_vector: Sequence[float],
        pool: int,
        floor: float,
        margin: float,
    ) -> list[tuple[str, float]]:
        """Return the nearest units to a query vector, and their cosine scores.

        An exact scan, not an approximate index: a memory holds thousands of
        units, which is a few milliseconds of arithmetic, and an approximate
        index would have to be opened on every one of a blocking read. Units
        below ``floor`` are dropped, except that a unit within ``margin`` of the
        query's own best candidate is admitted, so a single strong match is not
        thrown away with the rest.
        """

        connection = self._open()
        rows = connection.execute(
            "SELECT unit_key, components FROM vector WHERE model = ? AND dimension = ?",
            (self.model, self.dimension),
        ).fetchall()
        if not rows:
            return []

        scored = [
            (str(key), _cosine(query_vector, _unpack(blob))) for key, blob in rows
        ]
        best = max(score for _, score in scored)
        if best < floor:
            return []
        allowed = best - margin
        scored = [item for item in scored if item[1] >= floor or item[1] >= allowed]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: max(int(pool), 1)]


def _pack(values: Sequence[float]) -> bytes:
    """Return one vector as 32-bit floats, little-endian."""

    import array

    packed = array.array("f", (float(value) for value in values))
    if packed.itemsize != 4:  # pragma: no cover - every platform this runs on
        raise RuntimeError("32-bit floats are required for the vector store")
    return packed.tobytes()


def _unpack(blob: bytes) -> tuple[float, ...]:
    """Return one stored vector as floats."""

    import array

    packed = array.array("f")
    packed.frombytes(blob)
    return tuple(packed)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Return the cosine of two vectors that are each already at unit length."""

    if len(left) != len(right):
        raise ValueError(f"vector widths differ: {len(left)} and {len(right)}")
    return math.fsum(a * b for a, b in zip(left, right, strict=True))
