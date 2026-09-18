# Agent Guide — Memory UltraRAG MCP

This guide defines how an AI agent must use the planned Memory UltraRAG MCP
server. The server is not implemented yet; the tool names below are the target
contract and must not be presented as currently available.

## Purpose

Use this server to preserve operational continuity across agent sessions:

- active rules;
- project status and material progress;
- preferences, style, tone, and formatting;
- decisions and their rationale;
- practical, theoretical, and meta-theoretical commitments;
- constraints and procedures;
- useful outcomes; and
- unresolved questions.

Do not use it as a document knowledge base, conversation transcript, scratchpad,
or quotation store. Put source documents and literature evidence in the
appropriate document RAG. A memory may point to evidence, but is not itself
evidence.

## Authority

The roles are:

1. The agent decides whether context contains a durable memory worth proposing.
2. The server enforces structure, isolation, lifecycle, duplicate checks, and
   conflict workflow.
3. The user is the final authority over ambiguous conflicts, destructive
   changes, and global rules.

Never claim that the server independently understood or verified a proposition.
Similarity retrieves candidates; it does not establish truth, equivalence,
causality, or trustworthiness.

## Start of a session

1. Call `list_projects` if the registered name is unknown.
2. Call `initialize_project` with one exact project name. Never invent a path.
3. Confirm the project returned by the server when there is any ambiguity.
4. Call `recall_memory` with the task or decision you are about to handle.
5. Read every returned `applicable_rule` before acting. If rules are paginated or
   marked incomplete, load the continuation first.
6. Use relevant memories as context and cite their stable memory IDs when they
   materially affect the response or action.

Project recall is the default. Request global recall only when cross-project
rules or preferences are relevant to the user's request. Never search other
projects to approximate global memory.

## Rules

Rules are operative state, returned separately from relevance-ranked memories.

- Follow an applicable project rule over a global rule only when the server
  reports an explicit override.
- Do not ignore a rule because its embedding or lexical score is low.
- Do not treat a proposed or disputed rule as active.
- Do not assume an old rule stopped applying merely because a replacement was
  proposed. It remains operative until the server reports an atomic committed
  replacement or retraction.
- If two active rules appear incompatible without a declared override, stop and
  use the conflict workflow.

## Decide whether to remember

Propose memory only when it is likely to matter in a later session. Good
candidates include:

- a durable instruction or preference;
- a meaningful change in project status;
- completed progress that prevents duplicated work;
- a consequential decision and its reason;
- a commitment or constraint shaping later choices;
- a procedure whose success or failure should guide future work; or
- an open question that must be revisited.

Usually do not propose:

- greetings, acknowledgements, or conversational filler;
- an entire user or assistant message;
- generated prose, transient plans, routine command output, or verbose logs;
- repetitions with no new provenance or qualification;
- credentials, secrets, or sensitive values;
- source excerpts, citations, article summaries, or general factual knowledge;
  or
- unsupported agent speculation presented as fact.

An explicit “remember this” request is intentional, but still atomize it, apply
the correct scope, and run duplicate/conflict checks. It is valid to decide that
ordinary context contains nothing durable and make no memory call.

## Propose and commit

1. Convert durable context into the smallest independently revisable records.
2. Give each proposal a type, scope, clear statement, conditions, uncertainty,
   and available provenance.
3. Use project scope unless the user clearly intends a cross-project record.
4. Call `propose_memory` and inspect every returned comparison candidate.
5. Classify the proposal semantically as:
   - `new`;
   - `equivalent`;
   - `refinement`; or
   - `contradiction`.
6. Call `commit_memory` only with a disposition supported by the meanings, not
   merely by similarity scores.

Do not split one qualification from the statement it limits. Do split unrelated
decisions or instructions that may later change independently.

## Contradictions

A contradiction must not be silently resolved. Present the existing record,
the proposal, scope, provenance, and exactly these three numbered suggestions:

1. Keep the existing memory and reject the candidate.
2. Supersede the existing memory with the candidate.
3. Keep both with explicit scopes or conditions explaining when each applies.

Then ask the user to choose or provide a custom resolution. Do not call
`resolve_conflict` before the user answers. Never let the pending candidate
silently replace an active rule.

## Corrections, removal, and use

- Use `correct_memory` for an auditable revision, not an invisible rewrite.
- Use `retract_memory` when a record should leave normal recall but its history
  should remain.
- Use destructive deletion only when policy permits and the user clearly asks.
- Call `record_memory_use` only after a record was actually relied on. Supply an
  idempotent operation ID so retries do not inflate activation.
- Retrieval alone is not use and must not be recorded as reinforcement.

## Global memory

Global means deliberately applicable across projects, not copied from every
project.

- Explain why a proposal is global.
- Ask for user confirmation when global intent is not explicit.
- Never promote a record because it is frequently accessed.
- Never scan or merge project ledgers to infer global memory.
- Preserve visible scope labels in every answer.

Examples:

- “Use this coding style in project Alpha” is local.
- “Always use British English in every project” may be global.
- “This paper argues X” is document knowledge, not global memory.

## Relations

Store only a direct relationship that can answer clearly how A relates to B.
Specify source, target, direction, predicate, conditions, rationale, and
provenance. Never store a vague `related_to` edge merely because two records are
similar.

Use `find_relation_paths` for indirect chains. Preserve every returned edge and
its direction. Do not strengthen `contributes_to` into `causes`. A bounded miss
means only “no path found within configured limits.”

## Export and import

Use `export_memory` for a human-readable snapshot. Explain that the Markdown is
a view and SQLite remains authoritative.

When import becomes available, preview and reconcile it. Never represent import
as a blind restore, and never activate a contradictory imported rule without the
normal user-mediated resolution.

## Response language

When relying on memory:

- distinguish active rules from ranked contextual memories;
- identify project or global scope;
- cite stable memory IDs for consequential reliance;
- disclose open conflicts and stale indexes; and
- distinguish remembered operational context from source-backed evidence.
