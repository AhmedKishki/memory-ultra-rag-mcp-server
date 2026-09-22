"""The surface, the child environment, and the command line."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from mcp.types import CallToolRequestParams, ListToolsRequest
from mcp.types import Tool as MCPTool

from memory_ultra_rag_mcp import server
from memory_ultra_rag_mcp.config import ServerConfig
from memory_ultra_rag_mcp.manifest import MEMORY_TOOLS, STORAGE_ENV_VAR


def _config(tmp_path: Path) -> ServerConfig:
    checkout = tmp_path / "UltraRAG"
    checkout.mkdir()
    return ServerConfig(
        ultrarag_root=checkout,
        workspace_root=tmp_path / "workspace",
        storage_root=tmp_path / "workspace" / "ui-storage",
        python_executable=Path("/usr/bin/python3"),
    )


def test_the_child_environment_points_at_the_checkout_and_the_storage_root(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    env = server.child_environment(config)

    assert env["PYTHONPATH"].split(":")[0] == str(config.ultrarag_root / "src")
    assert env[STORAGE_ENV_VAR] == str(config.storage_root)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["ULTRARAG_LOG_TS"].startswith("memory_")


def test_the_surface_is_narrowed_to_the_memory_tools() -> None:
    """M4: the upstream pipeline `build` tool is not part of this surface."""
    middleware = server.MemorySurfaceOnly()
    context: MiddlewareContext[ListToolsRequest] = MiddlewareContext(
        message=ListToolsRequest(method="tools/list")
    )

    async def call_next(_: MiddlewareContext[ListToolsRequest]) -> list[MCPTool]:
        return [
            MCPTool(name="build", inputSchema={"type": "object"}),
            MCPTool(name="get_global_memory", inputSchema={"type": "object"}),
            MCPTool(name="save_memory", inputSchema={"type": "object"}),
        ]

    listed = asyncio.run(middleware.on_list_tools(context, call_next))
    assert [tool.name for tool in listed] == list(MEMORY_TOOLS)

    calls: MiddlewareContext[CallToolRequestParams] = MiddlewareContext(
        message=CallToolRequestParams(name="build", arguments={})
    )
    with pytest.raises(ToolError, match="not part of this server's surface"):
        asyncio.run(middleware.on_call_tool(calls, call_next))


def test_the_surface_allows_the_memory_tools_through() -> None:
    middleware = server.MemorySurfaceOnly()
    context: MiddlewareContext[CallToolRequestParams] = MiddlewareContext(
        message=CallToolRequestParams(name="get_global_memory", arguments={})
    )

    async def call_next(_: MiddlewareContext[CallToolRequestParams]) -> str:
        return "forwarded"

    assert asyncio.run(middleware.on_call_tool(context, call_next)) == "forwarded"


def test_the_command_line_requires_a_checkout(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ULTRARAG_ROOT", raising=False)
    assert server.main([]) == 2
    assert "--ultrarag-root is required" in capsys.readouterr().err


def test_the_command_line_reports_a_bad_checkout(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert server.main(["--ultrarag-root", str(tmp_path / "absent")]) == 2
    assert "not a directory" in capsys.readouterr().err
