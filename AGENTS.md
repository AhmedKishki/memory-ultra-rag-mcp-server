---
name: AGENTS.md
description: Stdio memory server fidelity, storage boundaries, and validation rules.
---

# Stdio memory server rules

## What this project is

- UltraRAG-compatible memory over stdio MCP, bound to one project.
- Local records live under `.memory-rag`; global records live under `<storage-root>/memory/default/`.
  - `memory.sqlite3` is authoritative; `MEMORY.md` renders its statements in UltraRAG's format.
- Document behavior differences from upstream in the `README.md` choice table before implementing them.

## Rules

### Isolation and fidelity

- `store.py` owns one behavior and format for both roots; `config.py` owns locations.
- Preserve byte-identical upstream rendering fixtures in `tests/fixtures/upstream/` and `tests/test_fidelity.py`.
  - Fixture changes require updating `reference.py`, the reason, and attribution.
  - Reimplement behavior in original Python; never vendor UltraRAG source.
- Create local memory only when needed, inside `<project-root>/.memory-rag`; never copy it into global storage.
- Keep global memory at `memory/default`, with no user identifier in tools or pages.
- Storage-root priority: `--storage-root`, `MEMORY_ULTRARAG_STORAGE_ROOT`, `ULTRARAG_UI_STORAGE_ROOT`, then the account data directory.
  - `_selected_storage_root` owns resolution.
- Resolve configuration and validate the project directory before touching memory.
  - Diagnostics go to stderr; stdout is MCP only.
- `--project-root` binds the server; tools take no project or user parameter.
- Never depend on a sibling repository or read its private state; comparisons belong to the collection README.

### Records and derived state

- Keep `store.py`, `index.py`, and `vectors.py` free of `fastembed`, `onnxruntime`, and `numpy`.
  - Retrieval uses `models.py`'s `Embedder` and `Reranker` seams; tests need no models.
- `unit` holds statement, identity, and `PRODUCTIVE_COLUMNS`.
  - New columns, normalized copies, or timestamps need a named consumer here and in `PRODUCTIVE_COLUMNS`; `tests/test_metadata.py` enforces it.
- Commit records before worker submission; lookup failure must never fail a durable write.
  - Disclose pending units; retry missing models after `RETRY_SECONDS`, with `submit` clearing the wait.
  - Serialize scope writes; keep any lock outside the memory directory.
- `memory.sqlite3` holds statements, history, FTS5, and vectors; it is not a disposable cache.
  - Schema migration preserves original rows, vectors, and history rather than restoring from a rendering.
- Render `MEMORY.md` only when requested by `--export` or the page, never on an ordinary write.
  - Do not reimport a stale rendering into an authoritative record.
  - Preserve genuine legacy recovery and report retired files through `superseded_removed`.
  - Keep unreadable legacy files; distinguish unreadable from valid empty input.
  - Do not import duplicate statements merely because legacy digests differ.
  - Keep a requested rendering until `--retire-document` removes it.
- Refuse startup without FTS5; `scripts/check_sqlite_fts5.py` probes support.

### Retrieval and tools

- Expose only `record_memory`, `recall_memory`, `forget_memory`, and `record_handoff`.
  - Writable scopes are `local` and `global`, defaulting to `local`.
  - Recall ranks both scopes together; forgetting requires exact, unambiguous text.
  - No browser-only tools, pipeline `build`, `server.yaml`, `parameter.yaml`, `output=` wiring, or UltraRAG package dependency.
- Store uppercase kinds as columns, not prefixes in returned text or rendered prose.
  - Kinds are caller-defined, defaulting to `ITEM`, including kindless legacy exports.
  - Narrow reads through `kind`; category words alone need not match statement text.
  - Reserve `HANDOFF` and replace the previous handoff by kind.
- Recall before recording; forget contradicted statements by the exact recalled text.
  - Choose local/global scope from substance and intended reach, never marker-phrase rules.
  - Do not add fuzzy forgetting or a write workflow that skips recall.
- Collapse repetition once across the merged answer, never refuse a write for similarity.
  - Test exact words first, then vector cosine at `retrieval.duplicate_cosine`.
  - Keep the best-ranked match and disclose the reason in `collapsed_repetitions`.
  - Compare bounded answer candidates; do not add another similarity rule or make collapse advisory.
  - The threshold affects only cosine; exact-word collapse remains mandatory.
- Rerank every read using a pinned `models.py` model; reject empty or unnamed settings.
  - `retrieval.rerank_depth` bounds scored candidates, not a separate ranking rule.
- Apply recency after reranking using recorded dates and `RetrievalSettings.recency_bonus`, never document position.
  - Missing dates get no bonus; newest statements render first.
  - The default is ten percent, not a quality guarantee or another recency rule.
- Semantic quality is unmeasured; claim no gain and measure before changing models.
  - Borrowed retrieval parameters are starting values, not memory-quality evidence.
- Tool answers include statements and only useful conditions or disclosures.
  - `units_pending`, `semantic_available`, `truncated`, `hint`, `superseded_removed`, and `collapsed_repetitions` appear only when informative.
  - Do not publish ranking scores, match branches, positions, timings, digests, inferable counts, write paths, or queue internals.
  - Internal ranking arrays are `unit_keys`, `unit_scores`, and `unit_stamps`; diagnostics do not justify new tool fields.

### Settings and browser view

- Declare every tunable in `settings.py` and `default.toml`; reject undeclared keys in every layer.
- Merge per key, low to high: packaged defaults, account `config.toml`, project config, `--config`, `MEMORY_ULTRARAG_*`, `--set`.
  - Report provenance through `--print-config`; never duplicate default values elsewhere.
- The current layer engine imports the commit-pinned `config-ultra-rag-mcp` library.
  - This repository owns `SETTINGS`, its derived maps, `EffectiveSettings`, defaults, prefixes, and directory names.
  - `tests/test_architecture.py` rejects copied layer machinery; a new tunable changes the local registry and default, not that library.
  - Independent consumers may pin different commits; never float dependencies.
- Settings declare change cost: `identity` requires re-embedding, `runtime` sets locations, `retrieval` changes admission/order.
  - Schema versions and scope names/paths stay in code, not settings.
- `ui.py` authorizes scope and calls server/store operations; the shared UI receives results, never memory paths.
  - Keep the UI package commit-pinned; deliberate pin changes run both suites and state why.
- The page replaces a whole standing document atomically with stale-read protection, not individual tool statements.
  - Refuse dated round writes with HTTP 409.

## Working rule

- Run validation, commit, and push each change to `origin main` before starting the next.
  - State what changed and why; update the collection pointer only after the child commit is remote.

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

- Standard tests need no UltraRAG checkout.
  - Integration starts two real stdio servers over separate projects and shared global storage.
  - Browser tests exercise the adapter and HTTP routes.
  - `ULTRARAG_CHECKOUT` enables upstream comparison; otherwise that test skips.

## Attribution

- Credit UltraRAG, its contributors, THUNLP, NEUIR, OpenBMB, and AI9stars.
- Preserve the independent-project and no-endorsement statements, Apache-2.0 `LICENSE`, and `NOTICE`.
- Reference revision changes require recapturing fixtures and reviewing attribution.
