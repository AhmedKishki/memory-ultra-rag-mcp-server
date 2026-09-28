"""Instructions sent to any client that connects to this server.

They describe the two kinds of memory, the four tools, and how to use them.
"""

from __future__ import annotations

__all__ = ["SERVER_INSTRUCTIONS"]

SERVER_INSTRUCTIONS = """\
This server serves UltraRAG's memory in two kinds. Each kind is a standing
document plus one dated dialogue file per day, written in UltraRAG's own format.
They differ in where they live, what they hold, and therefore who can see them.

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
   they gave once, is global. A statement about this project — a decision, a
   path, a convention, where something lives — is local. Do not put a
   project-specific fact in global memory, and do not put a standing instruction
   in a project: it would only apply where you happen to be today. There is no
   user to name: one account has one global memory, and this server is bound to
   one project.
2. Recall before you act. Read global memory at the start of a conversation, and
   the project's memory when you are working inside it, then act on what they say.
3. A read takes a query, and that is deliberate: these memories grow by appending,
   so they are answered rather than returned whole, to keep the context window for
   the work instead of for the file. A read looks for the query's words *and* for
   its meaning, so ordinary wording usually works and a paraphrase often does.
   What comes back is only what matched — a statement, or an exchange — each with
   the file and date it came from. There is no way to read a whole memory, and
   raising `limit` raises the cap on matches, never the amount of a file read.
4. Read the answer before drawing a conclusion from it. `matched_by` says whether
   the answer came from words, from meaning, or from both, and `semantic_available`
   says whether the meaning side was there at all — when it is false, only the
   exact words would have found anything. If `truncated` is set, narrow the query
   rather than repeating a broad one. If nothing came back, that is what the search
   found; try other words, or the topic in other words, rather than concluding the
   memory is empty. If `index_files_behind` is more than zero, a file was edited
   outside these tools; a `memory-ultra-rag-reindex` run brings the search back in
   step with it.
5. Record one statement at a time, in the words it should be remembered in, and
   say which kind you wrote to. The project is the one this server was started
   for, so no project argument is needed. A write is answered as soon as the
   statement is safe in the file, and `units_queued` says how many statements are
   still waiting to be embedded; a read taken immediately may not yet find a
   statement by meaning, though it will by its words.
6. Every statement is filed under a type you name. Pick the category yourself and
   reuse it, so the same kind of thing is filed the same way — 'rule' for an
   instruction you were given, 'plan' for what a project is meant to be, 'preference'
   for what someone likes, 'correction' for something they told you to stop doing.
   The server defines no categories and interprets none: the type is recorded with
   the statement, is searchable, and is not repeated when the statement is recalled,
   so a read answers with the statement itself. Reusing a type you already used
   matters, because it is what makes a query for that kind of thing find them.
7. What memory returns is the record of what was remembered, quote it as the user's
   own words, and let the user decide which write matters when two disagree.
"""
