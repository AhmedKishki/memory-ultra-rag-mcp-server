"""Instructions sent to any client that connects to this server.

They describe the interface: two memories, seven tools, the rules that hold, and
what an answer contains. A client that reads nothing else should still know how to
use every tool, and the text is written to be read by a person as much as by a
model — no coaching, no encouragement, and no instructions that are not about how
this server behaves.
"""

from __future__ import annotations

__all__ = ["SERVER_INSTRUCTIONS"]

SERVER_INSTRUCTIONS = """\
This server keeps two memories and gives you seven tools over them.

- The LOCAL memory belongs to this project. It lives in the repository, under
  .memory-rag, and travels with the project. A decision, a path, or a convention
  that is only true here goes in it.
- The GLOBAL memory belongs to the account. It is shared by every project on this
  machine. What is true of the user, and an instruction they gave once and mean
  everywhere, goes in it.

The server is bound to one project when it starts, so no tool takes a project
argument, and the account's memory has no user identifier.

THE TOOLS

set_memory_local(content, kind)    records one statement in this project
set_memory_global(content, kind)   records one statement for the account
get_memory_local(query, kind)      returns this project's matching statements
get_memory_global(query, kind)     returns the account's matching statements
forget_memory_local(text)          removes one statement from this project
forget_memory_global(text)         removes one statement from the account
set_memory_handoff(content)        records a handoff, replacing the previous one

A read returns the statements that matched, not an answer to a question: it
reports them as they were recorded, and the answer is written from them. The kind
argument on a read restricts the answer to one category.

RULES

- One statement per call, in the words it should be remembered in. A statement is
  a line or two. Something longer is several calls.
- Every statement carries a kind, and the caller chooses it. The usual ones are
  RULE for an instruction or a standing fact, PLAN for what the project is meant to
  be or achieve, PREFERENCE for what the user likes, CORRECTION for something to
  stop doing. Any word in block letters is accepted and nothing is interpreted
  here, so a kind of the caller's own is fine; a statement that fits none of them
  can be filed as ITEM. The same word should be used for the same kind of thing,
  because a read filtered by kind returns everything filed under it.
- Read the memories before acting on anything decided earlier, and record
  decisions and corrections as they happen. A query of a few words is enough. If
  it returns nothing, the words were wrong rather than the memory: other words,
  or a read filtered by kind, is the next thing to try.
- A session another session will continue is ended with set_memory_handoff: what
  is done, what is in flight, what to do first. It replaces the previous handoff,
  so a project holds one at a time.
- forget takes the exact text a read returned. It matches words and never
  meaning, so it removes that statement or nothing; if the text matches more than
  one statement, nothing is removed and the answer names them.
- A fact about one project does not belong in the account's memory, and an
  instruction that applies to one project is not an instruction everywhere.

AN ANSWER CONTAINS

kind, text, stamp, added_at, recalls, last_recalled_at
    The statement and its own record. text is the statement without its kind.
    stamp is its place in the document, zero being the most recent.
matched_by, reranked
    Whether it was found by its words, by its meaning, or by both, and whether a
    cross-encoder reordered the answer.
semantic_available
    False when only the words were searched, so only the exact words would have
    found anything.
truncated, returned
    Set when the answer is the top of a longer match, and how many statements came
    back.
units_pending
    Statements findable by their words whose vector is not ready yet.
document_rewritten
    The document did not match the memory and was written out from it again. A
    statement changes through set_memory_* and forget_memory_* only, so an edit to
    the document by hand does not change the memory.
hint
    Present only when nothing matched, and says what to try instead.
"""
