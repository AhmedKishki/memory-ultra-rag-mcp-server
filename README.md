# Memory UltraRAG MCP

**Purpose:** serve UltraRAG's memory to an agent over stdio MCP, in two kinds —
**global memory**, one per user, and **local memory**, one per project. Each kind
is a standing document plus one dated dialogue file per day, written in
UltraRAG's own format, in UltraRAG's own storage tree, so every client pointed at
that tree reads and shows the same memory.

The memory itself is UltraRAG's. Its own server runs as a child process from a
pinned checkout and is served through: the two upstream tools are proxied
unchanged, and the two local-memory tools call those same upstream tools with a
project's scope, so both kinds have the same shape, the same file names, and the
same format.

## Requirements

- CPython 3.11 or 3.12, Linux.
- `uv`.
- A **UltraRAG Git checkout at the pinned revision**. The server validates the
  revision and the declared version, and serves an unmodified tree.

| Pinned revision | Declared version | Memory server |
| --- | --- | --- |
| `3a709a2aea3fbe46acca59c422621c94b6e86857` | `0.3.0.2` | `servers/memory/src/memory.py` |

## Install

```bash
git clone https://github.com/AhmedKishki/memory-ultra-rag-mcp-server.git
cd memory-ultra-rag-mcp-server
uv sync --frozen
```

UltraRAG is installed from that revision's source archive, the way the other
UltraRAG-based servers in this collection install it.

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
| `--workspace-root` | Directory holding the child's log | the per-user data directory |
| `--storage-root` | UltraRAG's **UI storage root**, where memory is read and written | `<workspace-root>/ui-storage` |
| `--python-executable` | Interpreter for the upstream child server | this interpreter |
| `--log-level` | Log level handed to the child server | `warn` |

## The four tools

| Tool | Parameters | What it does |
| --- | --- | --- |
| `get_global_memory` | `user_id="default"` | Returns that user's standing memory, the whole `MEMORY.md`. UltraRAG creates it from its template the first time it is read. |
| `save_memory` | `user_id`, `q_ls`, `ans_ls` | Appends one round — your message and the reply — with a timestamp, to that user's daily file. |
| `get_local_memory` | `project_id` | Returns that project's standing memory, in the project's own scope. |
| `save_local_memory` | `project_id`, `q_ls`, `ans_ls` | Appends one round to that project's daily file, in the project's own scope. |

`project_id` is a name, not a path: letters, digits, `_` and `-`, at most 64
characters. The prefix `local-` is reserved for local memory, so a project's
memory and a user's memory never share a directory by accident.

The first two tools are UltraRAG's, with its names, parameters, defaults, and
return shapes; the two local tools call them with a project's scope.

## Storage

Everything lives under the storage root, in UltraRAG's layout:

```
<storage-root>/memory/<user_id>/MEMORY.md                   a user's standing memory
<storage-root>/memory/<user_id>/project/<date>.md           that user's rounds, by day
<storage-root>/memory/local-<project_id>/MEMORY.md          a project's standing memory
<storage-root>/memory/local-<project_id>/project/<date>.md  that project's rounds, by day
```

`--storage-root` is UltraRAG's UI storage root (`ULTRARAG_UI_STORAGE_ROOT`).
Pointing it at an existing `UltraRAG/ui/storage` adopts the memory already there,
and pointing a UltraRAG UI at the same directory shows the memory this server
writes: the two read one tree, with no exchange format and no import step.

## The choices this package makes

The memory behaviour is UltraRAG's, running unmodified from the checkout. These
are the entry-point choices, each visible in the surface or in the storage above:

| # | Choice | Reason |
| --- | --- | --- |
| M1 | It installs and runs as its own console command over stdio | so it can be configured as a standalone MCP server |
| M2 | The storage root is passed in | UltraRAG derives a relative root from where its own checkout lives, which suits a pipeline and not an installed server |
| M3 | The child runs in, and logs to, `<workspace-root>` | UltraRAG's server base class creates a relative `logs/` directory, and this keeps it inside the server's own workspace |
| M4 | The surface carries the four memory tools | UltraRAG's server base class also registers a pipeline `build` tool, which belongs to a pipeline deployment; this surface is the memory tools |
| M5 | The pinned revision and version are validated at startup | so the served memory is a known revision |
| M6 | Local memory is a project's scope under the reserved `local-` prefix | two kinds of memory in one tree, with UltraRAG's own tools and format for both |

No UltraRAG source file is copied, patched, or vendored: the checkout is read,
and the child process runs from it in place.

## Develop and test

```bash
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest          # unit suite; the integration suite skips itself
uv build
```

The integration suite starts the real upstream memory server, so it takes a
checkout at the pinned revision:

```bash
ULTRARAG_ROOT=/path/to/UltraRAG uv run --frozen pytest -q -m integration
```

It calls both kinds of memory through the real server and checks the surface, the
storage layout, the file format of a round, the separation of two projects and of
a project from a user, UltraRAG's own validation of identifiers, and that serving
memory leaves the checkout untouched.

## Credit and licensing

The memory model, the tools `get_global_memory` and `save_memory`, the file
names, and the file formats are UltraRAG's, from
[`OpenBMB/UltraRAG`](https://github.com/OpenBMB/UltraRAG) at commit `3a709a2`
(Apache-2.0). Credit belongs to the UltraRAG team and contributors, including
participants from THUNLP, NEUIR, OpenBMB, and AI9stars.

This package is licensed under the [Apache License 2.0](LICENSE). It is an
independent project: not an official release of UltraRAG, not affiliated with or
endorsed by its maintainers, and the UltraRAG name describes what it serves.

The graph memory server
([`graph-memory-ultra-rag-mcp-server`](https://github.com/AhmedKishki/graph-memory-ultra-rag-mcp-server))
is the collection's other memory project, which extends memory into a typed,
relational store.
