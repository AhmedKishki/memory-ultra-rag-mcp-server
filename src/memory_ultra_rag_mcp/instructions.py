"""Instructions sent to any client that connects to this server.

They are written to match what the server actually does: the memory is
UltraRAG's own, the tools are upstream's, and nothing here searches, ranks,
summarizes, or corrects memory.
"""

from __future__ import annotations

__all__ = ["SERVER_INSTRUCTIONS"]

SERVER_INSTRUCTIONS = """\
This server exposes UltraRAG's own memory: one global MEMORY.md per user and one
dated dialogue file per day, unmodified from the pinned UltraRAG checkout it was
started from.

Two tools are available.

- get_global_memory(user_id) returns the whole global MEMORY.md for that user.
  It is the user's standing profile: identity, preferences, and anything else
  they asked to be remembered globally. Note that upstream creates the file with
  a template on first read, so the first call for a new user also creates it.
- save_memory(user_id, q_ls, ans_ls) appends one user/assistant round, with a
  timestamp, to that day's project file. It appends; it never edits or removes.

How to behave:

1. Read the global memory before answering anything that depends on the user's
   standing preferences, and treat what it says as the user's own words.
2. Save a round when the user asks you to remember the exchange, and pass their
   question and your answer as they were, not as a summary.
3. Treat memory as a record, never as evidence: it is not a source, and it is
   never safe to quote as one.
4. The stored text can be stale or wrong, and nothing here reconciles it. If a
   statement contradicts the user, say so and let them decide; this server has
   no way to correct, retract, or deduplicate what was written.
5. user_id is a scope, not a secret. Use the identifier the user or the host
   application gave you, and do not invent one to separate two conversations
   unless the user asked for that.
"""
