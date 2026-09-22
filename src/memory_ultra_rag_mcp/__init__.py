"""Serve UltraRAG's own memory server over stdio MCP.

The implementation is one proxy: the pinned UltraRAG memory server runs as a
child process and its two tools are exposed unchanged. See ``server.py`` for the
one deliberate difference from the upstream surface, and ``README.md`` for the
fidelity contract this package keeps.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
