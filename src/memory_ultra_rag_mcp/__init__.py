"""Serve UltraRAG's own memory over stdio MCP.

Two kinds of memory, one behaviour: the bound project's own memory inside
``.memory-rag``, and each user's global memory in UltraRAG's UI storage tree. See
``store.py`` for the format this package reproduces, ``server.py`` for the four
tools and the one deliberate difference from the upstream surface, ``ui.py`` for
the browser view, and ``README.md`` for the fidelity contract this package keeps.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

#: The version is the distribution's, read from the one place that declares it.
#: A copy here is a number that disagrees with ``pyproject.toml`` as soon as a
#: release is cut, and a stdio server is asked for its version by its clients.
try:
    __version__ = _distribution_version("memory-ultra-rag-mcp")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
