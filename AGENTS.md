# AGENTS.md

This is the engineering guide for agents working in this repository. Read it with
`README.md`, which states what the server is and is not.

## What this project is

A faithful stdio MCP face for **UltraRAG's own memory server**. The memory model,
the tools, the parameters, the file formats, and the storage behaviour are
UltraRAG's, served from a pinned checkout. The package adds a validated
configuration, a storage root the child can write to, a workspace for its log,
and a memory-only tool surface.

The value of this project is *fidelity*. A change that makes the memory behave
differently from upstream is therefore a change to the product, not an
improvement to it, and it does not belong here.

## Rules

- **Never patch or vendor upstream.** The checkout is read, never written. No file
  from `OpenBMB/UltraRAG` is copied into this package.
- **Keep the pinned revision pinned.** `src/memory_ultra_rag_mcp/manifest.py`
  holds the commit, the version, and the entrypoint. Moving the pin is a
  deliberate change with a fresh review of the upstream file, not a flag.
- **Validate before serving.** Configuration is resolved and validated before any
  process starts: an unpinned revision, a modified checkout, or a missing
  entrypoint fails loudly, and diagnostics go to stderr so stdout stays a clean
  MCP stream.
- **Keep the surface honest.** Only tools that work are exposed. The upstream
  pipeline `build` tool is filtered out on purpose; do not re-expose it, and do
  not add tools of our own — a tool added here would be a memory feature this
  project does not own.
- **Do not add behaviour.** No deduplication, no correction, no search, no
  ranking, no summarising, no migration, no second storage format. Those are the
  sibling project's subject, not this one's.
- **Never depend on a sibling repository**, and never read another server's
  private state. Cross-server comparison belongs in the collection README, one
  level up.
- **Document every difference.** `README.md` carries the modification table. A
  behaviour difference that is not in that table is a defect.

## Canonical commands

```bash
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest
uv build
```

The integration suite starts the real upstream memory server and is opt-in:

```bash
ULTRARAG_ROOT=/path/to/UltraRAG uv run --frozen pytest -q -m integration
```

Run it against the pinned revision before claiming fidelity for a change.

## Attribution

Credit UltraRAG, its contributors, and the organizations the project names:
THUNLP, NEUIR, OpenBMB, and AI9stars. Preserve the independent-project and
no-endorsement statements, the Apache-2.0 `LICENSE`, and `NOTICE`. Any dependency,
snapshot, or pin change requires a fresh license and attribution review.
