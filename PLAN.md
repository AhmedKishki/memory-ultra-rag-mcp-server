# Memory UltraRAG MCP — Design and Implementation Plan

Status: design phase; not implemented

This document consolidates the agreed design for a standalone,
CPU-first `memory-ultra-rag-mcp` stdio server. It is a plan, not a description
of working software.

## 1. Purpose

Build a durable semantic memory service that an AI agent can use across many
projects from one installed MCP package.

The server must:

- keep every project's memory physically and logically isolated;
- provide one deliberately shared global-memory shard;
- accept explicit memory proposals from an AI agent rather than silently
  recording conversations;
- turn useful context into typed, atomic, searchable records;
- merge semantic duplicates instead of accumulating paraphrases;
- identify contradictions and require the user to resolve them;
- represent explicit directional and symmetric relations;
- support bounded relation-path search without materializing every possible
  path;
- prevent indefinite growth through admission control, consolidation,
  class-specific capacity, and transparent compaction;
- run locally on CPU initially, with a possible GPU path later; and
- use UltraRAG without modifying the upstream UltraRAG source tree.

This is a memory system for project state, decisions, preferences, procedures,
and learned experience. It is not a document knowledge base, source-citation
system, general conversation archive, or replacement for the original project
files.

## 2. Core principle

The canonical object is a **versioned semantic assertion**, not a text chunk.
The system is a curated memory ledger with a small explicit relation graph and
rebuildable retrieval indexes.

The following invariants govern the design:

1. Retrieval is not proof.
2. Similarity is not identity or equivalence.
3. Frequency of access is not trustworthiness.
4. Calendar age does not by itself make information less true.
5. Only explicit, meaningful direct relations are stored.
6. Inferred relation paths are query results, not durable edges.
7. Contradictions are never silently resolved.
8. The canonical database is authoritative; indexes are disposable derivatives.
9. Project memory never leaks into another project.
10. Global memory is used only when an agent explicitly requests that scope.
11. Nothing becomes durable merely because it appeared in a conversation.
12. Source-document evidence and remembered assertions remain distinct.

## 3. UltraRAG boundary

### UltraRAG is used for

- the MCP-oriented architecture and stdio integration pattern;
- compatible server/tool conventions and agent-facing descriptions;
- CPU retrieval components and patterns where their public interfaces fit;
- dense retrieval and FAISS support where appropriate;
- lexical/dense retrieval orchestration patterns; and
- optional future pipeline and shared-UI integration.

### This project implements

- project discovery, initialization, and isolation;
- the canonical memory database;
- memory types, classes, scopes, and lifecycle states;
- semantic-admission and reconciliation workflows;
- provenance, confirmation, and explainable reliability fields;
- versioning, correction, retraction, and supersession;
- contradiction records and user-mediated resolution;
- the direct-relation graph and bounded path search;
- use-based activation and class-specific retention;
- compaction and pruning;
- project/global result separation; and
- the high-level MCP tools and agent operating instructions.

UltraRAG's existing memory component is not sufficient as the canonical store.
It appends conversation turns to Markdown and makes them retrievable, but does
not provide the lifecycle, structured relations, contradiction handling,
project/global scopes, trust separation, or bounded-retention contract required
here. Its behavior should be evaluated and credited, but not mistaken for this
system's record model.

The implementation must pin and verify the supported UltraRAG release. It must
not patch or write into the managed UltraRAG runtime.

## 4. Attribution

The eventual repository must prominently credit UltraRAG and link to its
canonical project:

- https://github.com/OpenBMB/UltraRAG
- https://ultrarag.openbmb.cn/

Credit must acknowledge the UltraRAG team and contributors and the project's
stated participating organizations: THUNLP, NEUIR, OpenBMB, and AI9stars. The
repository must say that this is an independent project and must not imply
endorsement.

## 5. One installation, many projects

One installed executable must manage many project stores without asking the
user or agent to supply filesystem paths to ordinary memory tools.

### 5.1 One-time installation configuration

The MCP configuration may supply one trusted projects root through
`--projects-root`, `ULTRARAG_PROJECTS_ROOT`, or a shared user configuration
file. When it is not configured, every MCP server uses the same default:

```text
~/ultrarag-projects/
```

Projects then resolve to:

```text
~/ultrarag-projects/<project-name>/
```

This is a default parent directory, not a shared default project. The server
must never place unrelated work in one implicit `default` project.

Resolution precedence is:

1. an explicit `--projects-root` supplied by the MCP installation;
2. `ULTRARAG_PROJECTS_ROOT`;
3. the shared user configuration file; and
4. `~/ultrarag-projects/`.

The final root is expanded, canonicalized, and validated once at server
startup. The same resolved root and registry format must be used by the vanilla,
research, memory, and future project-oriented MCP servers.

The installation may also supply an optional configurable global-memory root.
Global memory is application data and should use the operating system's normal
per-user data location by default rather than appearing as a project beneath
`~/ultrarag-projects/`.

This is installation configuration, not per-query input. The user configures it
once.

The server must never let an agent use an arbitrary path as a project name.
Project names and aliases resolve only through the trusted projects root and a
small registry.

Existing projects outside the default root may be registered once in the
shared registry after explicit authorization. Ordinary agent calls still use
the registered name, never the stored filesystem path.

### 5.2 Project manifest and registry

Each initialized project receives a stable manifest such as:

```text
<project>/.ultrarag/project.json
```

It contains at least:

- a stable random `project_id`;
- the canonical project name;
- optional aliases;
- the manifest schema version; and
- initialization timestamps.

A user-level registry may map names and aliases to canonical paths and project
IDs. It may contain project names and paths, but never project content, memory
records, document chunks, or search results. The in-project manifest remains
the identity authority.

### 5.3 Session initialization

The agent's first project operation is:

```text
initialize_project(project_name)
```

The operation must:

1. validate the name as a name, not a path;
2. resolve an exact registered name or alias beneath the trusted projects root;
3. reject traversal, symlink escape, and roots outside the configured boundary;
4. verify or create the project's manifest and memory state directory;
5. bind the current stdio MCP session to the resulting project ID;
6. initialize the project database and indexes if absent; and
7. return the selected name, project ID, resolved root, state, and schema version.

If no exact project exists, the server returns `not_found` and safe candidate
names. It must not create a new project because of an agent typo. Creating a
new top-level project, if supported, is a separate explicit operation requiring
clear user intent.

If a name is ambiguous, initialization fails and the agent asks the user to
choose. It must never guess.

After initialization, ordinary memory tools take no filesystem paths and no
project name. They use the project bound to that MCP session. Calls made before
initialization fail with a concise instruction to initialize a project.

A stdio process is expected to serve one active project at a time. Switching
projects must be explicit, must wait for current operations to finish, and must
clear all project-bound caches and handles. Running separate processes for two
simultaneously active projects remains supported and uses the same installed
package.

Client-provided MCP roots may be used as a safe convenience when available, but
the server must not depend on every MCP client implementing roots correctly.

### 5.4 Project isolation contract

All content-bearing project state lives beneath:

```text
<project>/.ultrarag/memory/
```

This includes the database, indexes, locks, content-bearing logs, import reports,
and generated exports. Shared model weights and verified program runtimes may
use an OS cache because they contain no project content.

Project memory must be stored in physically separate database and index files,
not one database that relies only on a `WHERE project_id = ...` condition.

There is no cross-project project-memory search. A missing index or database
must never cause fallback to another project.

### 5.5 Shared MCP project contract

Project initialization and isolation are infrastructure behavior, not a RAG
feature. All project-oriented MCP servers must therefore present the same
high-level lifecycle:

1. discover or initialize a project by registered name;
2. bind the stdio session to its stable project ID;
3. expose the active identity through `project_status`;
4. omit paths from ordinary tools;
5. keep content-bearing state in a server-specific directory beneath the
   project's `.ultrarag/` directory; and
6. reject any operation that could read or write another project's content.

The server-specific state layout is:

```text
<project>/.ultrarag/
├── project.json
├── vanilla/
├── research/
└── memory/
```

The research and vanilla MCP servers should behave identically from the user's
perspective even though their internal tools differ. Research continues to
apply its PDF/EPUB and evidence policies. Vanilla continues to expose upstream
UltraRAG features, but its gateway must enforce the active project boundary on
all filesystem-bearing arguments and stateful external backends.

For vanilla, this project guard is an explicitly documented gateway constraint;
it does not modify UltraRAG source code. Write paths must remain inside the
active project. Read paths must be limited to the active project plus narrowly
allowlisted, content-free runtime and model caches. External collections, where
supported, must be namespaced by project ID or refused when isolation cannot be
guaranteed.

## 6. Memory scopes and levels

Scope and memory class are independent dimensions.

### 6.1 Scope

- `session`: optional ephemeral state owned by the running session; not durable
  unless deliberately promoted.
- `project`: durable state visible only while that project is active.
- `global`: durable state in the single shared global-memory shard.

Project memory and global memory use physically separate SQLite databases and
separate indexes.

### 6.2 Class

- `working`: bounded active context; aggressively consolidated or evicted.
- `episodic`: events, attempts, outcomes, and experience.
- `semantic`: facts, definitions, decisions, constraints, and terminology.
- `procedural`: workflows, instructions, and methods.
- `pinned`: a retention modifier; never automatically removed.

A record can therefore be `project + procedural`, `global + semantic`, or any
other valid combination.

### 6.3 Global-memory policy

Global memory is an intentional shared shard for information such as durable
user preferences and genuinely cross-project procedures. It is not a union of
all project memories.

The default write and search scope is `project`. The AI agent may explicitly
request `global` or `project + global` based on the user's request and must
provide a short rationale.

Examples:

- "Use APA citations in this project" becomes project memory.
- "Across my projects, prefer concise summaries" may be proposed globally.
- "This author defines a term in this book" belongs in the project's document
  knowledge base, not global memory.
- "Remember this globally" is an explicit global-memory request.

Ambiguous information defaults to project scope. Project records must never be
promoted merely because they were frequently accessed. Promotion to global is
an explicit, reviewable operation.

The server must not scan every project to infer global rules. Such a scan would
break the isolation contract. Any future cross-project consolidation must be a
separate user-authorized operation with a visible input set and review report.

### 6.4 Scope precedence and conflicts

A project-specific instruction may operationally override a global preference
inside that project without changing the global record. Both records are
returned with their scopes and the local override is explicit.

If scope alone does not explain the difference, it is an ordinary contradiction
and enters the conflict workflow.

Results from project and global stores must remain visibly separated. A merged
ranking may be offered, but every result retains its scope and stable ID.

Relations may be stored project-to-project and global-to-global. A project may
reference a global memory. The global shard must not point back to a
project-local record, because that would couple global state to one project.

## 7. Canonical data model

The initial authoritative store is SQLite with foreign keys and transactional
writes. A possible project layout is:

```text
<project>/.ultrarag/memory/
├── memory.sqlite3
├── project.lock
├── config.json
├── indexes/
│   ├── current.json
│   └── generations/
├── logs/
└── exports/
```

The configurable global root uses the same internal layout but has no project
documents or project-local records.

Initial logical tables:

- `memories`: current canonical record and lifecycle state;
- `memory_revisions`: bounded, auditable content history;
- `provenance`: origins, confirmations, and evidence references;
- `relations`: direct typed edges only;
- `conflicts`: unresolved and resolved contradiction cases;
- `usage_rollups`: bounded confirmed-use statistics;
- `index_generations`: dense/lexical index state and model identity; and
- `schema_migrations`: database schema versioning.

Every durable memory needs at least:

- stable namespaced memory ID;
- scope and class;
- type, such as fact, preference, decision, constraint, hypothesis, procedure,
  event, outcome, terminology, or open question;
- atomic statement or structured subject/predicate/object form;
- project/global namespace;
- lifecycle and review state;
- provenance and origin type;
- creation, revision, and confirmation timestamps;
- explicit validity interval when the information itself expires;
- tags or normalized entities;
- activation counters; and
- a content fingerprint used only for exact duplicate detection.

Suggested lifecycle states:

- `active`
- `pending`
- `disputed`
- `superseded`
- `retracted`
- `expired`
- `deleted`

Deletion must be explicit and auditable. Search excludes inactive records by
default.

## 8. Semantic admission policy

Memory is curated state, not an archive. Admission control is the main defense
against bloat.

### 8.1 The agent performs semantic extraction

The AI agent determines whether supplied context contains durable information
and proposes atomic records. The agent is responsible for meaning-level work:

- separating facts, decisions, preferences, procedures, hypotheses, and events;
- preserving qualifications and scope;
- normalizing entities and terminology;
- explaining proposed relations;
- excluding conversational scaffolding and repeated prose; and
- attaching available provenance.

The server cannot assume that it can call the client's language model. The
portable initial design therefore uses the already-running agent through a
two-phase tool workflow. MCP sampling or a separately configured extraction
model may later provide a one-call convenience, but cannot be required.

### 8.2 The server enforces structural policy

The server:

- validates schemas and allowed types;
- enforces the active project and requested scope;
- performs exact duplicate checks;
- retrieves semantic and lexical comparison candidates;
- requires the agent to state whether candidates are new, equivalent, a
  refinement, or contradictory;
- enforces user mediation for contradictions;
- applies lifecycle transitions transactionally; and
- updates rebuildable indexes only after canonical storage succeeds.

### 8.3 Appropriate content

Normally retain:

- explicit user preferences and durable instructions;
- project decisions and their reasons;
- project constraints and important state changes;
- verified project facts;
- procedures that succeeded or failed, including their conditions;
- terminology and definitions used by the project;
- explicitly marked hypotheses;
- unresolved questions worth revisiting; and
- references to external evidence without presenting the memory itself as that
  evidence.

Normally reject or ignore:

- greetings and conversational filler;
- generated prose and full assistant responses;
- repeated restatements from the same origin;
- temporary logs, progress messages, and raw tool output;
- speculative brainstorming represented as fact;
- source material already stored in a document knowledge base;
- unsupported claims invented by an agent; and
- credentials, secrets, or sensitive values not explicitly intended for
  durable storage.

An explicit user request to remember something is presumed intentional, but the
server still canonicalizes it, checks for conflicts and duplication, and warns
about sensitive or obviously transient content.

## 9. Write and reconciliation workflow

The preferred portable workflow has two phases.

### Phase A: propose and inspect

1. The user asks the agent to remember context.
2. The agent produces one or more atomic, typed proposals.
3. The agent calls `propose_memory` in project scope unless another scope is
   explicit.
4. The server validates the proposal and retrieves possible existing matches
   using exact fingerprints, lexical search, embeddings, and entity keys.
5. The server returns a comparison packet without writing an active memory.

### Phase B: reconcile and commit

6. The agent compares meanings and reports one disposition per proposal:
   `new`, `equivalent`, `refinement`, or `contradiction`.
7. The server validates the requested transition.
8. New assertions are created; equivalent assertions merge; refinements create
   revisions; contradictions become pending conflicts.
9. Canonical changes commit transactionally.
10. Lexical and dense indexes update or build a verified next generation.
11. The server returns stable IDs, resulting states, reasons, index state, and
    required user actions.

An exact repetition from the same provenance creates neither a new row nor
artificial reinforcement. Independent confirmation may add provenance and
confirmation strength. It does not count as usage.

If dense indexing fails after a canonical commit, the record remains safe in
SQLite, the dense index is marked stale, and status reports the problem. The
server must never silently point at a partially built index.

## 10. Contradictions, corrections, and supersession

A contradictory candidate is stored in a non-active pending state. It never
silently replaces the current memory.

The existing memory remains available but is marked as having an open conflict.
Any affected dependent relations are marked for review.

The MCP result must contain the existing record, candidate, provenance,
conflict reason, and exactly these three machine-readable suggestions:

1. Keep the existing memory and reject the candidate.
2. Supersede the existing memory with the candidate.
3. Keep both with explicit scopes or conditions explaining when each applies.

The agent instructions require the agent to present those three numbered
choices to the user and not call `resolve_conflict` until the user responds.
The user may also provide a custom resolution.

Corrections create revisions; they do not rewrite history invisibly.
Superseded and retracted records leave normal retrieval but remain available to
audit tools subject to retention policy.

## 11. Relations and graph search

Only direct, justified relations are durable. The server never precomputes or
stores the transitive closure.

A relation must clearly express:

> Memory A `[specific predicate]` memory B, under `[scope/conditions]`, according
> to `[provenance/rationale]`.

Every relation contains:

- source memory ID;
- allowed relation type;
- target memory ID;
- directionality;
- inverse display label where applicable;
- scope and conditions;
- rationale and provenance;
- lifecycle state; and
- review state.

Initial relation vocabulary should be deliberately small:

- `causes` / `caused_by`
- `contributes_to` / `influenced_by`
- `enables` / `enabled_by`
- `depends_on` / `required_by`
- `supports`
- `contradicts`
- `supersedes`
- `part_of` / `contains`
- `equivalent_to`

Do not persist a vague `related_to` edge. Similarity without a clearly stated
relationship remains a retrieval signal only.

Rules:

1. Both endpoints and the precise relation type are mandatory.
2. Directionality comes from a relation-type registry.
3. Causal relations require provenance and user confirmation when inferred.
4. Contradictory relations enter the conflict workflow.
5. Duplicate edges reinforce or merge provenance; they do not multiply.
6. Relations are versioned, disputable, supersedable, and retractable.
7. Updating a memory marks semantically affected relations for review.
8. A path never automatically creates a shortcut edge.
9. Shared topics should normally use normalized entity/topic nodes instead of
   creating pairwise edges between every associated memory.
10. Joint causality may use an explicit causal-claim node rather than pretending
    that each premise independently causes the outcome.

### Bounded path search

`find_relation_paths` performs a bounded graph search over direct edges. If both
endpoints are known, use bidirectional search where compatible with relation
direction.

Initial limits:

- default maximum path length: 3 edges;
- hard maximum path length: 6 edges;
- default returned paths: 5;
- hard maximum returned paths: 20;
- default expanded neighbors per node: 25;
- hard maximum expanded neighbors per node: 100;
- default total expanded nodes: 500; and
- hard maximum total expanded nodes: 2,000.

The search uses a visited set, disallows repeated nodes within a path, respects
edge direction, prioritizes reviewed and specific edges, and reports
`truncated` when a budget is reached.

A miss is reported as "no path found within configured limits," never as proof
that no relationship exists.

Returned paths include every edge, rationale, direction, scope, review state,
and hop count. The agent may explain a path but must not omit steps or strengthen
`contributes_to` into `causes`.

Path traversal alone does not count as use. Only a path actually relied on by
the agent can be recorded as used.

## 12. Retrieval design

The recommended default is local hybrid retrieval:

- SQLite FTS5/BM25 for exact terms, identifiers, names, and phrases;
- a small pinned CPU embedding model for paraphrases and conceptual similarity;
- FAISS or another in-process, project-local vector index for dense candidates;
- structured filters for scope, class, type, state, tags, entities, and validity;
  and
- bounded graph traversal for explicit relations.

No external Qdrant or Milvus service is required initially. A future backend
may be added behind the same contract.

Embeddings generate candidates. They do not decide truth, equivalence,
contradiction, causality, or trustworthiness. Meaning-level reconciliation is
performed by the agent and conflict decisions remain user-mediated.

The exact embedding model, vector index, fusion method, candidate limits, and
CPU performance targets must be measured and pinned during implementation.

### Ranking dimensions

Search ranking may consider:

- lexical relevance;
- dense relevance;
- structured entity matches;
- class priority;
- use-based activation;
- review state and provenance quality; and
- open-conflict penalties or warnings.

Scores from unrelated systems must not be presented as calibrated truth
probabilities. The response should expose the component ranks and reasons.

## 13. Trust, activation, age, and validity

Do not reduce trustworthiness to an unexplained scalar. Expose its components:

- origin type;
- user-confirmation state;
- inference status;
- number and independence of supporting provenance records;
- open contradictions;
- review state; and
- retraction or supersession state.

Activation is separate:

- confirmed use count;
- explicit reinforcement count; and
- successful application count.

Retrieval alone must not increase activation, because that creates a feedback
loop favoring early results. After the agent actually uses a memory, it calls a
deduplicated `record_memory_use(memory_ids, operation_id)` tool.

Repeated access may improve retrieval rank but never makes a claim more true.

Creation and review dates remain audit fields. Calendar age does not ordinarily
reduce rank. Facts with intrinsic validity dates use `valid_from`, `valid_until`,
or an explicit expiry policy.

## 14. Pruning and bounded growth

Pruning follows admission control and consolidation; it is not a substitute for
them.

- Working memory has a strict configurable capacity and evicts low-activation,
  unpinned records.
- Repeated episodes may consolidate into a procedural or semantic memory while
  retaining a compact provenance summary.
- Exact duplicates do not create records.
- Repeated provenance from the same origin is deduplicated.
- Superseded, retracted, deleted, and expired records are excluded from ordinary
  indexes.
- Raw usage events are rolled into bounded counters or time buckets.
- Only a bounded number of full revisions need remain online; older audit data
  may be compacted into a digest according to an explicit policy.
- Stale index generations, orphaned vectors, and temporary build artifacts are
  garbage-collected.
- Pinned records, active durable records, and open conflicts are never removed
  automatically.

`compact_memory(dry_run=true)` must be the default and return an exact report of
proposed merges, removals, rollups, and reclaimed storage. Destructive
compaction requires explicit confirmation and creates an audit entry.

## 15. What search returns

The MCP server returns structured memory evidence, not a generated prose answer.
Each result includes at least:

- memory ID and scope;
- canonical statement and structured fields;
- class, type, state, and review state;
- provenance summary;
- validity information;
- activation statistics;
- retrieval methods and component ranks;
- open-conflict warning;
- relevant direct relations; and
- optional bounded paths with complete edge detail.

Project and global results remain separately labelled. The agent interprets the
records and answers the user.

Remembered text must never be represented as document evidence merely because
it contains a citation. Evidence references are links for follow-up, not proof
that the memory is a verified source passage.

## 16. Raw context and Markdown

The normal server does not ingest files. It receives explicit context or
structured proposals from the AI agent.

If bulk Markdown migration is added later, it must be a separate, explicit
workflow. It must not turn every sentence or fixed-size chunk into memory.

A semantic import would:

1. segment by document structure and meaning;
2. extract typed atomic claims, decisions, preferences, procedures, events, and
   entities with the AI agent or a separately configured model;
3. retain source section or line ranges as provenance;
4. normalize entities across sections;
5. consolidate cross-section repetition;
6. retrieve existing project/global comparison candidates;
7. classify proposals as new, equivalent, refinement, or contradictory;
8. present uncertain and conflicting proposals for review; and
9. commit only the distilled accepted records.

The original Markdown remains external provenance. Rejected prose is summarized
in an import report rather than stored as inactive memory, because storing every
rejection would recreate the bloat problem.

Embeddings help locate paraphrases but cannot replace semantic extraction and
comparison. A portable first release should defer bulk Markdown import rather
than introduce a hidden second language-model dependency.

## 17. Proposed MCP surface

Names and schemas remain provisional until implementation, but the capability
boundary should be:

### Project lifecycle

- `list_projects`: list safe registered names and initialization state, never
  arbitrary filesystem contents.
- `initialize_project`: bind this MCP session to one exact project name.
- `project_status`: report active name, ID, roots, database/index state, counts,
  conflicts, and storage health.

### Memory admission and mutation

- `propose_memory`: validate proposals and return reconciliation candidates.
- `commit_memory`: apply an agent-reviewed non-conflicting disposition.
- `correct_memory`: create a revision with explicit reason and provenance.
- `confirm_memory`: record user or evidence-backed confirmation.
- `retract_memory`: mark a memory invalid without erasing audit history.
- `delete_memory`: explicit controlled deletion where policy permits it.
- `promote_memory_to_global`: create a reviewed global counterpart; never an
  automatic move.

### Retrieval and graph

- `search_memory`: search project by default, optionally project plus global.
- `get_memory`: retrieve one record, revisions, provenance, and direct edges.
- `list_memories`: filtered inspection, not an unbounded dump.
- `find_relation_paths`: bounded direct-edge graph traversal.
- `record_memory_use`: record actual use with an idempotent operation ID.

### Conflicts and maintenance

- `list_conflicts`
- `resolve_conflict`
- `compact_memory`, defaulting to dry-run
- `rebuild_indexes`
- `export_memory`, producing a human-readable representation

All tool parameters require clear Pydantic/MCP descriptions. Mutating tools
must accurately set read-only, destructive, idempotent, and open-world hints.

## 18. Agent operating contract

Server instructions and the agent guide must require the agent to:

1. initialize the exact project by name before project operations;
2. show the active project when it could be ambiguous;
3. use project scope unless global scope is clearly intended;
4. never pass or invent storage paths for ordinary calls;
5. extract atomic proposals instead of storing entire messages;
6. preserve qualifications, origin, and uncertainty;
7. inspect returned comparison candidates semantically;
8. never treat embedding similarity as a final duplicate decision;
9. present exactly three recommended choices for a contradiction;
10. wait for the user's explicit conflict decision;
11. cite memory IDs when relying on memory;
12. call `record_memory_use` only for records actually used;
13. distinguish remembered information from document evidence; and
14. never imply that a bounded graph-search miss proves no relation exists.

## 19. Plain Markdown comparison

Plain Markdown remains useful because it is portable, human-readable, easy to
edit, and Git-friendly. It is inadequate as the sole authority for this design
because identity, types, scopes, lifecycle states, relations, provenance,
conflicts, activation, and pruning remain implicit and unenforced.

The recommended combination is:

- SQLite as the canonical ledger;
- rebuildable lexical and dense indexes;
- a generated read-only Markdown export for human inspection; and
- a deliberate import/reconciliation process for externally edited Markdown.

The export is a view, not the authoritative database.

## 20. Resource requirements

Initial operation should require:

- Python and the package's isolated environment;
- a pinned, verified UltraRAG runtime or dependency;
- SQLite with FTS support;
- a pinned CPU-compatible embedding model downloaded once;
- an in-process vector index such as FAISS; and
- the host AI agent for semantic extraction and reconciliation.

It should not require:

- a GPU;
- a local UltraRAG Git clone;
- a Qdrant or Milvus service;
- a second language model for ordinary operation; or
- a network connection after dependencies and models are cached.

A fully autonomous raw-text or bulk-import call would require MCP sampling or a
separately configured local/API model and is therefore deferred.

## 21. Safety and concurrency

- Canonicalize and validate all configured roots.
- Reject absolute, traversing, and symlink-escaping project names or references.
- Serialize writes with in-process and cross-process project/global locks.
- Use SQLite transactions and foreign keys.
- Never expose a partially built dense index as current.
- Keep MCP stdout reserved for protocol messages.
- Never log secrets or full memory content in shared operational logs.
- Keep content-bearing logs in the appropriate project or global scope.
- Back up or snapshot the canonical database before schema migrations and
  destructive compaction.
- Never let a global-memory failure fall back to a project database, or vice
  versa.

## 22. Implementation phases

### Phase 0 — evaluation and contracts

- Inspect the pinned UltraRAG memory, retriever, and MCP APIs.
- Confirm which components can be reused without private imports or patches.
- Pin the UltraRAG version and attribution.
- Finalize project registry and global-root locations.
- Finalize the memory/relation schema and lifecycle state machine.
- Define measurable CPU and storage targets.

### Phase 1 — isolated canonical store

- Scaffold the standalone package and stdio entry point.
- Implement trusted projects-root configuration and name-based initialization.
- Implement project manifests, registry validation, SQLite migrations, locks,
  and status.
- Implement structured create/get/list/correct/retract operations without dense
  retrieval.
- Prove two-project physical isolation with adversarial path tests.

### Phase 2 — semantic admission and hybrid retrieval

- Implement the two-phase proposal/reconciliation contract.
- Add FTS5/BM25 retrieval.
- Evaluate and pin a CPU embedding model and vector backend.
- Add immutable or atomic index switching and stale-index reporting.
- Return explainable component ranks and scope-labelled results.

### Phase 3 — conflicts, relations, and global memory

- Implement conflict persistence and the three-choice resolution workflow.
- Implement relation registry, direct edges, revision review, and bounded paths.
- Implement the physically separate global shard.
- Add explicit global search/write/promotion and local-over-global scope behavior.
- Prove that no project store can query another project.

### Phase 4 — activation, pruning, export, and UI adapter

- Implement idempotent confirmed-use recording and rollups.
- Add class capacities, consolidation support, expiry, dry-run compaction, and
  garbage collection.
- Add deterministic Markdown/JSON export.
- Add an adapter for the shared loopback-only UltraRAG MCP UI if appropriate.
- Keep the adapter dependent only on public memory MCP tools.

### Phase 5 — optional extensions

- MCP sampling integration where supported.
- Explicit semantic Markdown migration.
- Optional GPU embeddings.
- Alternative vector backends.
- Explicit user-authorized cross-project consolidation reports.

## 23. Acceptance tests

The first usable release must demonstrate:

1. The package installs once and starts without an UltraRAG checkout.
2. An agent initializes an existing project using only its registered name.
3. A typo cannot create or select an unintended project.
4. Project A and Project B use physically separate databases and indexes.
5. Queries in Project A cannot retrieve Project B records, even under path,
   symlink, malformed-ID, cache, or stale-index attacks.
6. Project and global results are clearly separated and correctly scoped.
7. Global memory is never searched or written unless explicitly requested.
8. Exact duplicates do not multiply records.
9. Semantic equivalents can be merged after agent reconciliation.
10. Contradictions remain pending until the user chooses a resolution.
11. The agent-facing conflict response contains exactly three suggestions.
12. Corrections preserve an audit trail and inactive records leave normal search.
13. Retrieval alone does not change activation; confirmed use does.
14. Relation traversal respects direction and every configured search bound.
15. No transitive paths are materialized as durable relations.
16. Pruning cannot automatically remove pinned records or open conflicts.
17. Failed canonical writes or index builds cannot expose partial state.
18. The server operates offline on CPU after its verified dependencies and model
    artifacts have been cached.
19. Exports are readable but do not become a second writable authority.
20. Memory responses never claim to be source-document quotations or evidence.

## 24. Decisions still requiring implementation evidence

The following choices should be settled through small benchmarks and tests,
not assumption:

- the exact CPU embedding model and pinned artifact revision;
- FAISS versus another embedded vector implementation for frequent updates;
- whether dense updates are incremental or rebuilt in small immutable
  generations;
- hybrid fusion and candidate limits;
- initial capacity defaults for each memory class;
- online revision-retention and usage-rollup limits;
- the exact trust/reliability response schema;
- which non-causal relation types the agent may commit without user review;
- the registry/config location on each supported operating system; and
- whether the first release supports session memory or begins with project and
  global durable memory only.

These choices may change performance and ergonomics, but may not weaken the
scope, conflict, provenance, or audit invariants above.
