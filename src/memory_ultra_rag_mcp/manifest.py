"""The pinned UltraRAG baseline this server serves.

Nothing here is configurable. The whole point of this package is that it serves
*one* known revision of UltraRAG's own memory server, so the revision and the
entrypoint are constants that the configuration validates against, and a
different checkout is a deliberate change to this file rather than a flag.
"""

from __future__ import annotations

__all__ = [
    "BASELINE_COMMIT",
    "BASELINE_VERSION",
    "MEMORY_SERVER_ENTRYPOINT",
    "MEMORY_TOOLS",
    "STORAGE_ENV_VAR",
]

#: The UltraRAG revision this package was reviewed against (2026-09-22).
BASELINE_COMMIT = "3a709a2aea3fbe46acca59c422621c94b6e86857"

#: The version that revision declares, checked so a version string cannot lie.
BASELINE_VERSION = "0.3.0.2"

#: UltraRAG's own memory server, exactly as it ships in the source tree.
MEMORY_SERVER_ENTRYPOINT = "servers/memory/src/memory.py"

#: The complete memory surface. Upstream also registers a pipeline ``build``
#: tool through its server base class; that tool belongs to a UltraRAG pipeline
#: deployment and cannot work here, so the surface is narrowed to these two.
MEMORY_TOOLS = ("get_global_memory", "save_memory")

#: The environment variable UltraRAG uses to locate the storage the UI reads.
STORAGE_ENV_VAR = "ULTRARAG_UI_STORAGE_ROOT"
