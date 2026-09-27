"""What a read is allowed to do, in one object.

Retrieval has levers — a model, a floor, two weights, a depth, a reranker — and
every one of them is a decision someone could argue about. They live here, in one
frozen object read from one place, so the later measurement can sweep them
without a single call site changing, and so a reader can see the whole shape of
a lookup in one screen.

The defaults are the collection's measured ones rather than invented numbers:
weighted reciprocal rank fusion with ``rrf_k = 60``, BM25 weighted 1.25 against
dense 1.0, a cosine floor of 0.72, and a relative margin of 0.10 around the
query's best candidate. They are the sibling server's, measured on a document
corpus rather than on memory units, and the later phase is what decides whether
they hold here. Reranking is off, because nothing in this package may claim a
gain it has not measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .maintenance import EmbeddingWorker, embed_pending, warm_models
from .models import Embedder, ModelError, Reranker
from .read import answer_read

__all__ = ["Retrieval", "RetrievalSettings"]


@dataclass(frozen=True, slots=True)
class RetrievalSettings:
    """One read's policy: what to embed, how to match, what to admit, what to return."""

    #: The local model a scope's vectors are built with. Changing it invalidates
    #: them, by fingerprint, rather than mixing two spaces.
    embedding_model: str

    #: A dense candidate below this cosine is dropped, so a query with nothing to
    #: do with the memory abstains instead of receiving the nearest thing.
    cosine_floor: float = 0.72

    #: A candidate below the floor is admitted only when the query's own best
    #: candidate cleared the floor and this one is within this much of it.
    relative_margin: float = 0.10

    #: Reciprocal rank fusion: each side contributes ``weight / (rrf_k + rank)``.
    bm25_weight: float = 1.25
    dense_weight: float = 1.0
    rrf_k: int = 60

    #: How many candidates each side contributes before fusion. This is an
    #: internal depth, not the answer: the answer is exactly ``limit`` units, and
    #: every unit is text the caller has to read.
    pool_depth: int = 50

    #: A local cross-encoder to order the fused candidates with, or None for off.
    reranker_model: str | None = None

    #: How many fused candidates the reranker sees, so its cost is bounded even
    #: if the pool grows.
    rerank_depth: int = 20

    #: What a warm read may cost, asserted by a test so a later change cannot
    #: quietly make a blocking lookup slow. A read that finds the model unloaded
    #: is outside this bound and says so in its answer.
    read_ceiling_ms: float = 250.0

    def describe(self) -> dict[str, object]:
        """Return this policy, for a diagnostic surface and for a test."""

        return {
            "embedding_model": self.embedding_model,
            "cosine_floor": self.cosine_floor,
            "relative_margin": self.relative_margin,
            "bm25_weight": self.bm25_weight,
            "dense_weight": self.dense_weight,
            "rrf_k": self.rrf_k,
            "pool_depth": self.pool_depth,
            "reranker_model": self.reranker_model or "off",
            "rerank_depth": self.rerank_depth,
            "read_ceiling_ms": self.read_ceiling_ms,
        }


class Retrieval:
    """One process's retrieval: its models, its policy, and its per-scope workers.

    This is the single object the tools and the browser view share, so a page
    write and a tool write leave the same derived state behind them, and so a
    model is loaded once for the process rather than once per caller. Nothing
    here holds a scope's memory in memory: it holds a path, and the indexes are
    opened and closed around each operation.
    """

    def __init__(
        self,
        *,
        embedder: Embedder,
        policy: RetrievalSettings,
        reranker: Reranker | None = None,
    ) -> None:
        self.embedder = embedder
        self.policy = policy
        self.reranker = reranker
        self._workers: dict[str, EmbeddingWorker] = {}

    def worker_for(self, directory: Path) -> EmbeddingWorker:
        """Return this scope's worker, creating it once per process."""

        key = str(directory)
        if key not in self._workers:
            self._workers[key] = EmbeddingWorker(self.embedder, directory)
        return self._workers[key]

    def warm(self) -> dict[str, Any]:
        """Load the models now, so a later read finds them resident."""

        return warm_models(
            self.embedder, self.reranker.warm if self.reranker is not None else None
        )

    def record(self, directory: Path, content: str) -> dict[str, Any]:
        """Record one statement and queue its vector, reporting what it queued."""

        from .store import StoreError, append_statement

        try:
            digest = append_statement(directory, content)
        except StoreError as error:
            raise ModelError(str(error)) from error
        queued = embed_pending(
            directory,
            self.embedder,
            self.worker_for(directory),
            changed=directory / "MEMORY.md",
        )
        return {
            "status": "set",
            "directory": str(directory),
            "standing_document": str(directory / "MEMORY.md"),
            "sha256": digest,
            "rounds_directory": str(directory / "project"),
            "embedding_model": self.policy.embedding_model,
            "units_queued": queued,
            "embedded": queued == 0,
        }

    def maintain(self, directory: Path, written: Path) -> int:
        """Keep the derived state in step after a write that was not a tool call."""

        return embed_pending(
            directory, self.embedder, self.worker_for(directory), changed=written
        )

    def answer(
        self,
        *,
        scope: str,
        directory: Path,
        query: str,
        limit: int,
    ) -> dict[str, Any]:
        """Answer one read, in words and in meaning."""

        return answer_read(
            scope=scope,
            directory=directory,
            query=query,
            limit=limit,
            embedder=self.embedder,
            reranker=self.reranker,
            policy=self.policy,
        ).payload()
