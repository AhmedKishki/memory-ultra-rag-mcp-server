"""The local reranker's call into its runtime, and nothing that needs a model.

The seam is covered elsewhere with a fake reranker, which cannot catch a wrong
call into the real library: this file hands the real `LocalReranker` a stub
runtime and asserts it is driven the way the library documents, which is the one
thing that broke once and cost the feature its default.
"""

from __future__ import annotations

import pytest

from memory_ultra_rag_mcp.models import LocalReranker, ModelError


class _StubRuntime:
    """Records how the reranker called it, and scores by length."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], int]] = []

    def rerank(self, query, documents, batch_size=64, **kwargs):
        recorded = list(documents)
        self.calls.append((query, recorded, batch_size))
        return [float(len(document)) for document in recorded]


def _reranker() -> tuple[LocalReranker, _StubRuntime]:
    reranker = LocalReranker("Xenova/ms-marco-MiniLM-L-6-v2")
    runtime = _StubRuntime()
    reranker._runtime = runtime  # the seam, standing in for the fetched model
    return reranker, runtime


def test_the_runtime_is_given_the_query_once_and_the_documents_beside_it() -> None:
    """The library's signature is (query, documents) - not a list of pairs."""

    reranker, runtime = _reranker()
    scores = reranker.score("which rule applies?", ["a", "bb", "ccc"])

    assert runtime.calls == [("which rule applies?", ["a", "bb", "ccc"], 64)]
    assert scores == [1.0, 2.0, 3.0]


def test_no_candidates_scores_nothing_and_calls_nothing() -> None:
    reranker, runtime = _reranker()

    assert reranker.score("query", []) == []
    assert runtime.calls == []


def test_a_runtime_that_raises_becomes_a_model_error() -> None:
    """A read must degrade, not crash, when the cross-encoder fails."""

    class _Broken:
        def rerank(self, query, documents, batch_size=64, **kwargs):
            raise RuntimeError("no")

    reranker = LocalReranker("Xenova/ms-marco-MiniLM-L-6-v2")
    reranker._runtime = _Broken()

    with pytest.raises(ModelError, match="failed to score"):
        reranker.score("query", ["a candidate"])


def test_an_unknown_reranker_is_refused_by_name() -> None:
    with pytest.raises(ModelError, match="unknown reranker model"):
        LocalReranker("not/a-model")
