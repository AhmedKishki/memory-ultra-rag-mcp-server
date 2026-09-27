"""Run the stdio server with a hand-written model, for the integration tests.

The transport is what an integration test is for: a real process, a real pipe,
the real server object. The model is not — it would make every run fetch 65 MB
and put a model's opinion inside an assertion about isolation. So the child is
this file: the same ``create_server``, the same stdio, and the fake from
``tests/fakes.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeEmbedder

from memory_ultra_rag_mcp.config import resolve_config
from memory_ultra_rag_mcp.retrieval import Retrieval, RetrievalSettings
from memory_ultra_rag_mcp.server import create_server


def main() -> None:
    """Serve the same server over stdio, on a model that costs nothing."""

    arguments = sys.argv[1:]
    project = arguments[arguments.index("--project-root") + 1]
    storage = (
        arguments[arguments.index("--storage-root") + 1]
        if "--storage-root" in arguments
        else None
    )
    embedder = FakeEmbedder()
    retrieval = Retrieval(
        embedder=embedder,
        policy=RetrievalSettings(embedding_model=embedder.identity.name),
    )
    config = resolve_config(project_root=project, storage_root=storage)
    create_server(config, retrieval=retrieval, warm=True).run(
        transport="stdio", show_banner=False
    )


if __name__ == "__main__":
    main()
