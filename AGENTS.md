# AGENTS.md

This is the engineering guide for agents working in this repository. Read it with
`README.md`, which states what the server is and is not.

## What this project is

A faithful stdio MCP face for **UltraRAG's own memory**, in two kinds: global
memory, one per user, and local memory, one per project. Both kinds are written
by UltraRAG's own server — running as a child process from a pinned checkout —
in UltraRAG's own layout, file names, and format, so anything pointed at the
storage tree reads them.

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
- **Local memory goes through UltraRAG's own tools.** `get_local_memory` and
  `save_local_memory` call the upstream memory tools with a project's scope, so
  there is one writer, one layout, and one format in this project. Keep it that
  way: a second writer would need its own format and would drift.
- **Keep the `local-` prefix reserved.** It is what separates a project's memory
  from a user's inside one directory tree.
- **The surface is the four memory tools.** UltraRAG's server base class also
  registers a pipeline `build` tool; it is kept off the surface. Add no other
  tool: a tool here that is not memory would be a feature this project does not
  own.
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
