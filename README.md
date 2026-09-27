# Memory UltraRAG MCP

**Purpose:** serve UltraRAG's memory to an agent over stdio MCP, in two kinds:

- **local memory** belongs to the project this server is bound to and lives inside that repository, under `.memory-rag`, so a project carries its memory with it and no other project reads it;
- **global memory** belongs to a user and lives in UltraRAG's shared storage tree, so every instance serving that tree reads the same memory. It is the user's, not the project's: who they are, what they want remembered everywhere, and the **durable rules that apply wherever they work**.

Both kinds work the same way: a standing document plus one dated dialogue file per day, written in UltraRAG's format. The agent chooses which kind a write goes to, and the two tools of each kind take the same arguments. A standing instruction the user gave once belongs in global memory's standing document, where it is read at the start of every conversation, rather than in a dated round.

The behaviour, the tool names, the file names, and the byte formats are UltraRAG's, taken from `servers/memory/src/memory.py` at the pinned revision below and checked against files that revision produced. The difference this package makes is where a project's memory lives: UltraRAG keeps everything under one storage tree, and this server keeps each project's memory inside the project. What the extension is, and what it deliberately leaves alone, is decided in [ADR 0001](docs/decisions/0001-extend-ultrarag-memory.md).

| Reference revision | Declared version |
| --- | --- |
| `3a709a2aea3fbe46acca59c422621c94b6e86857` | `0.3.0.2` |

## Requirements

CPython 3.11 or 3.12, Linux, and `uv`. No UltraRAG checkout is needed: the formats are verified against captured fixtures, and both roots are ordinary directories this server creates as it needs them. The browser view is the shared [`ui-ultra-rag-mcp`](https://github.com/AhmedKishki/ui-ultra-rag-mcp) package, pinned by commit and installed with this one.

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
| `--project-root` or `MEMORY_ULTRARAG_PROJECT_ROOT` | The repository whose local memory is served | required |
| `--storage-root` or `MEMORY_ULTRARAG_STORAGE_ROOT` | Where every user's global memory lives, as a normal directory | `$ULTRARAG_UI_STORAGE_ROOT`, then this account's home data directory |
| `--ui-port` or `MEMORY_ULTRARAG_UI_PORT` | Also serve a browser view of this memory on this loopback port | off |

The first root is inside the project and the second is in the account's home, and each can be moved without touching the other. `memory-ultra-rag-ui` takes the same `--project-root` and `--storage-root`, plus `--host` and `--port`.

One server instance serves one project, so `--project-root` binds the session: local memory always means that project's memory.

## The four tools

| Tool | Parameters | What it does |
| --- | --- | --- |
| `get_memory_local` | `query`, `limit=10` | Answers a question from this project's memory. |
| `set_memory_local` | `content` | Records one statement in this project's standing memory. |
| `get_memory_global` | `query`, `user_id="default"`, `limit=10` | Answers a question from that user's memory. |
| `set_memory_global` | `content`, `user_id="default"` | Records one statement in that user's standing memory. |

Two verbs, two scopes, and the scope in the name, so the only decision an agent makes is whether a memory is this project's or this user's. A `user_id` holds letters, digits, `_` and `-`, which is both upstream's rule and what keeps a user's memory to one directory.

**A read takes a query, and never returns a memory whole.** These memories grow by appending, so handing one to a model whole would spend the context window on the file instead of on the work. A read searches the standing document and the dated rounds for the query's words and answers with:

- the standing document, whole and bounded, because it is the small curated part and the rules live in it;
- the lines of it the query matched, so the agent can see why without a second copy of the file;
- up to `limit` dated rounds that matched, ranked by the longest word they share with the query, then by how many, then newest first;
- how many rounds matched in total, how many were returned, whether anything was left out, and what was searched.

Matching is a substring test over the files themselves: no index, no model, and nothing outside the scope being read. An empty query is refused rather than answered with everything.

**A write records one statement.** `set_*` appends the statement to the standing document as plain text, with no speaker attached to it: it is what you chose to remember, not something the user said and not something an assistant replied. The dated dialogue files stay the exchange history, and the browser view is what writes them.

## Browser view

Memory is worth looking at, so the same memory the tools serve can be read — and, if you want, added to — in a browser on this machine. Two commands do it:

```bash
# a view of its own, until you stop it
memory-ultra-rag-ui --project-root /ABSOLUTE/PATH/TO/my-project

# or inside the MCP server, reported on stderr and stopped with it
memory-ultra-rag-mcp --project-root /ABSOLUTE/PATH/TO/my-project --ui-port 5052
```

The view shows the bound project's memory and every user's global memory **together**, as one block per scope, newest rounds first:

- each block names its scope, its directory, how many rounds it holds, and its latest date;
- that scope's **standing memory**, whole, with a copy button;
- that scope's **recorded rounds**, filterable, each copyable on its own;
- **Add a round** in the block, which writes the user's line and the assistant's line through the same tool an agent calls, so the file keeps one format and one producer;
- **Edit standing memory** on the block, which replaces the whole document only when it still matches the version the page read, so a write an agent made meanwhile is never overwritten.

`memory-ultra-rag-ui` takes `--project-root`, `--storage-root`, `--host` (loopback only), and `--port` (default 5052). The page is the shared `ui-ultra-rag-mcp` package, pinned by commit, and this package supplies a thin adapter: the browser calls this server, the server reads and writes its own memory, and the view is handed no directory of its own.

## Storage

```
<project-root>/.memory-rag/MEMORY.md                  the project's standing memory
<project-root>/.memory-rag/project/<date>.md          the project's rounds, by day
<storage-root>/memory/<user_id>/MEMORY.md             a user's standing memory
<storage-root>/memory/<user_id>/project/<date>.md     that user's rounds, by day
```

A standing document holds statements, one block per write, after the template UltraRAG seeds it with:

```markdown
# MEMORY
i am jack. i like LLMs.

The draft lives in docs/
```

A round is written in UltraRAG's format, by the browser view, and is the exchange history:

```markdown
# Project Memory 2026-09-22

## 2026-09-22 14:48:30
- user: Where does the draft live?
- assistant: In the project directory.
```

The storage root is where global memory lives, and its default is in the account's home:

| Platform | Default storage root |
| --- | --- |
| Linux | `~/.local/share/memory-ultra-rag-mcp` |
| macOS | `~/Library/Application Support/memory-ultra-rag-mcp` |

Move it with `--storage-root`, or set `MEMORY_ULTRARAG_STORAGE_ROOT` once for every project this account opens. Set `ULTRARAG_UI_STORAGE_ROOT` instead and global memory lands in UltraRAG's own UI storage tree, under the `memory/` directory UltraRAG already reads — which is how a UltraRAG UI shows the same memory with no exchange format and no import step. The flag wins over the variables, and `MEMORY_ULTRARAG_STORAGE_ROOT` wins over `ULTRARAG_UI_STORAGE_ROOT`.

A project's memory is ordinary Markdown in that project: visible, diffable, and optionally committed, by the project's own choice.

## Not yet

Recorded so an absence reads as a decision rather than an oversight, and so a later change extends this server instead of replacing what it stands on. [ADR 0001](docs/decisions/0001-extend-ultrarag-memory.md) carries the reasoning.

- **A read searches words, not meaning.** No synonyms, no stemming, no ranking beyond the longest shared word; a paraphrase is found only if the words are there. There is no model and no index.
- **A read cannot be paged or offset.** `limit` caps the dated rounds; there is no "next page" and no date range.
- **Nothing removes or corrects a statement.** A statement is appended; the standing document is replaced only through the browser view or by hand, and a dated round is never edited.
- **No session tier.** Every statement is durable; nothing expires.
- **No typed or relational structure.** The memory model is upstream's, deliberately.
- **No cross-scope read.** One call reads one scope, so nothing can quietly sweep a project's memory into another's.

## The choices this package makes

| # | Choice | Reason |
| --- | --- | --- |
| 1 | Local memory is kept inside the project, under `.memory-rag` | isolation is the point: a project's memory is private to that project and travels with it |
| 2 | The project is bound at startup, and the project tools take no project argument | one session means one project, so local memory cannot be written into the wrong one |
| 3 | Global memory keeps UltraRAG's layout in the storage tree | the shared memory stays where a UltraRAG UI already reads it |
| 4 | The file names and formats are UltraRAG's, and the two kinds share one implementation | a project's memory and a user's memory have one shape, so either can be read the same way, and a UltraRAG UI already reads the shared one |
| 5 | The surface is four tools, and this server is not a UltraRAG pipeline node | UltraRAG's server base class registers a pipeline `build` tool, and its runner wires servers through generated `server.yaml` files and `output=` annotations. Both belong to a pipeline deployment, so this package ships neither and depends on no UltraRAG code: memory here is a complete solution for a project and an account, served to agents and people directly, not a stage inside someone else's pipeline |
| 6 | The reference revision and its captured output are committed and tested | the format cannot drift quietly: a change fails `tests/test_fidelity.py` |
| 7 | The browser view is the shared `ui-ultra-rag-mcp` package through a thin adapter, pinned by commit | interface code stays in one repository, and this package keeps no second UI |
| 8 | The page reaches memory only through this server: no directory is handed to the browser | one reader and one writer per file, and the page shows what an agent reads |
| 9 | Replacing the standing document is this package's own write, guarded by the digest the page read | upstream only creates that document from its template, and a plain overwrite could drop an agent's write |
| 10 | Global memory defaults to this account's home data directory, and moves by flag or variable | it belongs to the user rather than to a project, and the default follows the sibling repositories' pattern: one flag, one variable, a place in the home directory |
| 11 | The tool names are renamed to two verbs and two scopes, and upstream's names are not re-exposed | the agent's only decision should be which kind of memory it is, so the scope is in the name and there is nothing else to choose between |
| 12 | A read takes a query and answers it, and no tool returns a memory whole | a memory grows by appending; reading it whole spends the context window on the file instead of on the work, and would be unusable after a few weeks |
| 13 | Retrieval is a substring search over the scope's own files, with no index and no model | a grep is what these files support, it is explainable, and it keeps the no-model line that the rest of the design rests on |
| 14 | A statement is appended to the standing document, and the dated files stay the exchange history | a statement is not an exchange, and attributing one to the user or to an assistant would put a claim in the file that nobody made |

## Develop and test

```bash
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest
uv build
```

The suite runs without any UltraRAG checkout. It covers one scope's behaviour (template, create-on-read, round format, append, day rollover, refusals), the four tools over the real stdio server, project isolation between two projects, the shared global tree, a byte-for-byte comparison against the files UltraRAG's own server produced (`tests/fixtures/upstream/README.md` records how they were captured), and the browser view: its scopes, its reads, its two writes, the scope a request may name, and the routes a page can call.

The formats are also compared against a **real** checkout when you have one: point `ULTRARAG_CHECKOUT` at it and `tests/test_upstream_differential.py` starts that checkout's own `servers/memory/src/memory.py` over stdio, gives it the same inputs, and compares the bytes both wrote, the payload keys both returned, and the surface differences this package chose. It is the assurance a wrapper would have given by construction, without depending on a checkout at runtime.

```bash
ULTRARAG_CHECKOUT=/ABSOLUTE/PATH/TO/UltraRAG uv run --frozen pytest -m upstream
```

## Credit and licensing

The memory model, the file names, and the file formats are UltraRAG's, from [`OpenBMB/UltraRAG`](https://github.com/OpenBMB/UltraRAG) at commit `3a709a2` (Apache-2.0), and the tools are its memory tools renamed and extended — see [ADR 0001](docs/decisions/0001-extend-ultrarag-memory.md). Credit belongs to the UltraRAG team and contributors, including participants from THUNLP, NEUIR, OpenBMB, and AI9stars.

This package is licensed under the [Apache License 2.0](LICENSE). It is an independent project: not an official release of UltraRAG, not affiliated with or endorsed by its maintainers, and the UltraRAG name describes what it serves.

The graph memory server ([`graph-memory-ultra-rag-mcp-server`](https://github.com/AhmedKishki/graph-memory-ultra-rag-mcp-server)) is the collection's other memory project, which extends memory into a typed, relational store.
