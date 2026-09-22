"""The two memory kinds this server serves, and how a scope maps to storage.

UltraRAG's memory is scoped by a single directory name, which upstream calls
``user_id``. That is enough for one kind of memory and not for two, so the two
kinds are kept apart by a naming rule that needs no change to upstream and no new
storage format:

* **global memory** keeps the caller's own ``user_id``, so the files UltraRAG
  already writes for a user stay exactly where a UltraRAG UI reads them;
* **local memory** is scoped to a project, and its scope is that project's
  identifier under a reserved prefix.

The prefix is what keeps the two kinds apart in one directory tree, so an
identifier beginning with it is reserved for local memory and refused as a
global one.
"""

from __future__ import annotations

import re

from .manifest import LOCAL_SCOPE_PREFIX

__all__ = [
    "ScopeError",
    "global_scope",
    "is_reserved_scope",
    "local_scope",
]

#: Identifiers name a directory segment, so they are narrow on purpose: the
#: same shape upstream accepts, which is also a shape that cannot escape.
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

MAX_IDENTIFIER_LENGTH = 64


class ScopeError(ValueError):
    """Raised when a scope identifier cannot name a memory directory."""


def _validate(identifier: str, what: str) -> str:
    candidate = str(identifier).strip()
    if not candidate:
        raise ScopeError(f"{what} must not be empty")
    if len(candidate) > MAX_IDENTIFIER_LENGTH:
        raise ScopeError(f"{what} must be at most {MAX_IDENTIFIER_LENGTH} characters")
    if not IDENTIFIER_PATTERN.fullmatch(candidate):
        raise ScopeError(
            f"{what} may only contain letters, digits, '_' and '-', not {identifier!r}"
        )
    return candidate


def local_scope(project_id: str) -> str:
    """Return the storage scope a project's local memory lives in."""
    return f"{LOCAL_SCOPE_PREFIX}{_validate(project_id, 'project_id')}"


def global_scope(user_id: str) -> str:
    """Return the storage scope a user's global memory lives in."""
    resolved = _validate(user_id, "user_id")
    if is_reserved_scope(resolved):
        raise ScopeError(
            f"the prefix {LOCAL_SCOPE_PREFIX!r} is reserved for local memory; "
            f"choose a different user_id than {user_id!r}"
        )
    return resolved


def is_reserved_scope(identifier: str) -> bool:
    """Return True when an identifier belongs to the local-memory namespace."""
    return str(identifier).startswith(LOCAL_SCOPE_PREFIX)
