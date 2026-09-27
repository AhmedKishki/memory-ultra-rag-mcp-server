# ADR 0001: Extend UltraRAG's Memory, Do Not Replace It

**Purpose:** state what this server is an extension of, what the extension adds, and what is deliberately left alone, so the product is not re-decided from prose.

Status: accepted 2026-09-27. It records the intent this server was built to; it introduces no new behaviour.

## Context

UltraRAG ships a memory server (`servers/memory/src/memory.py`, reviewed at `3a709a2aea3fbe46acca59c422621c94b6e86857`, version `0.3.0.2`). It keeps one kind of memory: a standing `MEMORY.md` plus one dated dialogue file per day, per `user_id`, under a storage root, written and read through `get_global_memory` and `save_memory`.

That is a good format and a working server, and it is the base rather than a starting point. Two things it does not do, both of which an agent working across repositories needs:

- **Nothing is project-scoped.** Every `user_id` is one flat memory, so a project inherits every other project's material and carries none of its own out of the repository.
- **There is nowhere for a rule to live.** A standing instruction the user gave once — "always cite the commit", "use British English" — is written as a round in a dated file, where nothing reads it and nothing gives it standing.

The UltraRAG UI already reads the `memory/` directory of its own storage tree. Whatever a project-scoped memory looks like, the shared one has to stay there, or the UI stops being able to show it.

## Decision

**1. UltraRAG's memory is the base, and the files stay byte-compatible.** The file names and the byte format are upstream's, taken from the pinned revision and checked against files that revision produced. The project does not introduce a memory file of its own and does not migrate a store. The *tools* are deliberately not upstream's: see decision 4a.

**2. The extension is a second, project-scoped memory.** A project's memory lives inside that repository, under `.memory-rag`, in upstream's format. One server instance serves one project and binds it at startup, so a project's memory is private to it and travels with it.

**3. Global memory is where durable, cross-project rules live.** The account is the unit, not the project: who the user is, what they want remembered everywhere, and the standing instructions that apply wherever they work. A rule belongs in the global standing document, not in a project's, and not in a dated round.

**3a. There is no user dimension on the global tools.** Upstream keys its memory by `user_id`; this server does not, and neither the tools nor the page takes one. A project is identified by the repository the server is bound to, so a user identifier would be a second identity for something already identified, and an agent would have to decide which user it is before it could remember anything. The global memory is one per account, and it lives in the directory upstream uses for the user it is given when none is named — `memory/default/` — so the layout stays upstream's, an existing store keeps working, and a UltraRAG UI still reads it. The tools are four; the page shows two blocks, this project and global.

**4. The agent is told which kind to use, and is given four names to choose from.** `get_memory_local` / `set_memory_local` and `get_memory_global` / `set_memory_global`: two verbs, two scopes, and the scope in the name, so the only decision is whether a memory is this project's or this user's. Read global memory at the start of a conversation and project memory when working inside the project, and act on what they say. A statement about the user, or an instruction they gave once, is global; a statement about this project is local.

**4a. The tool names are a recorded difference from upstream.** Upstream's `get_global_memory` and `save_memory` are renamed rather than re-exposed, and the read is renamed too because it no longer reads a whole file. UltraRAG's two tools and this package's four are different surfaces on the same files, and `tests/test_upstream_differential.py` asserts the difference rather than assuming it. The **files** remain the compatibility contract: same names, same bytes, same template, same daily-round format.

**4b. A write records a statement, not an exchange.** `set_*` appends one plain block to the standing document, with no speaker attached: it is what the caller chose to remember, not something the user said and not something an assistant replied. Attributing either would put a claim in a file the user reads that nobody made. The dated files stay the exchange history, written in upstream's format by the browser view, and read by a query.

**4c. A read answers a query and returns only what matched.** These memories grow by appending, so returning one whole spends the context window on the file and becomes unusable after a few weeks. A read returns the units that matched — a statement, or a dated round — each naming the file and stamp it came from, plus how it matched and whether anything was left out. No file, no document, no whole memory is ever returned, and there is no way to ask for one.

**4d. The search runs over an index, and the index is derived.** Matching is SQLite FTS5 over a `index.sqlite3` built from the scope's own files and kept beside them, so a read is a lookup and a page of rows rather than a pass over every dated file, and its cost does not grow with the memory. Three properties follow from the Markdown staying the record: nothing in the write path updates the index, because a read compares each file against what was indexed and re-reads what changed; a hand edit, or one made by a UltraRAG UI, is read correctly; and deleting the index costs one rebuild. FTS5 is a compile-time option of SQLite, so the server probes for it and refuses to start without it, rather than serve reads that grow with the memory.

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
- Serving one global memory per account rather than one per user means two people sharing an account and a storage root share this memory too. That is the same bargain upstream's default user already makes, and the differential test records that upstream still keys and still validates by user while these tools do neither.

## What this server deliberately does not do

Recorded so the next reader does not mistake an absence for an oversight, and does not invent a different substrate to fill it:

- **No search by meaning.** A read matches words, not intent: no synonyms, no stemming, no embeddings, and no model of any kind.
- **No way to read a memory whole.** There is no tool, flag, or query that returns a file, and no limit that can be raised to reach one. What is relevant comes back; the rest does not.
- **No paging.** `limit` caps the units; there is no offset, no cursor, and no date range.
- **Nothing removes or corrects a statement.** Statements are appended; the standing document is replaced only through the browser view or by hand, and a dated round is never edited.
- **No session tier.** Every statement is durable; nothing expires.
- **No typed or relational structure.** Objects, types, and relations between them are not this server's subject; the memory model is upstream's, deliberately.
- **No cross-scope read.** One call reads one scope. Global memory is read by naming it, and a project's memory is never returned inside a global read, because a per-project fact must not travel by accident.
