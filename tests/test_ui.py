"""The browser view: the adapter over the four tools, and its own HTTP surface."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from memory_ultra_rag_mcp import ui
from memory_ultra_rag_mcp.config import ServerConfig, resolve_config
from memory_ultra_rag_mcp.server import create_server
from memory_ultra_rag_mcp.store import append_round, list_rounds, read_standing


def _config(tmp_path: Path) -> ServerConfig:
    project = tmp_path / "thesis"
    project.mkdir()
    return resolve_config(
        project_root=project,
        storage_root=tmp_path / "shared",
    )


def test_the_view_reports_the_two_scopes(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def scenario() -> dict:
        async with Client(create_server(config)) as client:
            adapter = ui.MemoryUIAdapter(config, client)
            return await adapter.call("memory_status", {})

    status = asyncio.run(scenario())

    scopes = status["scopes"]
    assert [entry["scope"] for entry in scopes] == ["local", "global:default"]
    assert scopes[0]["label"] == "This project"
    assert scopes[0]["directory"] == str(config.local_directory)
    assert scopes[0]["standing_present"] is False
    assert scopes[0]["round_count"] == 0
    assert scopes[0]["latest_round_date"] is None
    assert scopes[1]["directory"] == str(config.global_root / "default")


def test_rounds_are_read_newest_first_and_limited(tmp_path: Path) -> None:
    config = _config(tmp_path)
    for index in range(3):
        append_round(config.local_directory, [f"question {index}"], ["answer"])

    async def scenario() -> tuple[dict, dict]:
        async with Client(create_server(config)) as client:
            adapter = ui.MemoryUIAdapter(config, client)
            everything = await adapter.call("memory_rounds", {"scope": "local"})
            limited = await adapter.call(
                "memory_rounds", {"scope": "local", "limit": 2}
            )
            return everything, limited

    everything, limited = asyncio.run(scenario())
    assert everything["round_count"] == 3
    assert everything["truncated"] is False
    assert [entry["user"] for entry in everything["rounds"]] == [
        "question 2",
        "question 1",
        "question 0",
    ]
    assert limited["truncated"] is True
    assert len(limited["rounds"]) == 2
    assert limited["rounds"][0]["user"] == "question 2"
    assert limited["rounds"][0]["source_file"].endswith(".md")


def test_an_unknown_scope_is_refused(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def scenario() -> list[int]:
        codes = []
        async with Client(create_server(config)) as client:
            adapter = ui.MemoryUIAdapter(config, client)
            for scope in ("", "elsewhere", "global:../outside", "global:with/slash"):
                with pytest.raises(ui.UIRequestError) as failure:
                    await adapter.call("memory_standing", {"scope": scope})
                codes.append(failure.value.status_code)
        return codes

    assert asyncio.run(scenario()) == [400, 404, 400, 400]


def test_writing_a_round_goes_through_the_tool(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def scenario() -> dict:
        async with Client(create_server(config)) as client:
            adapter = ui.MemoryUIAdapter(config, client)
            return await adapter.call(
                "memory_append",
                {
                    "scope": "global:ahmed",
                    "user_message": "Where does the draft live?",
                    "assistant_message": "In the project directory.",
                },
            )

    saved = asyncio.run(scenario())

    assert saved["status"] == "saved"
    assert saved["scope"] == "global:ahmed"
    assert saved["round_count"] == 1
    daily = config.global_root / "ahmed" / "project"
    written = min(daily.glob("*.md"))
    assert saved["written"] == str(written)
    assert "- user: Where does the draft live?" in written.read_text(encoding="utf-8")
    # The round the page wrote is read back by the same parser the view serves, so
    # a write from the browser is in the format the agent's tools produce.
    rounds, _ = list_rounds(config.global_root / "ahmed")
    assert rounds[0]["user"] == "Where does the draft live?"
    assert rounds[0]["assistant"] == "In the project directory."


def test_the_standing_document_is_written_only_when_it_still_matches(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    async def scenario() -> tuple[str, str]:
        async with Client(create_server(config)) as client:
            adapter = ui.MemoryUIAdapter(config, client)
            read = await adapter.call("memory_standing", {"scope": "local"})
            saved = await adapter.call(
                "memory_standing_save",
                {
                    "scope": "local",
                    "content": "# MEMORY\nRemember the thesis deadline.\n",
                    "expected_sha256": read["sha256"],
                },
            )
            with pytest.raises(ui.UIRequestError) as failure:
                await adapter.call(
                    "memory_standing_save",
                    {
                        "scope": "local",
                        "content": "# MEMORY\nSomething else.\n",
                        "expected_sha256": read["sha256"],
                    },
                )
            return saved, str(failure.value)

    saved, refusal = asyncio.run(scenario())

    assert saved["status"] == "saved"
    assert saved["sha256"]
    assert "changed since it was read" in refusal
    assert read_standing(config.local_directory).endswith(
        "Remember the thesis deadline.\n"
    )


def test_the_profile_asks_for_no_document_workspace(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with TestClient(ui.create_ui_app(config)) as client:
        profile = client.get("/api/ui").json()
        page = client.get("/")
        health = client.get("/api/health").json()
        status = client.get("/api/status").json()
        search = client.post("/api/search", json={"query": "anything"})

    capabilities = profile["capabilities"]
    assert capabilities["documents"] is False
    assert capabilities["sources"] is False
    assert capabilities["ingestion"] is False
    assert capabilities["source_files"] is False
    assert capabilities["memory"] is True
    assert capabilities["memory_writes"] is True
    assert 'data-panel="search" data-capability="documents"' in page.text
    assert 'data-view="memory" data-capability="memory"' in page.text
    assert health["global_memory_root"] == str(config.global_root)
    assert status["project_root"] == str(config.project_root)
    assert status["ready"] is False
    assert search.status_code == 404


def test_the_command_line_refuses_a_taken_port(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        port = probe.getsockname()[1]
        code = ui.main(
            ["--project-root", str(config.project_root), "--port", str(port)]
        )

    assert code == 2
    assert "already in use" in capsys.readouterr().err


def test_the_command_line_requires_a_project_root(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ui.main([]) == 2
    assert "--project-root is required" in capsys.readouterr().err


def test_the_routes_serve_the_view(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with TestClient(ui.create_ui_app(config)) as client:
        page = client.get("/")
        profile = client.get("/api/ui").json()
        health = client.get("/api/health").json()
        status = client.get("/api/memory").json()
        rounds = client.get("/api/memory/rounds?scope=local&limit=5").json()
        standing = client.get("/api/memory/standing?scope=local").json()
        appended = client.post(
            "/api/memory/append",
            json={
                "scope": "local",
                "user_message": "Remember this.",
                "assistant_message": "Remembered.",
            },
        )
        saved = client.post(
            "/api/memory/standing",
            json={
                "scope": "local",
                "content": "# MEMORY\nKept for the thesis.\n",
                "expected_sha256": standing["sha256"],
            },
        )

    assert page.status_code == 200
    assert profile["application_name"] == "Memory UltraRAG"
    assert profile["capabilities"]["memory"] is True
    assert profile["capabilities"]["memory_writes"] is True
    assert health["local_memory"] == str(config.local_directory)
    assert [entry["scope"] for entry in status["scopes"]] == ["local", "global:default"]
    assert rounds["rounds"] == []
    assert standing["content"].startswith("# MEMORY")
    assert appended.json()["round_count"] == 1
    assert saved.json()["status"] == "saved"
    assert (config.local_directory / "MEMORY.md").read_text(encoding="utf-8") == (
        "# MEMORY\nKept for the thesis.\n"
    )


def test_the_view_lists_a_user_that_already_has_memory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    append_round(config.global_root / "ahmed", ["hello"], ["hi"])

    with TestClient(ui.create_ui_app(config)) as client:
        status = client.get("/api/memory").json()

    assert [entry["scope"] for entry in status["scopes"]] == [
        "local",
        "global:default",
        "global:ahmed",
    ]
    ahmed = status["scopes"][2]
    assert ahmed["round_count"] == 1
    assert ahmed["directory"] == str(config.global_root / "ahmed")
