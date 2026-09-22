"""Global and local memory are two scopes of one storage tree."""

from __future__ import annotations

import pytest

from memory_ultra_rag_mcp.scopes import (
    ScopeError,
    global_scope,
    is_reserved_scope,
    local_scope,
)


def test_local_memory_lives_under_a_reserved_prefix() -> None:
    assert local_scope("thesis") == "local-thesis"
    assert is_reserved_scope("local-thesis") is True
    assert is_reserved_scope("ahmed") is False


def test_global_memory_keeps_the_users_own_identifier() -> None:
    assert global_scope("ahmed") == "ahmed"


def test_the_reserved_prefix_cannot_be_used_as_a_global_identifier() -> None:
    """Two kinds of memory must not land in one directory by accident."""
    with pytest.raises(ScopeError, match="reserved for local memory"):
        global_scope("local-thesis")


@pytest.mark.parametrize(
    "identifier",
    ["../escape", "a/b", "with space", "dot.name", "", "x" * 65, "ünïcode"],
)
def test_an_identifier_that_cannot_name_a_directory_is_refused(
    identifier: str,
) -> None:
    with pytest.raises(ScopeError):
        local_scope(identifier)
