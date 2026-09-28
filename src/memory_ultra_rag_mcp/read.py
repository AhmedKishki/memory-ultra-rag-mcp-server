"""Answering one read: words, meaning, and only what matched.

This is the blocking path, so it is the one place where the cost rules are
strict. A read embeds its query once, scans the scope's vectors once, queries
the lexical index once, fuses the two sides, and returns. It does not walk the
scope's files, it does not embed anything but the query, and it does not load a
model: a lookup that has to wait for a load is a lookup that stalls its caller,
and the model is loaded on the serving side instead.

Three properties are decisions rather than details:

* **Both sides answer, and the answer says which.** A dense side alone is the
  weakest mode in the collection's own measurements, and its worst class is
  proper nouns, which is most of what a memory holds; a word side alone cannot
  find a statement that never used the caller's words. So a unit may arrive by
  words, by meaning, or by both, and it is labelled.
* **Nothing below the floor is admitted.** Without a floor a dense side always
  answers, always with the nearest thing it has, and always answering is worse
  than declining. A unit within the relative margin of the query's own best is
  the one exception, so a single strong match is not discarded with the rest.
* **A read reports what it could not do.** If the model is not resident, the
  answer says the semantic side was unavailable rather than quietly returning a
  worse result that looks like the same one. If the index may be behind because
  a file was edited in place, it says that too, and names the command that
  fixes it.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .index import MemoryIndex
from .models import Embedder, ModelError, Reranker
from .store import STATEMENT_LABEL_PATTERN, read_standing
from .vectors import VectorStore

if TYPE_CHECKING:
    # A type only: retrieval.py owns the object that calls in here, so importing
    # it at runtime would close a loop.
    from .retrieval import RetrievalSettings

__all__ = ["Answer", "answer_read"]


def _answered_text(kind: str, stored: str) -> str:
    """Return the text one unit is answered with.

    A statement is stored under its own label, and the index and the vectors keep
    that whole block: the label is a category the caller chose, and letting it be
    indexed is what makes a query naming the category find the statements filed
    under it. The answer drops the label, because a caller that recorded a
    statement wants the statement back, not the category it filed it under.

    Every unit in a scope is a statement now, so the check is kept only to make
    that assumption explicit: anything this store did not write is returned
    untouched, rather than losing its first words to a pattern meant for a label.
    """

    if kind != "statement":
        return stored
    return STATEMENT_LABEL_PATTERN.sub("", stored, count=1).strip()


@dataclass(slots=True)
class Answer:
    """One read's result, and everything a caller is told about how it was got."""

    scope: str
    query: str
    units: list[dict[str, Any]] = field(default_factory=list)
    matched_by: str = "lexical"
    semantic_available: bool = False
    index_behind: int = 0
    units_pending: int = 0
    truncated: bool = False
    reranked: bool = False
    elapsed_ms: float = 0.0

    def payload(self) -> dict[str, Any]:
        """Return the object a read answers with."""

        return {
            "scope": self.scope,
            "query": self.query,
            "matched_by": self.matched_by,
            "units": self.units,
            "returned": len(self.units),
            "truncated": self.truncated,
            "semantic_available": self.semantic_available,
            "units_pending": self.units_pending,
            "index_files_behind": self.index_behind,
            "reranked": self.reranked,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


def _fuse(
    lexical: Sequence[tuple[str, int]],
    dense: Sequence[tuple[str, float]],
    policy: RetrievalSettings,
) -> list[tuple[str, float, str, bool]]:
    """Return (unit_key, score, side, both) for the fused ranking.

    Weighted reciprocal rank fusion, as the collection's other server measured
    it: each side contributes ``weight / (rrf_k + rank)``, so the two are compared
    by position rather than by putting a BM25 score and a cosine on one scale.
    """

    scores: dict[str, float] = {}
    sides: dict[str, set[str]] = {}
    for rank, (key, _) in enumerate(lexical, start=1):
        scores[key] = scores.get(key, 0.0) + policy.bm25_weight / (policy.rrf_k + rank)
        sides.setdefault(key, set()).add("lexical")
    for rank, (key, _) in enumerate(dense, start=1):
        scores[key] = scores.get(key, 0.0) + policy.dense_weight / (policy.rrf_k + rank)
        sides.setdefault(key, set()).add("semantic")
    fused = [
        (key, score, "|".join(sorted(found)), len(found) == 2)
        for key, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        for found in (sides[key],)
    ]
    return fused


def answer_read(
    *,
    scope: str,
    directory: Path,
    query: str,
    limit: int,
    embedder: Embedder,
    reranker: Reranker | None,
    policy: RetrievalSettings,
) -> Answer:
    """Answer one read from a scope's derived state."""

    started = time.perf_counter()
    answer = Answer(scope=scope, query=query)

    lexical: list[tuple[str, int]] = []
    semantic: list[tuple[str, float]] = []
    units_by_key: dict[str, dict[str, str]] = {}

    pool = max(int(limit) + 1, policy.pool_depth)
    identity = embedder.identity
    # Upstream brings a scope into being on the first read, creating the
    # directory and the standing document from its template. That is how a
    # memory starts, and it is one small file, so it stays on the read path even
    # though the document is no longer what a read returns.
    read_standing(directory)
    with MemoryIndex(directory) as index:
        # Two stat calls, not a walk: this is what tells the caller the index may
        # be behind without paying to find out precisely.
        answer.index_behind = int(index.freshness()["files_behind"])

        lexical = _lexical(index, query, pool)
        units_by_key.update(index.units_by_key([key for key, _ in lexical]))

        with VectorStore(directory, identity.name, identity.dimension) as store:
            # Two counts, not a walk: the disclosure must not cost per unit.
            answer.units_pending = max(0, index.count_units() - store.count())
            if store.model_is_foreign():
                # Vectors from another model are not an answer, they are a
                # different question; the read says so and uses words.
                answer.semantic_available = False
            elif not getattr(embedder, "loaded", False):
                # Loading a model here would stall a caller mid-lookup. The read
                # says so instead, and the write side loads it for next time.
                answer.semantic_available = False
            else:
                try:
                    query_vector = embedder.embed_query(query)
                except ModelError:
                    answer.semantic_available = False
                else:
                    answer.semantic_available = True
                    semantic = store.search(
                        query_vector,
                        pool=policy.pool_depth,
                        floor=policy.cosine_floor,
                        margin=policy.relative_margin,
                    )
                    units_by_key.update(
                        index.units_by_key([key for key, _ in semantic])
                    )

    fused = _fuse(lexical, semantic, policy)
    # Each side is asked for one row past the pool, so a longer fused list than
    # the caller asked for means the answer is a selection, not the whole match.
    answer.truncated = len(fused) > max(int(limit), 1)
    ordered = fused[: max(int(limit), 1)]

    if reranker is not None and len(ordered) > 1:
        candidates = [
            units_by_key[key]["text"] for key, _, _, _ in ordered if key in units_by_key
        ]
        if candidates:
            scores = reranker.score(query, candidates)
            paired = [
                (item, score)
                for item, score in zip(ordered, scores, strict=False)
                if item[0] in units_by_key
            ]
            paired.sort(key=lambda entry: entry[1], reverse=True)
            ordered = [item for item, _ in paired]
            answer.reranked = True

    answer.units = [
        {
            "kind": units_by_key[key]["kind"],
            "text": _answered_text(
                units_by_key[key]["kind"], units_by_key[key]["text"]
            ),
            "source": units_by_key[key]["source"],
            "stamp": units_by_key[key]["stamp"],
            "matched_by": side,
            "score": round(score, 6),
        }
        for key, score, side, _both in ordered
        if key in units_by_key
    ]
    if semantic and not lexical:
        answer.matched_by = "semantic"
    elif lexical and semantic:
        answer.matched_by = "both"
    else:
        answer.matched_by = "lexical"
    answer.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return answer


def _lexical(index: MemoryIndex, query: str, pool: int) -> list[tuple[str, int]]:
    """Return the units the words found, best first, as (unit key, rank)."""

    found, _mode = index.search(query, pool)
    return [(str(row["unit_key"]), rank) for rank, row in enumerate(found, start=1)]
