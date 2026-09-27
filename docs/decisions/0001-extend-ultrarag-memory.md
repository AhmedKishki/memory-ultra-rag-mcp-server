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

**1. UltraRAG's memory is the base, and it stays byte-compatible.** The tool names, file names, byte format, and read payloads are upstream's, taken from the pinned revision and checked against files that revision produced. The project does not introduce a memory format of its own, and it does not migrate a store.

**2. The extension is a second, project-scoped memory.** A project's memory lives inside that repository, under `.memory-rag`, in upstream's format. One server instance serves one project and binds it at startup, so a project's memory is private to it and travels with it.

**3. Global memory is where durable, cross-project rules live.** The user is the unit, not the project: who they are, what they want remembered everywhere, and the standing instructions that apply wherever they work. A rule belongs in a user's standing document, not in a project's, and not in a dated round.

**4. The agent is told which kind to use, and told the truth about what it can write.** Read global memory at the start of a conversation and project memory when working inside the project. Write to global memory what applies everywhere, and to project memory what belongs to the project. The four MCP tools read a standing document and append a round; **they cannot write a standing document**, so an agent asked to remember a rule says where the rule belongs rather than burying it in a dated file.

**5. The extension is behavioural, not a code dependency.** This package depends on no UltraRAG code. UltraRAG's server base class registers a pipeline `build` tool, and its runner wires servers through generated `server.yaml` files and `output=` annotations; both belong to a pipeline deployment. Memory here is a complete solution for a project and an account, served to agents and people, not a stage inside someone else's pipeline. Fidelity is therefore a tested claim (`tests/test_fidelity.py`, and `tests/test_upstream_differential.py` against a real checkout) rather than a property of shared code.

**6. A project file is ordinary Markdown, on purpose.** A project's memory is visible, diffable, and optionally committed by the project's own choice. The server never reads, hashes, indexes, or copies it.

**7. The browser view is the shared UI library behind a thin adapter.** The page reaches memory only through this server: it is given results, never a path, and it must stay unable to read `.memory-rag` or the storage tree. Replacing the standing document is this package's own write, guarded by the digest the page read, so a concurrent agent write is never overwritten.

## Consequences

- The server is an extension of something that exists rather than a competitor to it. A user who already runs UltraRAG's memory keeps their format, their storage root, and their UI, and gains project scoping.
- Upstream's byte format is a contract with a test. A change to it fails `tests/test_fidelity.py` rather than drifting quietly.
- Global memory is small and durable by design, which is what makes it the right place for rules; project memory is larger and project-scoped, which is what makes it the wrong place.
- An agent cannot create a rule through the four tools. Until a tool can write a standing document, that step is the user's or the browser view's, and the instructions say so.
- Depending on no UltraRAG code means a format change upstream is this project's problem to notice. The pinned revision and the differential test are the notice.

## What this server deliberately does not do

Recorded so the next reader does not mistake an absence for an oversight, and does not invent a different substrate to fill it:

- **No search.** `get_local_memory` and `get_global_memory` return a whole standing document. Nothing finds a specific statement inside it.
- **No partial reads.** There is no limit, offset, or date range; a read is the whole document.
- **No editing or removing a round.** A round is appended and never corrected or withdrawn through a tool. The standing document can be replaced only through the browser view or by hand.
- **No way to write a standing document from MCP.** See decision 4.
- **No session tier.** Every round is durable; nothing expires.
- **No typed or relational structure.** Objects, types, and relations between them are not this server's subject; the memory model is upstream's, deliberately.
