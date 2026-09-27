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

**3. Global memory is where durable, cross-project rules live.** The user is the unit, not the project: who they are, what they want remembered everywhere, and the standing instructions that apply wherever they work. A rule belongs in a user's standing document, not in a project's, and not in a dated round.

**4. The agent is told which kind to use, and is given four names to choose from.** `get_memory_local` / `set_memory_local` and `get_memory_global` / `set_memory_global`: two verbs, two scopes, and the scope in the name, so the only decision is whether a memory is this project's or this user's. Read global memory at the start of a conversation and project memory when working inside the project, and act on what they say. A statement about the user, or an instruction they gave once, is global; a statement about this project is local.

**4a. The tool names are a recorded difference from upstream.** Upstream's `get_global_memory` and `save_memory` are renamed rather than re-exposed, and the read is renamed too because it no longer reads a whole file. UltraRAG's two tools and this package's four are different surfaces on the same files, and `tests/test_upstream_differential.py` asserts the difference rather than assuming it. The **files** remain the compatibility contract: same names, same bytes, same template, same daily-round format.

**4b. A write records a statement, not an exchange.** `set_*` appends one plain block to the standing document, with no speaker attached: it is what the caller chose to remember, not something the user said and not something an assistant replied. Attributing either would put a claim in a file the user reads that nobody made. The dated files stay the exchange history, written in upstream's format by the browser view, and read by a query.

**4c. A read answers a query and never returns a memory whole.** These memories grow by appending, so returning one whole spends the context window on the file and becomes unusable after a few weeks. A read takes words, returns the standing document bounded (it is the small curated part, and the rules live in it), returns the dated rounds that match — ranked by the longest word they share, then by how many, then newest — and reports what it searched, how many matched, and whether anything was left out. Ranking on the longest shared word is what keeps a question from matching on its commonest word, without a list of words to exclude. An empty query is refused rather than answered with everything.

**5. The extension is behavioural, not a code dependency.** This package depends on no UltraRAG code. UltraRAG's server base class registers a pipeline `build` tool, and its runner wires servers through generated `server.yaml` files and `output=` annotations; both belong to a pipeline deployment. Memory here is a complete solution for a project and an account, served to agents and people, not a stage inside someone else's pipeline. Fidelity is therefore a tested claim (`tests/test_fidelity.py`, and `tests/test_upstream_differential.py` against a real checkout) rather than a property of shared code.

**6. A project file is ordinary Markdown, on purpose.** A project's memory is visible, diffable, and optionally committed by the project's own choice. The server never reads, hashes, indexes, or copies it.

**7. The browser view is the shared UI library behind a thin adapter.** The page reaches memory only through this server: it is given results, never a path, and it must stay unable to read `.memory-rag` or the storage tree. Replacing the standing document is this package's own write, guarded by the digest the page read, so a concurrent agent write is never overwritten.

## Consequences

- The server is an extension of something that exists rather than a competitor to it. A user who already runs UltraRAG's memory keeps their format, their storage root, and their UI, and gains project scoping.
- Upstream's byte format is a contract with a test. A change to it fails `tests/test_fidelity.py` rather than drifting quietly. The tool *names* are not part of that contract and are not upstream's.
- Global memory is small and durable by design, which is what makes it the right place for rules; project memory is larger and project-scoped, which is what makes it the wrong place.
- An agent can now record a rule itself, in the standing document, which the next read of either scope returns. What it still cannot do is remove one, or edit one: the standing document is replaced only through the browser view or by hand.
- Retrieval is bounded and disclosed, so a memory that has grown large is still answered in a fixed number of tokens. It is a word search, not an understanding: a paraphrase is found only when the words are present.
- Depending on no UltraRAG code means a format change upstream is this project's problem to notice. The pinned revision, the captured fixtures, and the differential workflow are the notice.

## What this server deliberately does not do

Recorded so the next reader does not mistake an absence for an oversight, and does not invent a different substrate to fill it:

- **No search by meaning.** A read matches words, not intent: no synonyms, no stemming, no ranking model, and nothing to embed.
- **No paging.** `limit` caps the dated rounds; there is no offset, no cursor, and no date range.
- **Nothing removes or corrects a statement.** Statements are appended; the standing document is replaced only through the browser view or by hand, and a dated round is never edited.
- **No session tier.** Every statement is durable; nothing expires.
- **No typed or relational structure.** Objects, types, and relations between them are not this server's subject; the memory model is upstream's, deliberately.
- **No cross-scope read.** One call reads one scope. Global memory is read by naming it, and a project's memory is never returned inside a global read, because a per-project fact must not travel by accident.
