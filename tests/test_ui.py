"""The browser view: the adapter over the four tools, and its own HTTP surface."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
from fakes import FakeEmbedder
from fastmcp import Client
from starlette.testclient import TestClient

from memory_ultra_rag_mcp import ui
from memory_ultra_rag_mcp.config import ServerConfig, resolve_config
from memory_ultra_rag_mcp.index import MemoryIndex
from memory_ultra_rag_mcp.retrieval import Retrieval, RetrievalSettings
from memory_ultra_rag_mcp.server import create_server
from memory_ultra_rag_mcp.store import read_document
from memory_ultra_rag_mcp.vectors import VectorStore


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
    assert [entry["scope"] for entry in scopes] == ["local", "global"]
    assert scopes[0]["label"] == "This project"
    assert scopes[0]["directory"] == str(config.local_directory)
    assert scopes[0]["standing_present"] is False
    assert scopes[0]["round_count"] == 0
    assert scopes[0]["latest_round_date"] is None
    assert scopes[1]["directory"] == str(config.global_directory)


def test_the_rounds_action_answers_empty_because_there_are_no_rounds(
    tmp_path: Path,
) -> None:
    """The shared page asks for rounds under the memory capability.

    An action that refused would take the whole memory view down with it, so the
    adapter answers the request it is given. An empty list is the true answer for a
    memory of statements, and the page's own wording is what says otherwise.
    """

    config = _config(tmp_path)

    async def scenario() -> dict:
        async with Client(create_server(config)) as client:
            adapter = ui.MemoryUIAdapter(config, client)
            return await adapter.call("memory_rounds", {"scope": "local"})

    answered = asyncio.run(scenario())
    assert answered["rounds"] == []
    assert answered["round_count"] == 0
    assert answered["truncated"] is False
    # Asking still brings the scope into being, exactly as a read does.
    # The page's document is the record rendered, so no file is written for it.
    assert read_document(config.local_directory) is None


def test_an_unknown_scope_is_refused(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def scenario() -> list[int]:
        codes = []
        async with Client(create_server(config)) as client:
            adapter = ui.MemoryUIAdapter(config, client)
            for scope in (
                "",
                "elsewhere",
                "global:default",
                "global:../outside",
                "../elsewhere",
            ):
                with pytest.raises(ui.UIRequestError) as failure:
                    await adapter.call("memory_standing", {"scope": scope})
                codes.append(failure.value.status_code)
        return codes

    assert asyncio.run(scenario()) == [400, 404, 404, 404, 404]


def test_writing_a_round_is_refused_because_there_are_no_rounds(
    tmp_path: Path,
) -> None:
    """A statement is recorded through a tool; the page's one write is the document."""

    config = _config(tmp_path)

    async def scenario() -> int:
        async with Client(create_server(config)) as client:
            adapter = ui.MemoryUIAdapter(config, client)
            with pytest.raises(ui.UIRequestError) as failure:
                await adapter.call(
                    "memory_append",
                    {
                        "scope": "global",
                        "user_message": "Where does the draft live?",
                        "assistant_message": "In the project directory.",
                    },
                )
            return failure.value.status_code

    assert asyncio.run(scenario()) == 409


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
    assert "changed since this page was loaded" in refusal
    # The page's text became a statement, and the file is the export of it: a
    # heading, a blank line, and the memory itself.
    with MemoryIndex(config.local_directory) as index:
        assert [item.text for item in index.statements()] == [
            "Remember the thesis deadline."
        ]
    assert read_document(config.local_directory) is None


def test_saving_the_page_unchanged_keeps_every_statement_as_it_was(
    tmp_path: Path,
) -> None:
    """The editor must not re-file what it was handed.

    The page is given a rendering, and a rendering carries no kinds: the statements
    the tools filed under `NOTE` and `RULE` arrive as plain prose. Saving that back
    without touching a word would file every statement as `ITEM` and reset every
    date and count in the memory, report a successful save, and show an empty diff,
    because the text the person was editing never held a kind in the first place.
    """

    config = _config(tmp_path)

    async def scenario() -> dict:
        async with Client(create_server(config)) as client:
            for content, kind in (
                ("the draft lives in docs/", "NOTE"),
                ("always cite the commit", "RULE"),
            ):
                await client.call_tool(
                    "record_memory", {"content": content, "kind": kind}
                )
            adapter = ui.MemoryUIAdapter(config, client)
            read = await adapter.call("memory_standing", {"scope": "local"})
            saved = await adapter.call(
                "memory_standing_save",
                {
                    "scope": "local",
                    "content": read["content"],
                    "expected_sha256": read["sha256"],
                },
            )
            return dict(saved)

    saved = asyncio.run(scenario())

    with MemoryIndex(config.local_directory) as index:
        statements = index.statements()
    assert saved["kept"] == 2
    assert saved["added"] == 0
    assert saved["removed"] == 0
    assert {item.text: item.kind for item in statements} == {
        "the draft lives in docs/": "NOTE",
        "always cite the commit": "RULE",
    }
    assert all(item.added_at for item in statements)


def test_a_statement_the_page_saves_is_queued_for_a_vector(tmp_path: Path) -> None:
    """The page and the tools share one retrieval, so a page write is not word-only.

    A statement added in the browser is a statement a tool would have recorded, and
    it needs a vector as much: without the queue it stays invisible to the meaning
    side until somebody runs the reindex.
    """

    config = _config(tmp_path)
    embedder = FakeEmbedder()
    engine = Retrieval(
        embedder=embedder,
        policy=RetrievalSettings(embedding_model=embedder.identity.name),
    )

    async def scenario() -> tuple[int, int]:
        async with Client(
            create_server(config, retrieval=engine, warm=False)
        ) as client:
            adapter = ui.MemoryUIAdapter(config, client, engine)
            saved = await adapter.call(
                "memory_standing_save",
                {
                    "scope": "local",
                    "content": "# MEMORY\nRemember the thesis deadline.\n",
                },
            )
            engine.worker_for(config.local_directory).drain(5.0)
            with VectorStore(
                config.local_directory,
                embedder.identity.name,
                embedder.identity.dimension,
            ) as store:
                return int(saved["units_queued"]), store.count()

    queued, held = asyncio.run(scenario())
    engine.close()

    assert queued == 1
    assert held == 1


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
    assert health["global_directory"] == str(config.global_directory)
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
    assert [entry["scope"] for entry in status["scopes"]] == ["local", "global"]
    assert rounds["rounds"] == []
    assert standing["content"].startswith("# MEMORY")
    # The page's round write is refused; the document write is the one it has.
    assert appended.status_code == 409
    assert saved.json()["status"] == "saved"
    with MemoryIndex(config.local_directory) as index:
        assert [item.text for item in index.statements()] == ["Kept for the thesis."]
