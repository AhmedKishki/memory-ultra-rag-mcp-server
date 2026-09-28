# Memory UltraRAG MCP

**Purpose:** serve UltraRAG's memory to an agent over stdio MCP, in two kinds:

- **local memory** belongs to the project this server is bound to and lives inside that repository, under `.memory-rag`, so a project carries its memory with it and no other project reads it;
- **global memory** belongs to the account and lives in UltraRAG's shared storage tree, so every instance serving that tree reads the same memory. It is not a project's: who the user is, what they want remembered everywhere, and the **durable rules that apply wherever they work**.

Both kinds work the same way: one standing document per scope, `MEMORY.md`, written in UltraRAG's format. The agent chooses which kind a write goes to, and the two tools of each kind take the same arguments apart from the global pair's absence of a `user_id`. A standing instruction the user gave once belongs in global memory's standing document, where it is read at the start of every conversation, rather than anywhere local.

There are no dated rounds. Upstream keeps a day-per-file dialogue log beside the standing document; this server does not, because anything worth keeping is recorded as a statement and a date does not change what it is. That divergence, and the one below, are in the choice table.

The behaviour, the tool names, the file names, and the byte formats are UltraRAG's, taken from `servers/memory/src/memory.py` at the pinned revision below and checked against files that revision produced. The difference this package makes is where a project's memory lives: UltraRAG keeps everything under one storage tree, and this server keeps each project's memory inside the project. What the extension is, and what it deliberately leaves alone, is decided in [ADR 0001](docs/decisions/0001-extend-ultrarag-memory.md).

| Reference revision | Declared version |
| --- | --- |
| `3a709a2aea3fbe46acca59c422621c94b6e86857` | `0.3.0.2` |

## Requirements

CPython 3.11 or 3.12, Linux, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), and a SQLite built with FTS5, which the search index needs. Run the probe to check that last one:

```bash
uv run --frozen python scripts/check_sqlite_fts5.py
```

Without FTS5 the server refuses to start, because a read would have to pass over every file a memory holds, which is the cost the index exists to remove.

A lookup also runs a local sentence embedder on the CPU, so the first use fetches its weights once — about 65 MB — into this package's own cache under `~/.cache/memory-ultra-rag-mcp/models`, and never again. **No API is used at any point**: the model runs in this process, and a machine that cannot fetch it still starts, records statements, and answers in words, saying `semantic_available: false` in every read so a caller is never left guessing why an answer is thinner. Which model it is takes one line, in a documented table.

No UltraRAG checkout is needed: the formats are verified against captured fixtures and, when a checkout is available, against the real server. The browser view is the shared [`ui-ultra-rag-mcp`](https://github.com/AhmedKishki/ui-ultra-rag-mcp) package, pinned by commit and installed with this one.

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
| `--storage-root` or `MEMORY_ULTRARAG_STORAGE_ROOT` | Where the account's global memory lives, as a normal directory | `$ULTRARAG_UI_STORAGE_ROOT`, then this account's home data directory |
| `--ui-port` or `MEMORY_ULTRARAG_UI_PORT` | Also serve a browser view of this memory on this loopback port | off |

The first root is inside the project and the second is in the account's home, and each can be moved without touching the other. `memory-ultra-rag-ui` takes the same `--project-root` and `--storage-root`, plus `--host` and `--port`.

One server instance serves one project, so `--project-root` binds the session: local memory always means that project's memory.

## The four tools

| Tool | Parameters | What it does |
| --- | --- | --- |
| `get_memory_local` | `query`, `limit=10` | Answers a question from this project's memory. |
| `set_memory_local` | `content`, `type` | Records one typed statement in this project's standing memory. |
| `get_memory_global` | `query`, `limit=10` | Answers a question from the account's global memory. |
| `set_memory_global` | `content`, `type` | Records one typed statement in the account's global standing memory. |

Two verbs, two scopes, and the scope in the name, so the only decision an agent makes is whether a memory is this project's or the account's. There is no `user_id`: **one server is bound to one project**, and that project reaches exactly two memories — its own, inside its repository, and the one global memory, which belongs to the account and is shared by every project on the same storage root.

**A read takes a query, and returns only what matched.** These memories grow by appending, so handing one to a model whole would spend the context window on the file instead of on the work. A read answers in three stages:

1. **Words**, against an FTS5 index of the scope's own statements. Every query term is required together first, because a question like "where does the draft live" means that; if nothing holds them all, the two rarest words are tried alone, so an over-strict conjunction never answers with nothing. English stop words are dropped before either attempt — the word side requires every term it is given, so a term no statement contains does not narrow the answer, it empties it.
2. **Meaning**, against the vector of every statement in the scope, by exact scan. A unit below a cosine floor of 0.72 is dropped, and if the best candidate itself is below the floor the whole dense side abstains rather than returning the nearest thing. A model that is not resident, or vectors built by a different model, are reported as unavailable instead of answered worse.
3. **Order**, by weighted reciprocal rank fusion — lexical at 1.25, dense at 1.0, both over rank 60 — and then a local English cross-encoder reorders the first ten candidates, which is what a memory of one-line statements needs and what a single vector cannot do.

The answer carries:

- the statements that matched, at most `limit` of them, best first;
- `matched_by` on the answer and on each unit: words, meaning, or both, and `reranked` for the third stage;
- whether anything was left out, whether the meaning side was available at all, and how long the read took.

Every unit names the document it came from. Nothing else is returned: no file, no document, no whole memory, and no way to ask for one. The label a statement was filed under is in the index and in the answer's own search, and is not in the returned text — a caller that recorded a statement gets the statement back, and a query naming the category still finds them.

Two derived indexes sit beside the files and hold nothing the Markdown does not: `index.sqlite3` for words, rebuilt in seconds, and `index-vectors.sqlite3` for meaning, whose cost is one embedding per unit. A read costs the same whatever the memory holds — measured on this repository's corpus shape, a word query costs the same over 50,000 statements as over 1,000, and a vector scan is arithmetic over a few megabytes. The record is still the Markdown, so either file can be deleted and rebuilt.

The write side keeps both current: the tools, the browser view, and `memory-ultra-rag-reindex`. A read never walks the scope to discover a change; it spends two `stat` calls and reports `index_files_behind` when a file was edited in place, and that report is what the reindex command answers.

**A write records one typed statement.** `set_*` appends the statement to the standing document as plain text, with no speaker attached to it: it is what you chose to remember, not something the user said and not something an assistant replied. A statement is the whole record, and nothing dates it.

**Every statement carries a type, and a read does not repeat it.** `type` is required, and it is yours: this server defines no categories, checks none, and interprets none. It is one run of block letters — `RULE`, `PLAN`, `PREFERENCE` — with no white space and nothing else in it, and it is written as the statement's own first words followed by a colon, so the standing document is plain prose with no field syntax and a person opening `MEMORY.md` reads it the way they would read a note. A type carries no meaning of its own: it exists to help a read find the statements filed under it, and the same word reused across statements is what makes that work. It is indexed and embedded with the statement, so a query naming the category finds them. It is not in the answer: `get_*` returns the statement as you wrote it, without the label. That asymmetry is the point — a caller that recorded a statement wants the statement back, not the category it happened to file it under, while the category stays in the record and stays searchable. Case is the one thing normalised rather than refused: a type given as `rule` is recorded as `RULE`.

```markdown
RULE: The user's decision is final and is not revisited without being asked.
```

## Browser view

Memory is worth looking at, so the same memory the tools serve can be read — and, if you want, added to — in a browser on this machine. Two commands do it:

```bash
# a view of its own, until you stop it
memory-ultra-rag-ui --project-root /ABSOLUTE/PATH/TO/my-project

# or inside the MCP server, reported on stderr and stopped with it
memory-ultra-rag-mcp --project-root /ABSOLUTE/PATH/TO/my-project --ui-port 5052
```

The view shows the bound project's memory and the account's global memory **together**, as one block per scope:

- each block names its scope, its directory, and whether its standing document is there;
- that scope's **standing memory**, whole, with a copy button;
- **Edit standing memory** on the block, which replaces the whole document only when it still matches the version the page read, so a write an agent made meanwhile is never overwritten.

The shared page still has a section for dated exchanges, because it asks for them under the same capability that shows the memory view and an action that refused would take the whole view down. This adapter answers that request with an empty list, which is the true answer here, and the page's own empty-state wording is what says otherwise. That wording is a rough edge in `ui-ultra-rag-mcp`, not here.

`memory-ultra-rag-ui` takes `--project-root`, `--storage-root`, `--host` (loopback only), and `--port` (default 5052). The page is the shared `ui-ultra-rag-mcp` package, pinned by commit, and this package supplies a thin adapter: the browser calls this server, the server reads and writes its own memory, and the view is handed no directory of its own.

## Storage

```
<project-root>/.memory-rag/MEMORY.md                  the project's standing memory
<project-root>/.memory-rag/index.sqlite3              derived word index, disposable in seconds
<project-root>/.memory-rag/index-vectors.sqlite3        derived vector index, disposable in a re-embedding
<storage-root>/memory/default/MEMORY.md            the account's global standing memory
<storage-root>/memory/default/index.sqlite3          derived word index, disposable in seconds
<storage-root>/memory/default/index-vectors.sqlite3    derived vector index, disposable in a re-embedding
```

The global memory lives in `memory/default/`: `memory` is UltraRAG's own directory name, and `default` is the user it names when none is given, which keeps the layout upstream's and keeps a UltraRAG UI able to read it. It is one memory, not one per user, and the tools take no identifier for it.

A standing document holds statements, one block per write, after the template UltraRAG seeds it with:

```markdown
# MEMORY
i am jack. i like LLMs.
```

A statement is one block: a label, a colon, the text, and a blank line after it.

```markdown
# MEMORY
i am jack. i like LLMs.

NOTE: The draft lives in docs/
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

- **A read understands a query, it does not rewrite it.** No query expansion, no stemming, no synonym list, and no model asked to rephrase; a statement is found because its meaning is near the query's, which is close to but not the same as understanding the question.
- **A read cannot be paged or offset.** `limit` caps the units; there is no "next page" and no date range.
- **A hand edit is not noticed by a read.** The write side keeps the index current, so a file edited behind its back is reported by `index_files_behind` and picked up by `memory-ultra-rag-reindex` — traded deliberately for a read that never walks the scope.
- **The semantic side is unmeasured here.** This release adds it; the measurement that decides which model, and whether it beats words on this data, is the next phase. Nothing in this package claims a gain it has not measured.
- **Nothing removes or corrects a statement.** A statement is appended; the standing document is replaced only through the browser view or by hand.
- **No session tier.** Every statement is durable; nothing expires.
- **No typed or relational structure.** The memory model is upstream's, deliberately.
- **No cross-scope read.** One call reads one scope, so nothing can quietly sweep a project's memory into another's, and a project is reachable only by the server bound to it.

## The choices this package makes

| # | Choice | Reason |
| --- | --- | --- |
| 1 | Local memory is kept inside the project, under `.memory-rag` | isolation is the point: a project's memory is private to that project and travels with it |
| 2 | The project is bound at startup, and the project tools take no project argument | one session means one project, so local memory cannot be written into the wrong one |
| 3 | Global memory keeps UltraRAG's layout in the storage tree | the shared memory stays where a UltraRAG UI already reads it |
| 4 | The file names and formats are UltraRAG's, and the two kinds share one implementation | a project's memory and the global one have one shape, so either can be read the same way, and a UltraRAG UI already reads the global one |
| 5 | The surface is four tools, and this server is not a UltraRAG pipeline node | UltraRAG's server base class registers a pipeline `build` tool, and its runner wires servers through generated `server.yaml` files and `output=` annotations. Both belong to a pipeline deployment, so this package ships neither and depends on no UltraRAG code: memory here is a complete solution for a project and an account, served to agents and people directly, not a stage inside someone else's pipeline |
| 6 | The reference revision and its captured output are committed and tested | the format cannot drift quietly: a change fails `tests/test_fidelity.py` |
| 7 | The browser view is the shared `ui-ultra-rag-mcp` package through a thin adapter, pinned by commit | interface code stays in one repository, and this package keeps no second UI |
| 8 | The page reaches memory only through this server: no directory is handed to the browser | one reader and one writer per file, and the page shows what an agent reads |
| 9 | Replacing the standing document is this package's own write, guarded by the digest the page read | upstream only creates that document from its template, and a plain overwrite could drop an agent's write |
| 10 | Global memory defaults to this account's home data directory, and moves by flag or variable | it belongs to the account rather than to a project, and the default follows the sibling repositories' pattern: one flag, one variable, a place in the home directory |
| 11 | The tool names are renamed to two verbs and two scopes, and upstream's names are not re-exposed | the agent's only decision should be which kind of memory it is, so the scope is in the name and there is nothing else to choose between |
| 12 | A read takes a query and answers it, and no tool returns a memory whole | a memory grows by appending; reading it whole spends the context window on the file instead of on the work, and would be unusable after a few weeks |
| 13 | The search runs over an FTS5 index built from the memory's own document, kept beside it and disposable | a read is then a lookup and a page of rows rather than a pass over the whole memory, so its cost does not grow with it; the document stays the record, so an index is never the thing that is true |
| 14 | The index follows the files instead of leading them, and no write path updates it | a memory edited by hand or by a UltraRAG UI is read correctly, because a read compares each file against what was indexed and re-reads only what changed |
| 15 | A statement is appended to the standing document, and nothing is attributed to a speaker | attributing a statement to the user or to an assistant would put a claim in the file that nobody made |
| 16 | Global memory has no `user_id`: it is the account's one memory, in the directory upstream uses for the user it is given when none is named | a project is identified by the repository it is bound to, so a user dimension would be a second identity for something already identified; the directory is left as upstream's so the layout, an existing store, and a UltraRAG UI are unaffected |
| 17 | A read works in words and in meaning, fused, with the model behind a seam | a memory is largely proper nouns, paths and identifiers, which the collection's own measurements say need words; a query that never used the caller's wording needs meaning |
| 18 | The model is local, pinned, listed, and run on the CPU in this process | no API is used at this stage, the weights are fetched once into this package's own cache, and a stronger or multilingual model is one line rather than a rewrite |
| 19 | Recording is asynchronous; a lookup is not | a write may cost an append and a queued unit and never fails because the lookup layer is missing; a read may only do constant work, so it never embeds units, walks the scope, or loads a model |
| 20 | The write side keeps the index current, and `memory-ultra-rag-reindex` answers for a hand edit | sweeping every file on every read was measured at about 0.18 ms per source in the collection's other server, which is hundreds of milliseconds at a few thousand daily files |
| 21 | A statement is filed under a type the caller names, one run of block letters written as the statement's own first words and a colon, indexed with it and never returned by a read | a memory is a list of unrelated statements, and a category is what makes a query for "which of these are corrections" answerable; the type is the caller's own and carries no meaning, so the server defines no vocabulary and reads the label as opaque text — a single word keeps the document plain prose with no field syntax and keeps the label separable from the statement, and the recorded bytes are the one place this package's standing document differs from upstream's, where a fresh scope still matches upstream byte for byte (`tests/test_fidelity.py`, and the differential test when a checkout is available) |
| 22 | A scope is its standing document, with no dated exchange log beside it | anything worth keeping is recorded as a statement, and a date does not change what a statement is, so the log upstream keeps beside the document would be a second record of the same thing with a shape nothing here can answer a question from — the store, the index and the tools have one kind of unit, and the differential test asserts this side writes no dated file at all |
| 23 | A lookup drops English stop words before it searches | the word side asks FTS5 for every query term at once, so a term in no statement does not narrow the answer, it empties it, and the fallback then rescues the query by accident: "what is research-rag for" finds nothing, because no statement contains "what" |
| 24 | Reranking is on by default, at a depth of ten candidates | a memory's units are one line each, which is where a bi-encoder's vector is weakest, and a cross-encoder reads the pair rather than either side alone; measured here at 54 ms for ten candidates, which is what sets the depth against the 250 ms read ceiling. That it is more *accurate* is unmeasured, and nothing in this package claims it |
| 25 | This memory is English, and the models and the stop words say so | a memory is a record of what one person decided and asked to be kept, in one language; the embedder is `bge-small-en-v1.5` and the reranker is an English cross-encoder, so anything else is outside what the lookup was built to answer |

## Develop and test

```bash
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest
uv build
```

The suite runs without any UltraRAG checkout. It covers one scope's behaviour (template, create-on-read, the statement format, append, refusals, and that a scope is one document), the four tools over the real stdio server, the local reranker's call into its runtime, project isolation between two projects, the shared global tree, a byte-for-byte comparison against the standing document UltraRAG's own server produced (`tests/fixtures/upstream/README.md` records how it was captured), and the browser view: its scopes, its reads, its one write, the scope a request may name, and the routes a page can call.

The formats are also compared against a **real** checkout when you have one: point `ULTRARAG_CHECKOUT` at it and `tests/test_upstream_differential.py` starts that checkout's own `servers/memory/src/memory.py` over stdio, gives it the same inputs, and compares the bytes both wrote, the payload keys both returned, and the surface differences this package chose. It is the assurance a wrapper would have given by construction, without depending on a checkout at runtime.

```bash
ULTRARAG_CHECKOUT=/ABSOLUTE/PATH/TO/UltraRAG uv run --frozen pytest -m upstream
```

## Credit and licensing

The memory model, the file names, and the file formats are UltraRAG's, from [`OpenBMB/UltraRAG`](https://github.com/OpenBMB/UltraRAG) at commit `3a709a2` (Apache-2.0), and the tools are its memory tools renamed and extended — see [ADR 0001](docs/decisions/0001-extend-ultrarag-memory.md). Credit belongs to the UltraRAG team and contributors, including participants from THUNLP, NEUIR, OpenBMB, and AI9stars.

This package is licensed under the [Apache License 2.0](LICENSE). It is an independent project: not an official release of UltraRAG, not affiliated with or endorsed by its maintainers, and the UltraRAG name describes what it serves.

The graph memory server ([`graph-memory-ultra-rag-mcp-server`](https://github.com/AhmedKishki/graph-memory-ultra-rag-mcp-server)) is the collection's other memory project, which extends memory into a typed, relational store.
