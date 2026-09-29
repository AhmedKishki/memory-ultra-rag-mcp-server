# Memory UltraRAG MCP

**Purpose:** serve UltraRAG's memory to an agent over stdio MCP, in two kinds:

- **local memory** belongs to the project this server is bound to and lives inside that repository, under `.memory-rag`, so a project carries its memory with it and no other project reads it;
- **global memory** belongs to the account and lives in UltraRAG's shared storage tree, so every instance serving that tree reads the same memory. It is not a project's: who the user is, what they want remembered everywhere, and the **durable rules that apply wherever they work**.

Both kinds are one standing document per scope, `MEMORY.md`, of statements in UltraRAG's format, and the two kinds take the same arguments. A standing instruction the user gave once belongs in global memory, where it is read at the start of every conversation, rather than anywhere local.

There are no dated rounds. Upstream keeps a day-per-file dialogue log beside the standing document; this server does not, because anything worth keeping is recorded as a statement. Every divergence from upstream is in the choice table below.

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

## The seven tools

| Tool | Parameters | What it does |
| --- | --- | --- |
| `get_memory_local` | `query`, `limit=10`, `kind` | Returns the statements in this project's memory that match `query`, optionally only those filed under `kind`. |
| `get_memory_global` | `query`, `limit=10`, `kind` | Returns the statements in the account's memory that match `query`, optionally only those filed under `kind`. |
| `set_memory_local` | `content`, `kind=ITEM` | Records one statement at the top of this project's memory. |
| `set_memory_global` | `content`, `kind=ITEM` | Records one statement at the top of the account's memory. |
| `forget_memory_local` | `query` | Removes the one statement this project's memory holds whose text is exactly `query`. |
| `forget_memory_global` | `query` | Removes the one statement the account's memory holds whose text is exactly `query`. |
| `set_memory_handoff` | `content` | Records this session's handoff for the next one, and removes the previous handoff. |

Four verbs, and the scope in the name where there is one to name, so the only decision an agent makes is whether a memory is this project's or the account's. There is no `user_id`: **one server is bound to one project**, and that project reaches exactly two memories — its own, inside its repository, and the one global memory, which belongs to the account and is shared by every project on the same storage root. A handoff is only this project's, because a handoff is about a session of working in this project.

**A kind is asked for, not searched for.** It is a column of the record, not a word in the statement, so `kind` on a read narrows the answer to one category and a query that merely names a category finds nothing. That is the honest answer: the words are not in the text.

**Forgetting matches the words, never the meaning.** Pass the exact text a read gave you and that statement is removed; pass anything else and nothing is, and the answer says so. Two statements with the same text is also a refusal rather than a deletion of both, because a memory that can lose two statements to one vague query cannot be trusted with the user's decisions. A forgotten statement takes its own history with it, and it stops answering at once rather than at the next rebuild.

**A handoff is one statement, not a list of them.** It is filed under the reserved kind `HANDOFF` at the top of the project's memory, and the previous handoff is removed as part of the same call, so the caller never has to know that an older one existed. `replaced` in the answer says how many went: it was still data, and the caller is entitled to know it is gone.

**A read takes a query, and returns only what matched.** These memories grow by appending, so handing one to a model whole would spend the context window on the file instead of on the work. A read answers in three stages:

1. **Words**, against an FTS5 index of the scope's own statements. Every query term is required together first, because a question like "where does the draft live" means that; if nothing holds them all, the two rarest words are tried alone, so an over-strict conjunction never answers with nothing. English stop words are dropped before either attempt — the word side requires every term it is given, so a term no statement contains does not narrow the answer, it empties it.
2. **Meaning**, against the vector of every statement in the scope, by exact scan. A unit below a cosine floor of 0.72 is dropped, and if the best candidate itself is below the floor the whole dense side abstains rather than returning the nearest thing. A model that is not resident, or vectors built by a different model, are reported as unavailable instead of answered worse.
3. **Order**, by weighted reciprocal rank fusion — lexical at 1.25, dense at 1.0, both over rank 60 — and then a local English cross-encoder reorders the first ten candidates, which is what a memory of one-line statements needs and what a single vector cannot do.
4. **Recency**, last and only as a bonus. Every statement is dated when it is recorded, and among the statements that matched about as well, the newest is worth `recency_bonus` of its own score, the next half of that, and so on. At the default of ten per cent a materially better match still comes first and a close one does not; it is a knob rather than a guarantee, so `recency_bonus=1.0` means "the newest answer whatever it says" and `0.0` turns the preference off for a memory that must be reproducible from its scores alone.

The answer carries:

- the statements that matched, at most `limit` of them, best first;
- `matched_by` on the answer and on each unit: words, meaning, or both, and `reranked` for the third stage;
- each statement's `kind`, `added_at`, `recalls`, and `last_recalled_at`, so a statement that has never come up can be told from one that comes up constantly, and `stamp`, its position counting from the top of the document;
- `units_pending` for statements the record holds that have no vector yet, and `document_rewritten` when a read brought the document back in line with the record — which is how a hand edit to an export is disclosed rather than lost;
- whether anything was left out, whether the meaning side was available at all, and how long the read took.

Every unit names the document it came from. Nothing else is returned: no file, no document, no whole memory, and no way to ask for one. The kind is returned as its own field, and the returned `text` is the statement itself, so a caller that recorded something gets back the words they recorded rather than the kind they filed them under.

Counting a recall is one `UPDATE` per statement answered, in the same SQLite transaction the answer was read from, so it is atomic without a temporary file and a read's own work stays proportional to what it returned rather than to what the memory holds.

One file sits beside the Markdown. `memory.sqlite3` is the word index and the vector index together, and it also holds what the Markdown cannot: a statement's date and how often it has been recalled. The word index inside it is rebuilt in seconds; the vectors and the counts are not rebuilt by anything. A read that finds nothing changed costs the same whatever the memory holds — measured on this repository's corpus shape, a word query costs the same over 50,000 statements as over 1,000, and a vector scan is arithmetic over a few megabytes. The one cost that varies with the memory is the re-read a read performs after a hand edit, and it is paid once per edit. The record is still the Markdown, so deleting the file costs a rebuild and a re-embedding and cannot lose a statement.

**Editing the document by hand changes nothing, and the read says so.** The document is an export of the record, so a line typed into it, or a line deleted from it, is not memory. A read writes the document out from the record and reports `document_rewritten: true`, so an edit that did not take is visible at the moment it is ignored. Statements change through `set_memory_*` and `forget_memory_*`, or through the page, and each of them changes the record first. A record with nothing in it adopts the document, which is how a memory written by an older version is recovered.

**A write records one typed statement, at the top.** `set_*` inserts the statement into the standing document as plain text, with no speaker attached to it: it is what you chose to remember, not something the user said and not something an assistant replied. It goes above everything already recorded, so the file reads newest first and a read can prefer new information. Two writers are serialised by a lock in the system temporary directory, so a record never lands in a file another record just rewrote, and the lock leaves nothing behind in the memory if a process dies holding it.

**Every statement carries a kind.** It is the caller's: this server defines no categories, checks none, and interprets none. It is one run of block letters — `RULE`, `PLAN`, `PREFERENCE` — with no white space, held as a column of the record with the statement. It exists so a read can be narrowed to one category, which is why a read takes it as an argument. It is a field in the answer and not part of the returned text, because a caller that recorded a statement wants the statement back and not the kind it was filed under.

**A missing kind is `ITEM`, not a gap.** The parameter is optional and defaults to `ITEM`, so every statement carries one even when the caller named none, and a read narrowed to `ITEM` finds the ones nobody else filed. A statement recovered from an export is `ITEM` too, because an export holds no kinds. Case is normalised rather than refused: `rule` is recorded as `RULE`.

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

The shared page still asks for dated exchanges, because it fetches them under the same capability that shows the memory view and an action that refused would take the whole view down. This adapter answers with an empty list, which is the true answer here; the page's own empty-state wording is the rough edge, and it belongs to `ui-ultra-rag-mcp`.

`memory-ultra-rag-ui` takes `--project-root`, `--storage-root`, `--host` (loopback only), and `--port` (default 5052). The page is the shared `ui-ultra-rag-mcp` package, pinned by commit, and this package supplies a thin adapter: the browser calls this server, the server reads and writes its own memory, and the view is handed no directory of its own.

## Storage

```
<project-root>/.memory-rag/MEMORY.md                  the project's standing memory: the record
<project-root>/.memory-rag/memory.sqlite3              word index, vectors, and each statement's history
<storage-root>/memory/default/MEMORY.md            the account's global standing memory: the record
<storage-root>/memory/default/memory.sqlite3          word index, vectors, and each statement's history
```

The global memory lives in `memory/default/`: `memory` is UltraRAG's own directory name, and `default` is the user it names when none is given, which keeps the layout upstream's and keeps a UltraRAG UI able to read it. It is one memory, not one per user, and the tools take no identifier for it.

**The record is the table.** `memory.sqlite3` holds every statement: its text, the kind it was filed under, its place in the document, and when it was added and how often it has been recalled. The same table is the FTS5 word index, so a statement is stored once and found by words without a second copy to keep in step. A statement is one line of prose and a row — not a tagged line.

`MEMORY.md` is an **export** of that record: the same statements as plain prose, newest first, rewritten whenever the memory changes and on a reindex. It is what a database cannot be — a memory you can open, read, diff, commit, and carry. It is not read as an input, so a hand edit to it is overwritten with the record and the read that notices says so, and it is a rendering rather than a backup: a file recovered from one has the right words in the right order, every statement undated and filed as `ITEM`, because the types, the dates, and the counts lived in the record.

An empty scope, before anything is recorded, holds the template UltraRAG seeds a document with:

```markdown
# MEMORY
i am jack. i like LLMs.
```

**The newest statement is at the top**, so the file a person opens reads in the order things were remembered and a read can prefer what is new.

```markdown
# MEMORY

Always cite the commit that introduced a change.

The draft lives in docs/
```

The file no longer writes a kind in front of a statement, and no longer carries
the seed. A file written by an older version is read once on upgrade — the
`RULE: ` prefix becomes the row's kind and the words after it become the
statement — and the next export has neither.

`memory.sqlite3` beside it is the one file, and it is the record: an FTS5 table of every statement — its text, its kind, and its position — with a vector per statement for the meaning side, and four columns on the row itself for what the document cannot hold: `added_at`, `recalls`, and `last_recalled_at`. A statement is one row and not a row plus a file, and a read counts a recall by updating the rows it just returned, in the transaction it read them in. Nothing is written beside the memory and no thread is involved, and the counting is proportional to the answer rather than to the file.

It is therefore *not* fully derived: deleting it costs a re-read of the document **plus** a re-embedding **plus** the dates and counts, and the last of those is the one thing no rebuild can restore. `memory-ultra-rag-reindex` collects the rows of statements that have since been reworded or removed, and the document itself is never in this file's power to lose. One file rather than three: the index, the vectors, and the history are all keyed by the same statement digest and all answer the same read, so one file means one schema version, one connection, and no way for two of them to disagree about one statement.

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
- **A hand edit is picked up on the next read, not before it.** A statement you add is findable by its words at once and by its meaning once its vector is embedded; the read discloses both. Deleting a statement stops it answering immediately — a stale index can no longer serve one that is gone.
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
| 4 | The file name and the scope layout are UltraRAG's, and the two kinds share one implementation | a project's memory and the global one have one shape, so either can be read the same way, and a UltraRAG UI finds the global memory where it looks. What is in that file is now an export rather than the record — choice 33 |
| 5 | The surface is seven memory tools, and this server is not a UltraRAG pipeline node | UltraRAG's server base class registers a pipeline `build` tool, and its runner wires servers through generated `server.yaml` files and `output=` annotations. Both belong to a pipeline deployment, so this package ships neither and depends on no UltraRAG code: memory here is a complete solution for a project and an account, served to agents and people directly, not a stage inside someone else's pipeline |
| 6 | The reference revision and its captured output are committed and tested | the format cannot drift quietly: a change fails `tests/test_fidelity.py` |
| 7 | The browser view is the shared `ui-ultra-rag-mcp` package through a thin adapter, pinned by commit | interface code stays in one repository, and this package keeps no second UI |
| 8 | The page reaches memory only through this server: no directory is handed to the browser | one reader and one writer per file, and the page shows what an agent reads |
| 9 | Replacing the standing document is this package's own write, guarded by the digest the page read | upstream only creates that document from its template, and a plain overwrite could drop an agent's write |
| 10 | Global memory defaults to this account's home data directory, and moves by flag or variable | it belongs to the account rather than to a project, and the default follows the sibling repositories' pattern: one flag, one variable, a place in the home directory |
| 11 | The tool names are renamed to two verbs and two scopes, and upstream's names are not re-exposed | the agent's only decision should be which kind of memory it is, so the scope is in the name and there is nothing else to choose between |
| 12 | A read takes a query and answers it, and no tool returns a memory whole | a memory grows by appending; reading it whole spends the context window on the file instead of on the work, and would be unusable after a few weeks |
| 13 | The search runs over an FTS5 index built from the memory's own document, kept beside it and disposable | a read is then a lookup and a page of rows rather than a pass over the whole memory, so its cost does not grow with it; the document stays the record, so an index is never the thing that is true |
| 14 | The index follows the files instead of leading them, and no write path updates it | a memory edited by hand or by a UltraRAG UI is read correctly, because a read compares each file against what was indexed and re-reads only what changed |
| 15 | A statement is inserted at the top of the standing document, and nothing is attributed to a speaker | attributing a statement to the user or to an assistant would put a claim in the file that nobody made. Insertion rather than appending is choice 27 |
| 16 | Global memory has no `user_id`: it is the account's one memory, in the directory upstream uses for the user it is given when none is named | a project is identified by the repository it is bound to, so a user dimension would be a second identity for something already identified; the directory is left as upstream's so the layout, an existing store, and a UltraRAG UI are unaffected |
| 17 | A read works in words and in meaning, fused, with the model behind a seam | a memory is largely proper nouns, paths and identifiers, which the collection's own measurements say need words; a query that never used the caller's wording needs meaning |
| 18 | The model is local, pinned, listed, and run on the CPU in this process | no API is used at this stage, the weights are fetched once into this package's own cache, and a stronger or multilingual model is one line rather than a rewrite |
| 19 | Recording is asynchronous; a lookup is not | a write may cost an append and a queued unit and never fails because the lookup layer is missing; a read may only do constant work, so it never embeds units, walks the scope, or loads a model |
| 20 | A read keeps the word index in step with the Markdown, and `memory-ultra-rag-reindex` settles the vectors a hand edit owes | superseded by choice 26, which reverses the reasoning: the sweep this trade was measured against counted a few thousand *dated files*, and a scope here is one document — a read now fingerprints it and re-reads it only when it changed |
| 21 | A statement is filed under a kind the caller names, held as a column of the record and returned as a field of its own rather than as part of the text | a memory is a list of unrelated statements, and a category is what makes a read for one kind answerable; the kind is the caller's own and carries no meaning, so the server defines no vocabulary and stores the word as it was given. A single word keeps it separable from the statement, and the document stays plain prose |
| 22 | A scope is its standing document, with no dated exchange log beside it | anything worth keeping is recorded as a statement, and a date does not change what a statement is, so the log upstream keeps beside the document would be a second record of the same thing with a shape nothing here can answer a question from — the store, the index and the tools have one kind of unit, and the differential test asserts this side writes no dated file at all |
| 23 | A lookup drops English stop words before it searches | the word side asks FTS5 for every query term at once, so a term in no statement does not narrow the answer, it empties it, and the fallback then rescues the query by accident: "what is research-rag for" finds nothing, because no statement contains "what" |
| 24 | Reranking is on by default, at a depth of ten candidates | a memory's units are one line each, which is where a bi-encoder's vector is weakest, and a cross-encoder reads the pair rather than either side alone; measured here at 54 ms for ten candidates, which is what sets the depth against the 250 ms read ceiling. That it is more *accurate* is unmeasured, and nothing in this package claims it |
| 25 | This memory is English, and the models and the stop words say so | a memory is a record of what one person decided and asked to be kept, in one language; the embedder is `bge-small-en-v1.5` and the reranker is an English cross-encoder, so anything else is outside what the lookup was built to answer |
| 26 | A read brings the document back in line with the record, and a hand edit to it is disclosed | a memory is something a person is meant to open, and a hand edit has to do something definite rather than nothing. With the record in the table, the definite thing is that the edit does not take: the document is written out from the record and the read reports `document_rewritten`. A statement is changed by `forget_memory_*` and `set_memory_*`, or by the page, and each of them changes the record first. This supersedes the earlier version of this row, which made the Markdown the record and kept a read in step with it by fingerprint (~17 µs unchanged, ~2 ms to re-read) — a property worth nothing once the words are not read from it |

| 27 | A new statement is inserted at the top of the standing document, and a read prefers new information by a bounded bonus | upstream appends, so its oldest statement is first, and a memory that only grows ends up answering with what it learned months ago as readily as what it learned an hour ago. Insertion on top makes the file a person opens read in the order things happened, and the bonus — at most ten per cent of the fused score, decaying with distance from the top — settles a tie towards the caller's current state without ever preferring a weaker match. The block format is untouched and a fresh scope is still byte-identical to upstream's (`tests/test_fidelity.py`) |
| 28 | Forgetting is a tool, and it matches the words exactly | a memory that cannot be emptied accumulates statements that are no longer true, and a later read returns them as confidently as the true ones. The match is on the exact text and never on meaning, and a query that matches several statements removes none of them, so one vague call cannot lose two decisions. The alternative — leaving removal to a person editing the Markdown — is what made the gap hard to notice |
| 29 | A handoff is one statement under a reserved kind, and setting it removes the previous one | yesterday's handoff and today's are both plausible and neither is current, which is worse than having none: a session cannot tell which one it is reading. Replacing by kind means the caller records a handoff and never thinks about the old one, and `replaced` reports what went so the loss is disclosed rather than silent. The handoff is a project's own state, so it has no global form |
| 30 | A statement's kind is optional and defaults to `ITEM` | a required kind is a decision every write has to make before it can make the write. A default that is a real kind means every statement is filed under something findable, and `ITEM` is exactly the right name for a statement nobody classified |
| 31 | When a statement was added, and how often it has been recalled, are a table in the same SQLite file as the word index | a statement that has been recalled fifty times and one that has never been recalled are not equally worth keeping, and neither fact is in the Markdown, whose bytes must stay the record and stay compatible with what upstream and a UltraRAG UI read. Putting the history in the file the read already has open means a recall is one `UPDATE` per statement answered, in the transaction the answer was read in: atomic without a temporary file, no background thread, and proportional to the answer rather than to the memory. The file is therefore no longer purely derived — deleting it costs a re-read *and* the counts, which is stated rather than assumed, and the document itself is never in its power to lose. A separate JSON file was the first shape of this and needed a background writer precisely because it was not in the transaction |
| 32 | A column that cannot vary is not kept, and the field is called `kind` | a `kind` column held `statement` for every row and said nothing, because a scope is one document of statements. A column that cannot vary is a claim the schema makes and does not keep. The field that does vary is the statement's own kind, and it is the column now (`kind`), indexed with the statement so a read can be narrowed to one |

| 33 | The record is the table, and `MEMORY.md` is an export of it | a memory is meant to be a thing a person owns, and a SQLite file is a thing a person cannot read, diff, commit, or carry. Upstream's whole design is a Markdown file in a project's repository, and a UltraRAG UI reads the global one from the shared storage tree; a record that is not a file breaks both. The table holds every statement, its kind, its position, its date, and its count, and the file is written from it. What is given up is stated rather than implied: a hand edit to the file does not take and is disclosed, a kind is a column and so is asked for rather than searched for, and a file recovered from an export has the words in the right order with every statement undated and filed as `ITEM` |
| 34 | A statement's kind is a column, not a prefix on its line | `RULE: the draft lives in docs/` is a tagged line, and a file of them reads as data rather than as something a person wrote — which is what a memory is for. The kind is still one run of block letters and still the caller's, and it is still returned with every answer; what changed is that it is held with the statement instead of inside its text, so a read narrows to a category rather than searching for a word that is no longer there. A file written before this is read once on upgrade and its prefixes become kinds |
| 35 | The export holds the statements and nothing else | A file that carried the kinds, the dates, and the counts would be a second record to keep in step, and keeping two in step is how a memory loses a statement. So the file is a rendering: the words, in order. It is regenerated on every write, so it cannot drift; it is not read, so it cannot be wrong; and a memory lost with its database comes back as what was said rather than as how it was said, which is the honest limit of a file that was never a backup |

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
