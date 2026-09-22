# Memory UltraRAG MCP

**Purpose:** serve UltraRAG's own memory server over stdio MCP, unmodified — one
global `MEMORY.md` per user and one dated dialogue file per day, exactly as
UltraRAG ships it, so an agent can read and append to the same memory a UltraRAG
UI shows.

This is not a second memory design. The memory model, the tools, the file
formats, and the storage behaviour all belong to UltraRAG; this package starts
that server from a pinned checkout, gives it a storage root it can actually
write to, and proxies it. Its sibling
[`graph-memory-ultra-rag-mcp-server`](../graph-memory-ultra-rag-mcp-server) is
the project that extends memory into a typed, relational store; this one is the
vanilla memory, kept deliberately small.

## What it is not

- Not a knowledge graph, and not a typed store: there is no schema, no types,
  no relations, and no identifiers.
- Not a RAG: there is no retriever, no index, no ranking, and no model.
- Not an admission layer: it never refuses a duplicate, never reconciles a
  contradiction, and never corrects or retracts anything.
- Not isolated per project: memory is scoped by `user_id` only.
- Not a source of evidence: stored text is a record of what was said.

## Requirements

- CPython 3.11 or 3.12, Linux.
- `uv`.
- A **UltraRAG Git checkout at the pinned revision** (see below). The server
  refuses any other revision, and refuses a checkout with tracked modifications,
  because "the pinned revision" must be a fact rather than a claim.

| Pinned revision | Declared version | Memory server |
| --- | --- | --- |
| `3a709a2aea3fbe46acca59c422621c94b6e86857` | `0.3.0.2` | `servers/memory/src/memory.py` |

## Install

```bash
git clone https://github.com/AhmedKishki/memory-ultra-rag-mcp-server.git
cd memory-ultra-rag-mcp-server
uv sync --frozen
```

UltraRAG is installed from that revision's source archive, the same way the
other UltraRAG-based servers in this collection install it. It is a large
dependency set (about 117 distributions); the upstream memory server imports
`ultrarag.server`, so its dependencies are the child process's dependencies.

## Configure an MCP client

```json
{
  "mcpServers": {
    "memory-ultra-rag-mcp": {
      "command": "/ABSOLUTE/PATH/TO/memory-ultra-rag-mcp-server/.venv/bin/memory-ultra-rag-mcp",
      "args": [
        "--ultrarag-root", "/ABSOLUTE/PATH/TO/UltraRAG",
        "--workspace-root", "/ABSOLUTE/PATH/TO/memory-workspace",
        "--storage-root", "/ABSOLUTE/PATH/TO/UltraRAG/ui/storage"
      ]
    }
  }
}
```

| Flag | Meaning | Default |
| --- | --- | --- |
| `--ultrarag-root` | The pinned checkout to serve | `$ULTRARAG_ROOT`, else required |
| `--workspace-root` | Where the child's stderr log lives | the per-user data directory |
| `--storage-root` | The UltraRAG **UI storage root** the memory is written into | `<workspace-root>/ui-storage` |
| `--python-executable` | Interpreter for the child server | this interpreter |
| `--log-level` | Log level handed to the child | `warn` |

## What the tools do

Both tools are UltraRAG's, with upstream names, parameters, defaults, and
return shapes:

| Tool | Parameters | Behaviour |
| --- | --- | --- |
| `get_global_memory` | `user_id="default"` | Returns the whole `MEMORY.md` for that user. Upstream creates the file from a template on first read, so this call can create it. |
| `save_memory` | `user_id`, `q_ls`, `ans_ls` | Appends one timestamped user/assistant round to that day's project file. |

## Storage

```
<storage-root>/memory/<user_id>/MEMORY.md              the global profile
<storage-root>/memory/<user_id>/project/<date>.md      one file per day
```

`--storage-root` is UltraRAG's UI storage root (`ULTRARAG_UI_STORAGE_ROOT`), which
is what makes the integration work: point a UltraRAG UI at the same directory and
it reads the same memory this server writes, with no exchange format and no
import step. Pointing it at an existing `UltraRAG/ui/storage` adopts memory that
already exists.

## Fidelity and modifications

The memory behaviour is upstream's, running unmodified from the checkout. The
differences are entry-point differences, not behaviour differences:

| # | Difference from running the file inside a UltraRAG pipeline | Why |
| --- | --- | --- |
| M1 | It is installable and runs as its own console command over stdio | so it can be configured as a standalone MCP server |
| M2 | The storage root is passed in (`--storage-root`) instead of being derived from the checkout's location | upstream resolves a relative root against its own checkout, which is right inside a pipeline and wrong for a standalone server |
| M3 | The child runs with `<workspace-root>` as its working directory, and its stderr is written to `<workspace-root>/logs/` | upstream's base class creates a relative `logs/` directory; this keeps that directory inside the server's own workspace instead of the client's working directory |
| M4 | The surface is narrowed to `get_global_memory` and `save_memory` | the base class also registers a pipeline `build` tool, which has no pipeline to build here; offering it would offer a tool that cannot work |
| M5 | The pinned revision and version are validated before anything starts | serving "UltraRAG memory" only means something if the revision is known |

Nothing else is changed: no source file of UltraRAG is copied, patched, or
vendored, and the checkout is read, not written.

## Known limits, inherited on purpose

- The global memory is one mutable document, read whole.
- Daily files are append-only: there is no correction, retraction, or
  deduplication, and no way to remove a round.
- A read creates the user's `MEMORY.md` if it does not exist.
- Two sessions appending to the same day's file at the same time may interleave;
  upstream does not lock.
- Scope is `user_id`; there is no project boundary and no cross-project split.

These are UltraRAG's behaviour. Fixing them would be a different project, which
is what the sibling repository is for.

## Develop and test

```bash
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest
uv build
```

The integration suite starts the real upstream memory server, so it needs a
checkout: it runs when `ULTRARAG_ROOT` names one at the pinned revision and skips
itself otherwise.

```bash
ULTRARAG_ROOT=/path/to/UltraRAG uv run --frozen pytest -q -m integration
```

## Credit and licensing

The memory server, its tools, its file formats, and its storage behaviour are
UltraRAG's, from [`OpenBMB/UltraRAG`](https://github.com/OpenBMB/UltraRAG) at
commit `3a709a2` (Apache-2.0). Credit belongs to the UltraRAG team and
contributors, including participants from THUNLP, NEUIR, OpenBMB, and AI9stars.
No UltraRAG source is copied into this package; the checkout is served in place.

This package is licensed under the [Apache License 2.0](LICENSE). It is an
independent project: not an official release of UltraRAG, not affiliated with or
endorsed by its maintainers, and the UltraRAG name is used only to describe what
it serves.
