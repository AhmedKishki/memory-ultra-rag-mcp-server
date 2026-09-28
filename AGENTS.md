# AGENTS.md

This is the engineering guide for agents working in this repository. Read it with `README.md`, which states what the server does.

## What this project is

A stdio MCP face for **UltraRAG's memory**, in two kinds: local memory, kept inside the project the server is bound to, under `.memory-rag`, and global memory, kept in UltraRAG's shared storage tree. Both kinds are one standing document, `MEMORY.md`, written in UltraRAG's format by one implementation (`src/memory_ultra_rag_mcp/store.py`).

The value of this project is *fidelity to UltraRAG's memory behaviour, with project isolation*. A change that makes the memory behave differently from upstream is a change to the product and belongs in the choice table in `README.md` before it belongs in the code.

## Rules

- **One implementation, two roots.** `store.py` holds the behaviour; the roots come from `config.py`. Add no second writer and no second format: a project's memory and a user's memory are the same shape on purpose.
- **Keep the formats byte-identical to upstream.** `tests/fixtures/upstream/` holds files that UltraRAG's own server at the pinned revision produced, and `tests/test_fidelity.py` compares against them. Changing a fixture means changing `reference.py` and recording why.
- **Never copy or vendor UltraRAG source.** The behaviour and the formats were taken from the pinned revision and reimplemented in original Python; the reference revision, its files, and its captured output are recorded in `reference.py` and the fixtures.
- **Local memory stays inside the project.** It lives in `<project-root>/.memory-rag`, it is created there when it is first needed, and nothing writes a project's memory into the shared tree.
- **Global memory stays in the storage tree, and has no user dimension.** `<storage-root>/memory/default/` is UltraRAG's own layout for the user it is given when none is named, which is what lets a UltraRAG UI show the same memory, and there is one such memory per account. No tool and no page request takes a user identifier; the directory name is a constant in `config.py`, not a parameter. The storage root is the account's home data directory unless `--storage-root`, `MEMORY_ULTRARAG_STORAGE_ROOT`, or `ULTRARAG_UI_STORAGE_ROOT` moves it; that order is the precedence, and it is implemented once, in `_selected_storage_root`.
- **Validate before serving.** Configuration is resolved before any memory is touched: a project root that is not a directory fails loudly, and diagnostics go to stderr so stdout stays a clean MCP stream.
- **A project is identified by the repository the server is bound to.** `--project-root` decides which project's memory is served, it is resolved before anything is touched, and the tools take no project argument. Do not add a project or user parameter to reach another scope: the isolation is the directory, and a parameter would turn it into something the code has to enforce.
- **The memory is the record; the indexes are derived, and a read keeps them in step.** `store.py` holds the behaviour and the files; `index.py` and `vectors.py` hold what `read.py` searches. Before it searches, a read calls `index.catch_up()`: if the standing document's fingerprint changed it is re-read, so a statement someone typed, reworded, or deleted by hand is reflected in the very next answer, and a deleted statement stops answering. A read still does not walk the scope or embed a unit — it embeds its query once, scans, fuses, and returns — and the catch-up costs one fingerprint (measured ~17 µs) when nothing changed, and one whole-file re-read (~2 ms) when it did. This reversed an earlier decision to keep the index current only on the write side, which was defended by a measurement in the collection's other server of ~0.18 ms per source across a few thousand *dated files*; this server keeps one document per scope, so that measurement no longer describes it. A read reports `index_synced` when it had to catch up and `units_pending` for vectors a hand edit still owes. Never make an index the thing that is true, and never let a read return a file.
- **No store or index may import a model library.** Retrieval is written against the `Embedder` and `Reranker` seams in `models.py`; `store.py`, `index.py` and `vectors.py` must not import `fastembed`, `onnxruntime`, or `numpy`. That is what makes a model change a line of configuration and keeps the whole test suite runnable with no model at all.
- **A write is durable before it is derived, and never fails because of the lookup layer.** Append the statement, then hand the unit to the worker. A model that cannot be fetched leaves the unit pending, and the read says so.
- **The semantic side is unmeasured.** No document in this package may claim a quality gain from it. Reranking is on by default on a measured cost, not a measured gain: its accuracy here is unknown and the parameters in `retrieval.py` are the collection's measured ones from another server, adopted as a starting point. Changing the model needs a measurement first.
- **A statement carries a type the caller names.** It is written as the statement's own first words and a colon, one run of block letters, indexed with the statement, and stripped from every answer. The server defines no categories and interprets none. The dated exchange log upstream keeps is not written or read here, and the differential test asserts it stays that way.
- **The surface is the four memory tools.** UltraRAG's server base class registers a pipeline `build` tool as well; it belongs to a pipeline deployment and is not part of this surface. Add no other tool: a tool here that is not memory would be a feature this project does not own. The browser view adds no tool.
- **This is not a UltraRAG pipeline node.** Ship no `build` tool, no `server.yaml` or `parameter.yaml` for UltraRAG's runner, no `output=` wiring annotations, and no dependency on UltraRAG's package. Memory here is the whole solution for a project and an account, served to agents over MCP and to people in the browser; a pipeline stage someone else wires is a different product.
- **The view reaches memory only through this server.** `ui.py` is a thin adapter: it authorizes the scope a request names, then reads or writes through `store.py` and the memory tools. The shared UI package is given results, never a path, and it must stay unable to read `.memory-rag` or the storage tree.
- **The UI package is pinned by commit** in `pyproject.toml`, and it is interface infrastructure with its own release history. Change the pin deliberately, run both suites, and record why in the commit message.
- **The view's one write is the standing document.** A statement is recorded through a tool, not the page. Replacing the whole document is written atomically and is refused when the document changed since it was read. The page's round write is refused with a 409, because this memory keeps no dated exchanges.
- **FTS5 is a requirement, and it is probed.** `main()` refuses to start without it, because a read would otherwise pass over every file a memory holds. `scripts/check_sqlite_fts5.py` is the check a user runs.
- **Never depend on a sibling repository**, and never read another server's private state. Cross-server comparison belongs in the collection README, one level up.
- **Document every choice.** `README.md` carries the choice table. A behaviour difference that is not in that table is a defect.

## Canonical commands

```bash
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest
uv build
# optional, and the strongest fidelity check: compare against a real checkout
ULTRARAG_CHECKOUT=/ABSOLUTE/PATH/TO/UltraRAG uv run --frozen pytest -m upstream
```

The suite needs no UltraRAG checkout, and the integration tests start the real stdio server twice: once per project, over one shared storage tree. The browser view's own suite drives the adapter in memory and the routes over a test client. The comparison test runs upstream's own memory server from a checkout when `ULTRARAG_CHECKOUT` names one, and skips otherwise.

## Attribution

Credit UltraRAG, its contributors, and the organizations the project names: THUNLP, NEUIR, OpenBMB, and AI9stars. Preserve the independent-project and no-endorsement statements, the Apache-2.0 `LICENSE`, and `NOTICE`. Any reference revision change requires recapturing the fixtures and a fresh attribution review.
