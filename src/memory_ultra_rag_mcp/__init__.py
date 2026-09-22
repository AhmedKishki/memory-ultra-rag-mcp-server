"""Serve UltraRAG's own memory over stdio MCP.

Two kinds of memory, one behaviour: the bound project's own memory inside
``.memory-rag``, and each user's global memory in UltraRAG's UI storage tree. See
``store.py`` for the format this package reproduces, ``server.py`` for the four
tools and the one deliberate difference from the upstream surface, ``ui.py`` for
the browser view, and ``README.md`` for the fidelity contract this package keeps.
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
