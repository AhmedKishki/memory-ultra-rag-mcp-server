# AGENTS.md

This is the engineering guide for agents working in this repository. Read it with
`README.md`, which states what the server does.

## What this project is

A stdio MCP face for **UltraRAG's memory**, in two kinds: local memory, kept
inside the project the server is bound to, under `.memory-rag`, and global memory,
kept in UltraRAG's shared storage tree. Both kinds are one standing document plus
dated rounds, written in UltraRAG's format by one implementation
(`src/memory_ultra_rag_mcp/store.py`).

The value of this project is *fidelity to UltraRAG's memory behaviour, with
project isolation*. A change that makes the memory behave differently from
upstream is a change to the product and belongs in the choice table in `README.md`
before it belongs in the code.

## Rules

- **One implementation, two roots.** `store.py` holds the behaviour; the roots
  come from `config.py`. Add no second writer and no second format: a project's
  memory and a user's memory are the same shape on purpose.
- **Keep the formats byte-identical to upstream.** `tests/fixtures/upstream/`
  holds files that UltraRAG's own server at the pinned revision produced, and
  `tests/test_fidelity.py` compares against them. Changing a fixture means
  changing `reference.py` and recording why.
- **Never copy or vendor UltraRAG source.** The behaviour and the formats were
  taken from the pinned revision and reimplemented in original Python; the
  reference revision, its files, and its captured output are recorded in
  `reference.py` and the fixtures.
- **Local memory stays inside the project.** It lives in
  `<project-root>/.memory-rag`, it is created there when it is first needed, and
  nothing writes a project's memory into the shared tree.
- **Global memory stays in the storage tree.** `<storage-root>/memory/<user_id>/`
  is UltraRAG's own layout, which is what lets a UltraRAG UI show the same memory.
- **Validate before serving.** Configuration is resolved before any memory is
  touched: a project root that is not a directory fails loudly, and diagnostics go
  to stderr so stdout stays a clean MCP stream.
- **The surface is the four memory tools.** UltraRAG's server base class registers
  a pipeline `build` tool as well; it belongs to a pipeline deployment and is not
  part of this surface. Add no other tool: a tool here that is not memory would be
  a feature this project does not own. The browser view adds no tool.
- **The view reaches memory only through this server.** `ui.py` is a thin adapter:
  it authorizes the scope a request names, then reads or writes through `store.py`
  and the memory tools. The shared UI package is given results, never a path, and
  it must stay unable to read `.memory-rag` or the storage tree.
- **The UI package is pinned by commit** in `pyproject.toml`, and it is interface
  infrastructure with its own release history. Change the pin deliberately, run
  both suites, and record why in the commit message.
- **Rounds are written by the tools' own code path.** The view's round form calls
  `save_local_memory` or `save_memory`, so the browser and an agent produce the
  same bytes. The one write the view performs itself is replacing the standing
  document, which has no tool, is written atomically, and is refused when the
  document changed since it was read.
- **Never depend on a sibling repository**, and never read another server's
  private state. Cross-server comparison belongs in the collection README, one
  level up.
- **Document every choice.** `README.md` carries the choice table. A behaviour
  difference that is not in that table is a defect.

## Canonical commands

```bash
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest
uv build
```

The suite needs no UltraRAG checkout, and the integration tests start the real
stdio server twice: once per project, over one shared storage tree. The browser
view's own suite drives the adapter in memory and the routes over a test client.

## Attribution

Credit UltraRAG, its contributors, and the organizations the project names:
THUNLP, NEUIR, OpenBMB, and AI9stars. Preserve the independent-project and
no-endorsement statements, the Apache-2.0 `LICENSE`, and `NOTICE`. Any reference
revision change requires recapturing the fixtures and a fresh attribution review.
