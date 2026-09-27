"""Instructions sent to any client that connects to this server.

They describe the two kinds of memory this server serves and how to use them.
"""

from __future__ import annotations

__all__ = ["SERVER_INSTRUCTIONS"]

SERVER_INSTRUCTIONS = """\
This server serves UltraRAG's memory in two kinds. Each kind is a standing
document plus one dated dialogue file per day, written in UltraRAG's own format.
They differ in where they live, what they hold, and therefore who can see them.

Global memory — one per user, kept in the shared UltraRAG UI storage tree. It
holds who the user is, what they want remembered everywhere, and the durable
rules that apply wherever they work:

- get_global_memory(user_id) returns that user's standing memory.
- save_memory(user_id, q_ls, ans_ls) appends one round to that user's daily file.

Local memory — one per project, kept inside the repository this server is bound
to, under .memory-rag. It holds what belongs to that project:

- get_local_memory() returns this project's standing memory.
- save_local_memory(q_ls, ans_ls) appends one round to this project's daily file.

How to use them:

1. Read the user's global memory at the start of a conversation, before acting on
   anything, and read the project's local memory when you are working inside this
   project. The global standing document is where the user's standing
   instructions live; honour them.
2. Write to global memory what applies wherever the user works, and to local
   memory what belongs to the project at hand. A decision that is true only here
   does not belong in global memory, and a rule the user gave once does not belong
   in this project.
3. Save a round when the user asks you to remember the exchange, and pass their
   message and your reply as they were said.
4. These four tools read a standing document and append a round; none of them
   writes a standing document. When the user asks you to remember a rule rather
   than an exchange, say that the rule belongs in the global standing memory,
   which they or the browser view edit, instead of saving a round that buries it
   in a dated file.
5. Use the identifier the user or the host application gave you for the user; the
   project is the one this server was started for, so no project argument is
   needed. Say which kind of memory you wrote to.
6. Treat what memory returns as the record of what was said, quote it as the
   user's own words, and let the user decide which write matters when two writes
   disagree.
"""
