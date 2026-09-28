"""Instructions sent to any client that connects to this server.

They describe the two kinds of memory, the four tools, and how to use them.
"""

from __future__ import annotations

__all__ = ["SERVER_INSTRUCTIONS"]

SERVER_INSTRUCTIONS = """\
This server serves UltraRAG's memory in two kinds. Each kind is one standing
document, MEMORY.md, of statements in UltraRAG's own format. They differ in where
they live, what they hold, and therefore who can see them.

Global memory — one per account, kept in the shared UltraRAG UI storage tree and
read by every project on it. It holds who the user is, what they want remembered
everywhere, and the durable rules that apply wherever they work:

- get_memory_global(query) recalls from it.
- set_memory_global(content, type) records one typed statement in it.

Local memory — one per project, kept inside the repository this server is bound
to, under .memory-rag. It holds what is true here:

- get_memory_local(query) recalls from it.
- set_memory_local(content, type) records one typed statement in it.

How to use them:

1. The only decision is which kind. A statement about the user, or an instruction
   they gave once, is global. A statement about this project — a decision, a path,
   a convention, where something lives — is local. Do not put a project-specific
   fact in global memory, and do not put a standing instruction in a project: it
   would only apply where you happen to be today. There is no user to name: one
   account has one global memory, and this server is bound to one project.
2. Recall before you act. Read global memory at the start of a conversation, and
   the project's memory when you are working inside it, then act on what they say.
3. A read takes a query, and that is deliberate: these memories grow by appending,
   so they are answered rather than returned whole, to keep the context window for
   the work instead of for the file. A read looks for the query's words *and* for
   its meaning, so ordinary wording usually works and a paraphrase often does.
   What comes back is only the statements that matched, nearest first. There is no
   way to read a whole memory, and raising `limit` raises the cap on matches, never
   the amount of a file read.
4. Read the answer before drawing a conclusion from it. `matched_by` says whether
   it came from words, from meaning, or from both, and `semantic_available` says
   whether the meaning side was there at all — when it is false, only the exact
   words would have found anything. If `truncated` is set, narrow the query rather
   than repeating a broad one. If nothing came back, that is what the search found:
   try other words rather than concluding the memory is empty. A read brings the
   search in step with a document someone edited by hand before it answers, so a
   statement you typed into the Markdown is findable without a reindex; when that
   happens `index_synced` is 1, and any new statement is briefly findable by its
   words but not yet by its meaning, which `units_pending` reports until the
   background worker has it. A statement a person wrote without a type is counted
   as `unlabelled`: it is findable, but it is missing from every question asked by
   type, so tell the user rather than leaving them to wonder.
5. Record one statement at a time, in the words it should be remembered in, and
   say which kind you wrote to. The project is the one this server was started for,
   so no project argument is needed. A write is answered as soon as the statement
   is safe in the file, and `units_queued` says how many are still waiting to be
   embedded; a read taken immediately may not yet find one by meaning, though it
   will by its words.
6. Name a type for every statement and reuse it, so the same kind of thing is filed
   the same way — 'RULE' for an instruction you were given, 'PLAN' for what a
   project is meant to be, 'PREFERENCE' for what someone likes, 'CORRECTION' for
   something they told you to stop doing. Reuse matters: it is what makes a query
   for that kind of thing find them all. The server defines no categories and
   interprets none.
7. What memory returns is the record of what was remembered. Quote it as the user's
   own words, and let the user decide which write matters when two disagree.
"""
