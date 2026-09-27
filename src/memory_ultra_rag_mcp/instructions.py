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
- set_memory_global(content) records one statement in it.

Local memory — one per project, kept inside the repository this server is bound
to, under .memory-rag. It holds what is true here:

- get_memory_local(query) recalls from it.
- set_memory_local(content) records one statement in it.

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
   the work instead of for the file. Ask for what you need in words. What comes
   back is only the parts that matched — a statement, or an exchange — each with
   the file and date it came from. There is no way to read a whole memory, and
   asking for more than `limit` will not produce one.
4. Read the answer before drawing a conclusion from it. `matched_by` says whether
   every word of your query was found (`all-words`) or whether the search fell
   back to the words that occur least (`rarest-words`), which is broader and may
   include something merely adjacent. If `truncated` is set, narrow the query
   rather than repeating a broad one. If nothing came back, that is what the
   search found; try other words rather than concluding the memory is empty.
5. Record one statement at a time, in the words it should be remembered in, and
   say which kind you wrote to. The project is the one this server was started
   for, so no project argument is needed.
6. What memory returns is the record of what was remembered, quote it as the user's
   own words, and let the user decide which write matters when two disagree.
"""
