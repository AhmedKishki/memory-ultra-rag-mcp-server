# Memory UltraRAG MCP

Memory UltraRAG MCP is a planned local, CPU-first MCP server that gives an AI
agent durable operational memory between sessions.

It is for things the next agent session needs in order to continue work
correctly: active rules, project status, material progress, preferences, style,
tone, formatting, decisions, commitments, procedures, outcomes, and open
questions.

It is **not** a document knowledge base. PDFs, articles, quotations, literature
notes, and general source knowledge belong in a document-oriented RAG. This
server may remember why a source affected a decision, but it does not replace
the source or present remembered text as evidence.

This project is based on the MCP architecture and retrieval work of
[UltraRAG](https://github.com/OpenBMB/UltraRAG). It is an independent project;
see [UltraRAG credit](#ultrarag-credit) below.

> **Current status:** design and documentation only. There is no installable MCP
> executable yet. Implementation follows [TODO.md](TODO.md) one tested milestone
> at a time.

## What it will provide

- One installation serving many isolated projects.
- A project-local memory store selected by project name, not per-call paths.
- A deliberately shared global-memory shard for cross-project rules and
  preferences.
- Persistent rules that remain operative until atomically replaced or
  explicitly retracted.
- Exact and lexical recall first, followed later by CPU semantic retrieval.
- Duplicate, refinement, and contradiction review instead of unbounded note
  accumulation.
- Explicit user decisions for conflicts and destructive changes.
- Direct typed relations and bounded path search in a later milestone.
- Deterministic, human-readable Markdown export; reviewed Markdown import later.
- No required GPU, external vector database, or separate language model.

The AI agent decides whether a conversation contains something worth proposing
as memory. The server checks scope, structure, existing records, redundancy, and
conflicts. The user remains the final authority.

## Project and global memory

Project memory belongs to one project only. A server session first initializes
an exact registered project name, then ordinary tools operate on that bound
project without accepting arbitrary filesystem paths.

Global memory is a separate store for deliberately shared information, such as
"Across all projects, keep summaries concise." It is not a union of all project
databases and the server never scans every project to infer global memory.

Examples:

| Instruction or context | Intended destination |
| --- | --- |
| “Use Chicago notes in this project.” | Project rule |
| “Across all projects, prefer concise prose.” | Global rule, after explicit review |
| “The parser migration is complete.” | Project progress/status |
| “We rejected option B because it breaks compatibility.” | Project decision |
| A chapter, paper, or quotation | A document RAG, not memory |

A project rule may explicitly override a global rule inside that project. The
agent receives both records and a clear precedence explanation.

## Intended agent workflow

Once implemented, a normal session will be:

1. The agent calls `initialize_project` with the registered project name.
2. The agent calls `recall_memory` for the task it is about to perform.
3. The server returns applicable active rules separately from ranked operational
   memories.
4. The agent follows those rules and uses relevant status, decisions, and
   commitments to continue the work.
5. When new durable context appears, the agent extracts small typed proposals
   and calls `propose_memory`.
6. The server returns exact, lexical, and eventually semantic comparison
   candidates without activating the proposal.
7. The agent classifies it as new, equivalent, a refinement, or contradictory,
   then calls `commit_memory` when safe.
8. A contradiction is shown to the user with exactly three suggestions: keep
   the existing record, supersede it, or keep both under explicit conditions.

The server does not silently record whole conversations. It does not decide
truth from an embedding score. It does not silently resolve contradictions.

### Example

User:

> Remember that all project reports must use British English.

The agent proposes a project-scoped `rule`. If no equivalent or conflict exists,
the server commits it and returns a stable memory ID. On later recalls, the rule
appears in `applicable_rules`, regardless of whether it happens to be a strong
semantic match for the task wording.

If the user later says "Use American English for this project," the original
rule stays operative while the contradiction is unresolved. A confirmed
replacement supersedes the old revision atomically.

## Storage and isolation

Every project's content-bearing state will live in one directory:

```text
<project>/.memory-rag/
├── project.json
├── memory.sqlite3
├── project.lock
├── config.json
├── exports/
└── runtime/
    ├── indexes/
    └── logs/
```

- `project.json` holds stable project identity.
- `memory.sqlite3` is the authoritative, versioned memory ledger.
- `exports/` contains human-readable snapshots created by the user or agent.
- `runtime/` contains disposable retrieval indexes and local operational logs.

Project A and Project B use different databases and indexes. A missing or stale
index never falls back to another project. Shared user caches may contain model
binaries, but never project memory.

The global shard uses its own database, indexes, and lock in the operating
system's per-user application-data location. Its exact cross-platform path will
be finalized before implementation.

## Rules and recall

Rules are operative state, not ordinary search results. Applicable active rules
are returned in a dedicated field when an agent recalls memory or prepares a
memory change. They are selected by explicit project/global scope and optional
conditions, not by similarity alone.

An active rule:

- does not decay because of age or low access;
- is protected from automatic pruning;
- stays active while a proposed replacement is disputed; and
- changes only through an auditable atomic replacement or explicit retraction.

Other memory is relevance-ranked and may include status, progress, decisions,
commitments, preferences, procedures, outcomes, and open questions.

## Export and import

The first useful release will export deterministic Markdown grouped by scope and
record type. It will include stable IDs, lifecycle states, active rules, and
unresolved conflicts. The export is readable and portable, but SQLite remains
the authority.

Import comes later. It will accept the server's versioned export format, stage a
preview, check duplicates and conflicts, and reconcile approved records. It will
never blindly overwrite the database or activate contradictory rules. Importing
arbitrary prose is optional future work because semantic distillation requires
more than sentence splitting or embedding similarity.

## Planned MCP tools

Names remain provisional until their schemas are implemented and tested.

| Area | Planned tools |
| --- | --- |
| Project | `list_projects`, `initialize_project`, `project_status` |
| Write/revise | `propose_memory`, `commit_memory`, `correct_memory`, `confirm_memory`, `retract_memory`, `delete_memory` |
| Recall | `recall_memory`, `get_memory`, `list_memories` |
| Conflicts | `list_conflicts`, `resolve_conflict` |
| Global | `promote_memory_to_global` and explicit global recall/write scope |
| Relations | `find_relation_paths` |
| Maintenance | `record_memory_use`, `compact_memory`, `rebuild_indexes` |
| Portability | `export_memory`, later `import_memory` |

Tools appear only when their complete behavior exists. The initial project
boundary will not pretend to support semantic retrieval, graph traversal, or
global memory.

## How it will work

SQLite is the canonical ledger. It stores stable IDs, typed records, revisions,
scope, provenance, lifecycle state, conflicts, and direct relations in
transactions. Retrieval indexes can always be rebuilt from it.

The implementation sequence is deliberately small:

1. Safe name-based project selection and isolated SQLite storage.
2. Local rules and operational memory with exact deduplication, SQLite FTS5,
   revision history, and Markdown export.
3. CPU embeddings for semantic candidates and user-mediated contradictions.
4. A physically separate, explicitly requested global shard.
5. Direct relations and bounded path search.
6. Use-based activation, bounded retention, reviewed compaction, import, and an
   optional local UI.

UltraRAG supplies the MCP-oriented architecture and reusable public retrieval
components where they satisfy the contract. This project supplies the
operational record model, project/global isolation, rule semantics, lifecycle,
conflicts, relations, retention, and portability. Upstream UltraRAG source will
not be modified.

See [PLAN.md](PLAN.md) for the complete design, [TODO.md](TODO.md) for the ordered
implementation gates, and [AGENT_GUIDE.md](AGENT_GUIDE.md) for the future agent
behavior contract.

## UltraRAG credit

This project directly builds on [UltraRAG](https://github.com/OpenBMB/UltraRAG)
and its [documentation](https://ultrarag.openbmb.cn/). Credit belongs to the
UltraRAG team and contributors, including the participating organizations named
by the project: THUNLP, NEUIR, OpenBMB, and AI9stars.

The supported upstream version/commit and applicable Apache-2.0 attribution
will be pinned before code is released. Memory UltraRAG MCP is independently
maintained and does not imply endorsement by the UltraRAG project or its
contributors.
