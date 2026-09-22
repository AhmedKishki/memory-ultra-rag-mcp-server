# Memory UltraRAG MCP

**Purpose:** serve UltraRAG's memory to an agent over stdio MCP, in two kinds:

- **local memory** belongs to the project this server is bound to and lives inside
  that repository, under `.memory-rag`, so a project carries its memory with it
  and no other project reads it;
- **global memory** belongs to a user and lives in UltraRAG's shared storage
  tree, so every instance serving that tree reads the same memory.

Both kinds work the same way: a standing document plus one dated dialogue file
per day, written in UltraRAG's format. The agent chooses which kind a write goes
to, and the two tools of each kind take the same arguments.

The behaviour, the tool names, the file names, and the byte formats are
UltraRAG's, taken from `servers/memory/src/memory.py` at the pinned revision below
and checked against files that revision produced. The difference this package
makes is where a project's memory lives: UltraRAG keeps everything under one
storage tree, and this server keeps each project's memory inside the project.

| Reference revision | Declared version |
| --- | --- |
| `3a709a2aea3fbe46acca59c422621c94b6e86857` | `0.3.0.2` |

## Requirements

CPython 3.11 or 3.12, Linux, and `uv`. No UltraRAG checkout is needed: the
formats are verified against captured fixtures, and both roots are ordinary
directories this server creates as it needs them.

## Install

```bash
git clone https://github.com/AhmedKishki/memory-ultra-rag-mcp-server.git
cd memory-ultra-rag-mcp-server
uv sync --frozen
```

## Configure an MCP client

```json
{
  "mcpServers": {
    "memory-ultra-rag-mcp": {
      "command": "/ABSOLUTE/PATH/TO/memory-ultra-rag-mcp-server/.venv/bin/memory-ultra-rag-mcp",
      "args": [
        "--project-root", "/ABSOLUTE/PATH/TO/my-project",
        "--storage-root", "/ABSOLUTE/PATH/TO/UltraRAG/ui/storage"
      ]
    }
  }
}
```

| Flag | Meaning | Default |
| --- | --- | --- |
| `--project-root` | The repository whose local memory is served | required |
| `--storage-root` | UltraRAG's UI storage tree, holding every user's global memory | `$ULTRARAG_UI_STORAGE_ROOT`, then `<workspace-root>/ui-storage` |
| `--workspace-root` | Directory for this server's own files | the per-user data directory |

One server instance serves one project, so `--project-root` binds the session:
local memory always means that project's memory.

## The four tools

| Tool | Parameters | What it does |
| --- | --- | --- |
| `get_local_memory` | — | Returns the project's standing memory, the whole `MEMORY.md`, and creates it from UltraRAG's template the first time it is read. |
| `save_local_memory` | `q_ls`, `ans_ls` | Appends one round to the project's daily file. |
| `get_global_memory` | `user_id="default"` | Returns that user's standing memory, and creates it from UltraRAG's template the first time it is read. |
| `save_memory` | `user_id`, `q_ls`, `ans_ls` | Appends one round to that user's daily file. |

`q_ls` and `ans_ls` are single-element lists — the user's message and the reply —
in the shape UltraRAG's own tool takes. A `user_id` holds letters, digits, `_` and
`-`, which is both upstream's rule and what keeps a user's memory to one directory.

## Storage

```
<project-root>/.memory-rag/MEMORY.md                  the project's standing memory
<project-root>/.memory-rag/project/<date>.md          the project's rounds, by day
<storage-root>/memory/<user_id>/MEMORY.md             a user's standing memory
<storage-root>/memory/<user_id>/project/<date>.md     that user's rounds, by day
```

A round is written in UltraRAG's format:

```markdown
# Project Memory 2026-09-22

## 2026-09-22 14:48:30
- user: Where does the draft live?
- assistant: In the project directory.
```

Two consequences worth knowing:

- **A project's memory is ordinary Markdown in that project.** It is visible,
  diffable, and optionally committed, by the project's own choice.
- **Global memory sits in UltraRAG's UI storage tree.** Point a UltraRAG UI at the
  same storage root and it shows the global memory an agent writes, with no
  exchange format and no import step.

## The choices this package makes

| # | Choice | Reason |
| --- | --- | --- |
| 1 | Local memory is kept inside the project, under `.memory-rag` | isolation is the point: a project's memory is private to that project and travels with it |
| 2 | The project is bound at startup, and the project tools take no project argument | one session means one project, so local memory cannot be written into the wrong one |
| 3 | Global memory keeps UltraRAG's layout in the storage tree | the shared memory stays where a UltraRAG UI already reads it |
| 4 | The behaviour, file names, and formats are UltraRAG's, and the two kinds share one implementation | a project's memory and a user's memory have one shape, so either can be read the same way |
| 5 | The surface is the four memory tools | UltraRAG's server base class also registers a pipeline `build` tool, which belongs to a pipeline deployment; this surface is memory |
| 6 | The reference revision and its captured output are committed and tested | the format cannot drift quietly: a change fails `tests/test_fidelity.py` |

## Develop and test

```bash
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest
uv build
```

The suite runs without any UltraRAG checkout. It covers one scope's behaviour
(template, create-on-read, round format, append, day rollover, refusals), the
four tools over the real stdio server, project isolation between two projects, the
shared global tree, and a byte-for-byte comparison against the files UltraRAG's
own server produced (`tests/fixtures/upstream/README.md` records how they were
captured).

## Credit and licensing

The memory model, the tools `get_global_memory` and `save_memory`, the file names,
and the file formats are UltraRAG's, from
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
