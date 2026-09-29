# ADR 0001: Extend UltraRAG's Memory, Do Not Replace It

**Purpose:** state what this server is an extension of, what the extension adds, and what is deliberately left alone, so the product is not re-decided from prose.

Status: accepted 2026-09-27. It records the intent this server was built to; it introduces no new behaviour.

**Amended twice on 2026-09-28.** The dated dialogue log is no longer written or
read, a statement carries a type, and the record moved into the table:
`memory.sqlite3` holds the statements, their types, their places, their dates and
their counts, and `MEMORY.md` is an export of it — written from the record, never
read from it. Places below that describe the log as part of this server's shape,
or the document as the thing that is true, are superseded: what they say about
*upstream* is still true, and what they say about *this* server is not. The
decisions that reasoned from them are kept where the reasoning still holds.

What the second amendment costs, so it is not discovered later: a hand edit to
`MEMORY.md` does not change the memory, and the read that overwrites it says so
(`document_rewritten`); a kind is a column and so is asked for with the read's
`kind` argument rather than searched for as a word; and a memory recovered from an
export has the right statements in the right order with every one of them undated
and filed as `ITEM`. The README's choice table (33, 34, 35) records each of
these, and `tests/test_metadata.py` and `tests/test_index.py` pin them.

## Context

UltraRAG ships a memory server (`servers/memory/src/memory.py`, reviewed at `3a709a2aea3fbe46acca59c422621c94b6e86857`, version `0.3.0.2`). It keeps one kind of memory: a standing `MEMORY.md` plus one dated dialogue file per day, per `user_id`, under a storage root, written and read through `get_global_memory` and `save_memory`.

That is a good format and a working server, and it is the base rather than a starting point. Two things it does not do, both of which an agent working across repositories needs:

- **Nothing is project-scoped.** Every `user_id` is one flat memory, so a project inherits every other project's material and carries none of its own out of the repository.
- **There is nowhere for a rule to live.** A standing instruction the user gave once — "always cite the commit", "use British English" — is written as an exchange in a dated file, where nothing reads it and nothing gives it standing.

The UltraRAG UI already reads the `memory/` directory of its own storage tree. Whatever a project-scoped memory looks like, the shared one has to stay there, or the UI stops being able to show it.

## Decision

**1. UltraRAG's memory is the base, and the files stay byte-compatible.** The file names and the byte format are upstream's, taken from the pinned revision and checked against files that revision produced. The project does not introduce a memory file of its own and does not migrate a store. The *tools* are deliberately not upstream's: see decision 4a.

**2. The extension is a second, project-scoped memory.** A project's memory lives inside that repository, under `.memory-rag`, in upstream's format. One server instance serves one project and binds it at startup, so a project's memory is private to it and travels with it.

**3. Global memory is where durable, cross-project rules live.** The account is the unit, not the project: who the user is, what they want remembered everywhere, and the standing instructions that apply wherever they work. A rule belongs in the global standing document, not in a project's.

**3a. There is no user dimension on the global tools.** Upstream keys its memory by `user_id`; this server does not, and neither the tools nor the page takes one. A project is identified by the repository the server is bound to, so a user identifier would be a second identity for something already identified, and an agent would have to decide which user it is before it could remember anything. The global memory is one per account, and it lives in the directory upstream uses for the user it is given when none is named — `memory/default/` — so the layout stays upstream's, an existing store keeps working, and a UltraRAG UI still reads it. The tools are four; the page shows two blocks, this project and global.

**4. The agent is told which kind to use, and is given four names to choose from.** `get_memory_local` / `set_memory_local` and `get_memory_global` / `set_memory_global`: two verbs, two scopes, and the scope in the name, so the only decision is whether a memory is this project's or this user's. Read global memory at the start of a conversation and project memory when working inside the project, and act on what they say. A statement about the user, or an instruction they gave once, is global; a statement about this project is local.

**4a. The tool names are a recorded difference from upstream.** Upstream's `get_global_memory` and `save_memory` are renamed rather than re-exposed, and the read is renamed too because it no longer reads a whole file. UltraRAG's two tools and this package's four are different surfaces on the same files, and `tests/test_upstream_differential.py` asserts the difference rather than assuming it. The **files** remain the compatibility contract: same names, same bytes, same template.

**4b. A write records a statement, not an exchange.** `set_*` appends one plain block to the standing document, with no speaker attached: it is what the caller chose to remember, not something the user said and not something an assistant replied. Attributing either would put a claim in a file the user reads that nobody made. The dated log is not kept: anything worth keeping is recorded as a statement, and a date does not change what a statement is.

**4b-bis. A statement is filed under a type the caller names.** One run of block letters, written as the statement's own first words and a colon, indexed and embedded with the statement and stripped from every answer. The server defines no categories and interprets none. A memory is a list of unrelated statements, and a category is what makes a query for "which of these are corrections" answerable at all.

**4c. A read answers a query and returns only what matched.** These memories grow by appending, so returning one whole spends the context window on the file and becomes unusable after a few weeks. A read returns the statements that matched, each naming the document it came from, plus how it matched and whether anything was left out. No file, no document, no whole memory is ever returned, and there is no way to ask for one.

**4d. A read works in words and in meaning, from local models.** Words come from an FTS5 index over the scope's own files; meaning comes from a pinned, CPU-only sentence embedder run in this process, its vectors held in a second derived file beside the index. The two sides are fused by weighted reciprocal rank fusion and a cosine floor, so a unit can arrive by words, by meaning, or by both, and the answer says which. Three properties make this safe to add without moving the record: the vectors are **derived**, so deleting them costs a re-embedding and nothing else; the model is chosen by a **documented table** with one default, so a stronger or multilingual model is one line; and both sides are written against a **seam** (`Embedder`, `Reranker`) that no store or index imports, so a hosted backend later would be additive rather than a rewrite. The store and the indexes import no model library at all.

**4e. Recording is asynchronous; a lookup is not.** A `set_*` appends the statement to the Markdown — durable before anything else happens — and hands the new unit to a background worker, reporting what it queued. A write never fails because the lookup layer is missing, and its work is proportional to one small file, never to the memory. A read may do only O(1) work: embed the query once, scan the vectors once, query the word index once, fuse, return. It does not embed units, walk the scope, or load a model; the model is warmed on the serving side, and a read that finds it unloaded says the semantic side was unavailable rather than stalling a caller.

**4f. A read keeps the index in step; it is the write side that embeds.** A search answers what the index holds, so keeping it current used to happen only where the files are written — the tools, the page, and `memory-ultra-rag-reindex` — and a read reported `index_files_behind` rather than sweep. That was a deliberate trade against a measurement from the collection's other server: ~0.18 ms per source across a few thousand *dated files*, hundreds of milliseconds in total, plus a 4 KB head read per file here. **Reversed 2026-09-28.** This server keeps one document per scope — there are no dated files, and none since the round log was dropped — so the per-source sweep that justified the trade does not exist here. A read now fingerprints the document and re-reads it when it changed, measured at 17 µs unchanged and ~2 ms changed against a read of hundreds of milliseconds, and a statement typed, reworded, or deleted by hand is reflected in the very next answer. The vectors a hand edit still owes stay with the write side's async worker and are disclosed as `units_pending`, and `memory-ultra-rag-reindex` is what settles them or forces a full pass. What this section still guards: a read must not embed a unit, must not walk a scope, and must never return a file.

**4d. The search runs over a table, and the table is the record.** Matching is SQLite FTS5 over a `memory.sqlite3` built from the scope's own files and kept beside them — which since 2026-09-28 also holds a statement's history, so "derived" now covers the word index inside that file and not the file as a whole, so a read is a lookup and a page of rows rather than a pass over every dated file, and its cost does not grow with the memory. Three properties follow from the Markdown staying the record: nothing in the write path updates the index, because a read compares each file against what was indexed and re-reads what changed; a hand edit, or one made by a UltraRAG UI, is read correctly; and the word index is rebuilt whenever the document changes. Since 2026-09-28 the same file also holds the vectors and each statement's history, so deleting the file costs a rebuild, a re-embedding, and the history, which no rebuild can produce. FTS5 is a compile-time option of SQLite, so the server probes for it and refuses to start without it, rather than serve reads that grow with the memory.

**4e. The query is words, and how it was interpreted is reported.** The query's words are required to be present together first, because an index answers that without visiting the rest of the memory and because it is what a question like "where does ref000137 keep the draft" means. When nothing holds every word, the query falls back to the words that occur in the fewest units, since an over-strict conjunction returning nothing is worse than a broader one. The answer says which of the two happened, so a caller is never left guessing whether an empty result means absent or merely unmatched.

**5. The extension is behavioural, not a code dependency.** This package depends on no UltraRAG code. UltraRAG's server base class registers a pipeline `build` tool, and its runner wires servers through generated `server.yaml` files and `output=` annotations; both belong to a pipeline deployment. Memory here is a complete solution for a project and an account, served to agents and people, not a stage inside someone else's pipeline. Fidelity is therefore a tested claim (`tests/test_fidelity.py`, and `tests/test_upstream_differential.py` against a real checkout) rather than a property of shared code.

**6. A project file is ordinary Markdown, on purpose.** A project's memory is visible, diffable, and optionally committed by the project's own choice. The server never reads, hashes, indexes, or copies it.

**7. The browser view is the shared UI library behind a thin adapter.** The page reaches memory only through this server: it is given results, never a path, and it must stay unable to read `.memory-rag` or the storage tree. Replacing the standing document is this package's own write, guarded by the digest the page read, so a concurrent agent write is never overwritten.

## Consequences

- The server is an extension of something that exists rather than a competitor to it. A user who already runs UltraRAG's memory keeps their format, their storage root, and their UI, and gains project scoping.
- Upstream's byte format is a contract with a test. A change to it fails `tests/test_fidelity.py` rather than drifting quietly. The tool *names* are not part of that contract and are not upstream's.
- Global memory is small and durable by design, which is what makes it the right place for rules; project memory is larger and project-scoped, which is what makes it the wrong place.
- An agent can now record a rule itself, in the standing document, which the next read of either scope returns. What it still cannot do is remove one, or edit one: the standing document is replaced only through the browser view or by hand.
- Retrieval is bounded, selective, and independent of the memory's size, so a memory that has grown large is still answered in a fixed number of tokens and a fixed number of lookups. It is a word search, not an understanding: a paraphrase is found only when the words are present, and a synonym is not.
- The index is disposable and the files are not. A memory can be edited, committed, or read by a UltraRAG UI exactly as before this package existed, and the index is rebuilt from those files whenever they change.
- Depending on no UltraRAG code means a format change upstream is this project's problem to notice. The pinned revision, the captured fixtures, and the differential workflow are the notice.
- Depending on FTS5 means a platform requirement, and it is the one requirement this package adds that upstream's memory does not have. It is probed once, loudly, and recorded in `scripts/check_sqlite_fts5.py`.
- **The semantic side is unmeasured.** Nothing in this package claims a quality gain from it, because nothing here has measured one. The later phase builds a judged set and decides which model, whether a cheap local reranker earns its place on a blocking read, and whether the semantic side beats words at all on this data. Until then the honest statement is that it makes a lookup less wording-dependent and better ordered, and the collection's other measurements suggest it improves ordering more than coverage.
- **The install now fetches a model.** 65 MB, once, from the model host, into this package's own cache. No API is used at any point, and a machine that cannot fetch it still starts, records statements, and answers in words — saying so in every read.
- Serving one global memory per account rather than one per user means two people sharing an account and a storage root share this memory too. That is the same bargain upstream's default user already makes, and the differential test records that upstream still keys and still validates by user while these tools do neither.

## What this server deliberately does not do

Recorded so the next reader does not mistake an absence for an oversight, and does not invent a different substrate to fill it:

- **No search by meaning, beyond the dense side.** A read matches words and, through a pinned local embedder, meaning. It does not paraphrase, stem, or expand a query: a statement is found because its meaning is near the query's, not because a synonym list knows them.
- **No way to read a memory whole.** There is no tool, flag, or query that returns a file, and no limit that can be raised to reach one. What is relevant comes back; the rest does not.
- **No paging.** `limit` caps the units; there is no offset, no cursor, and no date range.
- **No cross-scope vector search.** Each scope's vectors are its own; nothing searches two of them together, because a project's memory must not travel by accident.
- **Nothing removes or corrects a statement.** Statements are appended; the standing document is replaced only through the browser view or by hand.
- **No session tier.** Every statement is durable; nothing expires.
- **No relational structure.** Objects and relations between them are not this server's subject; the memory model is upstream's, deliberately. A type on a statement is a free label for retrieval, not a schema.
- **No cross-scope read.** One call reads one scope. Global memory is read by naming it, and a project's memory is never returned inside a global read, because a per-project fact must not travel by accident.
