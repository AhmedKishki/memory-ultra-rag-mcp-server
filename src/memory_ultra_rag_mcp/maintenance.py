"""Keeping the derived state in step with the record, on the write side.

Three rules, in the order they happen:

1. **Durability first.** The statement or the exchange is in the Markdown before
   anything here runs, so nothing in this module can lose a memory.
2. **No work proportional to the memory.** Recording a statement re-parses the
   standing document, which is small and curated by design; recording an exchange
   re-parses the one dated file it was appended to. Neither walks the rest of
   the memory, and neither embeds more than the units that are actually new.
3. **A write never fails because of the lookup layer.** If the model is
   unavailable the units are left pending, the write says so, and the next write
   or an explicit reindex picks them up.

This is also where the deliberate trade is made explicit. Maintaining the index
here rather than on the read path is what keeps a lookup O(1) in the number of
files; the cost is that a file edited in place, behind our back, is not noticed
by a read. :func:`reindex` exists for that, and a read reports when the index
may be behind rather than pretending otherwise.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .index import MemoryIndex
from .models import Embedder, ModelError
from .vectors import VectorStore

__all__ = [
    "EmbeddingWorker",
    "PendingUnit",
    "embed_pending",
    "reindex",
    "warm_models",
]

#: How many pending units one drain pass will embed. Bounded so a maintenance
#: call cannot run for a minute, and so a reindex converges over several calls.
DEFAULT_BATCH = 64


@dataclass(frozen=True, slots=True)
class PendingUnit:
    """One unit waiting for a vector: the text, and where it came from."""

    key: str
    text: str
    kind: str
    source: str
    stamp: str


class EmbeddingWorker:
    """Produces vectors off the caller's thread, in small batches.

    One queue and one thread for the whole process, because a memory has one
    scope at a time here and a second worker would fight the first over the same
    SQLite file. A unit is embedded once: the queue is drained, the model is
    called with a batch, and the vectors are written. If the model is not
    available the batch is put back and the queue is left alone, so the next
    write or an explicit reindex retries it.
    """

    def __init__(self, embedder: Embedder, scope_directory: Path) -> None:
        self._embedder = embedder
        self._scope_directory = scope_directory
        self._queue: queue.Queue[PendingUnit | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()

    @property
    def pending(self) -> int:
        """Return how many units are queued right now."""

        return self._queue.qsize()

    def start(self) -> None:
        """Start the worker thread, if it is not already running."""

        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="memory-embedding", daemon=True
            )
            self._thread.start()

    def submit(self, unit: PendingUnit) -> None:
        """Queue one unit for a vector, and wake the worker."""

        self.start()
        self._idle.clear()
        self._queue.put(unit)

    def drain(self, timeout: float = 5.0) -> bool:
        """Wait for the queue to empty, and report whether it did.

        A test and the reindex command need the work to be *done*, not merely
        queued, so this waits for the worker rather than for the queue.
        """

        return self._idle.wait(timeout)

    def stop(self) -> None:
        """Ask the worker to finish and wait for it."""

        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=5.0)
        self._thread = None

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                if self._queue.empty():
                    self._idle.set()
                continue
            if item is None:
                self._idle.set()
                return
            self._embed_one(item)
            if self._queue.empty():
                self._idle.set()

    def _embed_one(self, unit: PendingUnit) -> None:
        try:
            vectors = self._embedder.embed_documents([unit.text])
        except ModelError:
            # The model is not available. Put the unit back, stop pretending the
            # queue is empty, and let a later write or a reindex retry it.
            self._queue.put(unit)
            self._idle.clear()
            return
        if not vectors:
            return
        identity = self._embedder.identity
        with VectorStore(
            self._scope_directory, identity.name, identity.dimension
        ) as store:
            try:
                store.put(
                    unit.text,
                    vectors[0],
                    source=unit.source,
                    stamp=unit.stamp,
                    kind=unit.kind,
                )
            except ValueError:
                # Another model's file: leave it for reindex rather than mixing.
                return


def embed_pending(
    scope_directory: Path,
    embedder: Embedder,
    worker: EmbeddingWorker | None,
    *,
    changed: Path | None = None,
) -> int:
    """Queue the units of a scope that have no vector, and return how many.

    ``changed`` narrows the work to the one file a write just wrote, which is
    what keeps a write proportional to that file rather than to the memory: a
    statement lands in the standing document, an exchange in one dated file.
    Without it the whole scope is considered, which is what ``reindex`` and the
    diagnostic surface want.
    """

    with MemoryIndex(scope_directory) as index:
        if changed is None:
            index.sync()
            source = None
        else:
            index.sync_file(changed)
            source = str(changed.relative_to(scope_directory))
        units = index.unit_keys(source)
        identity = embedder.identity
        with VectorStore(scope_directory, identity.name, identity.dimension) as store:
            have = store.has(units)
            missing = index.units_by_key(units - have)
    if worker is None:
        return len(missing)
    for key, row in missing.items():
        worker.submit(
            PendingUnit(key, row["text"], row["kind"], row["source"], row["stamp"])
        )
    return len(missing)


def reindex(
    scope_directories: Sequence[Path],
    embedder: Embedder | None = None,
    *,
    worker: EmbeddingWorker | None = None,
    batch: int = DEFAULT_BATCH,
) -> dict[str, Any]:
    """Bring one or more scopes' derived state fully up to date.

    This is the answer to "I edited the files by hand": the read path maintains
    what the writers write, and this catches everything else — a changed
    statement, an added or removed dated file, an index built by another model,
    or a vector file that was deleted.
    """

    report: dict[str, Any] = {"scopes": {}}
    for scope_directory in scope_directories:
        identity = embedder.identity if embedder is not None else None
        with MemoryIndex(scope_directory) as index:
            indexed = index.sync()
            units = index.unit_keys()
            store_report: dict[str, Any] = {
                "lexical": indexed,
                "units": len(units),
            }
            if identity is not None:
                with VectorStore(
                    scope_directory, identity.name, identity.dimension
                ) as store:
                    if store.model_is_foreign():
                        store_report["vectors"] = "foreign model; rebuilt"
                        for path in _vector_siblings(scope_directory):
                            path.unlink(missing_ok=True)
                        with VectorStore(
                            scope_directory, identity.name, identity.dimension
                        ) as fresh:
                            missing = units - fresh.has(units)
                    else:
                        missing = units - store.has(units)
                    store_report["vectors_held"] = store.count()
            else:
                missing = set()
            store_report["units_pending"] = len(missing)
        if embedder is not None:
            rows = index.units_by_key(missing)
            if worker is not None:
                for key, row in rows.items():
                    worker.submit(
                        PendingUnit(
                            key, row["text"], row["kind"], row["source"], row["stamp"]
                        )
                    )
            else:
                identity = embedder.identity
                with VectorStore(
                    scope_directory, identity.name, identity.dimension
                ) as store:
                    vectors = embedder.embed_documents(
                        [row["text"] for row in rows.values()]
                    )
                    for (key, row), vector in zip(rows.items(), vectors, strict=True):
                        store.put(
                            row["text"],
                            vector,
                            source=row["source"],
                            stamp=row["stamp"],
                            kind=row["kind"],
                        )
        report["scopes"][str(scope_directory)] = store_report
    return report


def _vector_siblings(scope_directory: Path) -> list[Path]:
    """Return this scope's vector file and the write-ahead files beside it."""

    from .vectors import vector_path

    path = vector_path(scope_directory)
    return [Path(f"{path}{suffix}") for suffix in ("", "-wal", "-shm")]


def warm_models(
    embedder: Embedder,
    reranker: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Load the models now, so a later read finds them resident.

    Loading is seconds, and a read that paid for it would stall a caller. The
    write side has no deadline, so it is the side that loads.
    """

    report: dict[str, Any] = {}
    try:
        embedder.warm()
        report["embedding_model"] = str(embedder.identity)
        report["embedding_loaded"] = True
    except ModelError as error:
        report["embedding_model"] = str(embedder.identity)
        report["embedding_loaded"] = False
        report["embedding_error"] = str(error)
    if reranker is not None:
        try:
            reranker()
            report["reranker_loaded"] = True
        except ModelError as error:
            report["reranker_loaded"] = False
            report["reranker_error"] = str(error)
    return report
