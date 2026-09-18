# Memory UltraRAG MCP — Implementation Checklist

This checklist is the implementation sequence. Complete a milestone's gate
before starting the next one. Check an item only when its code, tests, and
documentation are complete.

## Working rules

- [ ] Keep each milestone usable on its own; do not add placeholder tools.
- [ ] Use only public, pinned UltraRAG interfaces and never patch upstream code.
- [ ] Keep canonical project state under `<project>/.memory-rag/` only.
- [ ] Keep every project's content physically separate from every other project.
- [ ] Treat SQLite as authoritative and retrieval indexes as rebuildable.
- [ ] Keep document/source knowledge out of this operational memory.
- [ ] Preserve user authority over conflicts, destructive changes, and global
      rules.
- [ ] Add no dependency or background process without measured need.

## Documentation baseline

- [x] Incorporate the operational-memory and persistent-rule requirements into
      `PLAN.md`.
- [x] Define incremental acceptance-gated milestones in `PLAN.md`.
- [x] Create the user-facing `README.md`.
- [x] Create the engineering-facing `AGENTS.md`.
- [x] Create the runtime agent contract in `AGENT_GUIDE.md`.
- [ ] Keep all four documents synchronized as public interfaces are finalized.

## Milestone 0 — Contracts and measured choices

- [x] Inspect the pinned UltraRAG MCP, memory, and retrieval public APIs
      ([audit](docs/audits/ultrarag-api-audit.md)).
- [x] Record which UltraRAG components are reused and why each custom component
      is necessary
      ([ADR 0001](docs/decisions/0001-ultrarag-boundary.md)).
- [x] Pin the supported UltraRAG version/commit and dependency versions
      ([ADR 0002](docs/decisions/0002-dependency-pins.md)).
- [x] Add the required [license](LICENSE) and UltraRAG
      [attribution/NOTICE](NOTICE) material.
- [x] Choose and document supported Python versions and packaging with `uv`
      ([ADR 0003](docs/decisions/0003-python-and-packaging.md)).
- [x] Confirm SQLite FTS5 on the supported Linux x86-64 target
      ([ADR 0004](docs/decisions/0004-platform-and-fts5.md)).
- [ ] Define the user registry and OS-specific global-memory data locations.
- [ ] Define versioned MCP input/output schemas with complete field descriptions
      and correct tool annotations.
- [ ] Define SQLite schema v1 and forward-only migration rules.
- [ ] Define record types, lifecycle transitions, rule applicability, priority,
      and project-over-global precedence.
- [ ] Define small CPU, latency, and storage benchmark corpora and budgets.
- [ ] Document decisions in a short architecture decision record.

Gate:

- [ ] Dependency boundaries and schemas are reviewed, FTS5 is verified, and no
      dense, graph, global, UI, or pruning implementation has been added.

## Milestone 1 — Safe project boundary

- [ ] Scaffold the installable package, `uv.lock`, tests, and stdio entry point.
- [ ] Start through stdio without writing protocol-invalid output to stdout.
- [ ] Resolve the trusted projects root from CLI, environment, user config, then
      the documented default.
- [ ] Implement a safe registry of project names/aliases to canonical paths.
- [ ] Implement `list_projects`.
- [ ] Implement `initialize_project` with exact-name selection only.
- [ ] Reject arbitrary paths, traversal, symlink escape, ambiguity, and typo-led
      project creation.
- [ ] Create and validate `<project>/.memory-rag/project.json` with a stable ID.
- [ ] Create the empty project-local SQLite database through migrations.
- [ ] Add process and cross-process write locking.
- [ ] Implement `project_status` with identity, schema, health, and counts.
- [ ] Test two simultaneous server processes against different projects.

Gate:

- [ ] Project A and Project B have physically separate state, adversarial path
      tests pass, and no memory content can cross the boundary.

## Milestone 2 — Useful local-memory MVP

- [ ] Implement local record types: rule, status, progress, preference, style,
      tone, formatting, decision, commitment, constraint, procedure, event,
      outcome, hypothesis, and open question.
- [ ] Implement transactional revisions and auditable lifecycle changes.
- [ ] Implement `propose_memory` with schema validation and exact fingerprints.
- [ ] Implement `commit_memory` for `new`, `equivalent`, and `refinement`.
- [ ] Implement exact duplicate and same-origin provenance deduplication.
- [ ] Implement `get_memory` and bounded/filterable `list_memories`.
- [ ] Implement `correct_memory` and `retract_memory` without erasing history.
- [ ] Add SQLite FTS5 retrieval and implement `recall_memory`.
- [ ] Return applicable active rules separately from ranked memories on recall
      and proposal calls.
- [ ] Implement explicit rule applicability, priority, and conditions.
- [ ] Replace or retract an active rule atomically; keep it operative while a
      proposed change is pending.
- [ ] Implement deterministic human-readable Markdown `export_memory`.
- [ ] Reject or warn on document excerpts, source corpora, secrets, and obvious
      conversational filler.
- [ ] Add end-to-end stdio tests and agent-readable tool descriptions.

Gate:

- [ ] An agent can add, find, amend, retract, and export isolated local memory;
      exact repeats do not multiply; active rules cannot disappear mid-update;
      and no embedding model is downloaded.

## Milestone 3 — Semantic reconciliation and contradictions

- [ ] Benchmark candidate CPU embedding models on paraphrase retrieval, size,
      latency, license, and offline installation.
- [ ] Benchmark FAISS against any serious embedded alternative before choosing.
- [ ] Pin the chosen model artifact, dimensions, normalization, and backend.
- [ ] Implement rebuildable dense indexes without changing SQLite authority.
- [ ] Add lexical/dense candidate fusion with component ranks and reasons.
- [ ] Keep embedding similarity advisory; require an agent disposition.
- [ ] Implement persisted contradiction cases and open-conflict warnings.
- [ ] Return exactly three machine-readable resolution suggestions.
- [ ] Implement `list_conflicts` and user-authorized `resolve_conflict`.
- [ ] Keep the existing active rule operative while a rule conflict is open.
- [ ] Switch a resolved rule replacement atomically.
- [ ] Report stale index state and recover cleanly from failed index builds.
- [ ] Test equivalent wording, refinements, real conflicts, and false-positive
      similarity candidates.

Gate:

- [ ] Paraphrases are found without automatic merging, unresolved candidates
      cannot become active, and failed indexing cannot corrupt the ledger.

## Milestone 4 — Deliberate global memory

- [ ] Create the global shard in the documented per-user data location.
- [ ] Give the global shard its own database, indexes, lock, and migrations.
- [ ] Require explicit scope and rationale for global writes and promotions.
- [ ] Implement reviewed `promote_memory_to_global` without deleting the local
      source record.
- [ ] Allow `recall_memory` to include global memory only when requested.
- [ ] Return project and global records in visibly separate groups.
- [ ] Implement explicit local-rule override of a global rule while returning
      both records and the precedence reason.
- [ ] Prohibit automatic global inference and scans across project stores.
- [ ] Test global failure without project fallback and vice versa.

Gate:

- [ ] Global state contains only deliberate shared records, local overrides are
      deterministic, and no project's local records are exposed to another.

## Milestone 5 — Direct relations and bounded path search

- [ ] Implement the small reviewed relation-type registry with directionality.
- [ ] Store only direct, justified, versioned edges with rationale/provenance.
- [ ] Merge duplicate edges without multiplying them.
- [ ] Mark affected relations for review after memory revision.
- [ ] Implement `find_relation_paths` with hop, neighbor, node, and result caps.
- [ ] Use direction-aware bidirectional search where valid.
- [ ] Never materialize transitive closure or inferred shortcut edges.
- [ ] Return complete edges, conditions, rationale, hop count, and `truncated`.
- [ ] Test causal wording is not strengthened during traversal.

Gate:

- [ ] Searches terminate within every configured bound and report either an
      explained path or "no path found within configured limits."

## Milestone 6 — Activation and bounded growth

- [ ] Implement idempotent `record_memory_use` with an operation ID.
- [ ] Ensure retrieval alone never increments activation.
- [ ] Add bounded usage rollups and configurable class capacities.
- [ ] Implement explicit validity and expiry without calendar-age trust decay.
- [ ] Add reviewed consolidation of repeated episodes.
- [ ] Implement `compact_memory` as dry-run by default with exact consequences.
- [ ] Protect active rules, pinned records, and open conflicts from automatic
      removal.
- [ ] Add index-generation and temporary-artifact garbage collection.
- [ ] Test bounded growth under repeated writes, searches, and retries.

Gate:

- [ ] Storage remains bounded under the configured policy and every destructive
      action is explicit, previewed, transactional, and auditable.

## Milestone 7 — Portability and optional UI

- [ ] Finalize the stable Markdown export schema and version marker.
- [ ] Implement staged import of compatible exports with validation, preview,
      deduplication, conflict review, and rollback.
- [ ] Never let import blindly overwrite canonical state or activate a conflict.
- [ ] Decide through evidence whether a lossless JSON backup format is needed.
- [ ] Evaluate a loopback-only shared UI adapter using only public MCP tools.
- [ ] If justified, expose project selection, recall, rules, review queues,
      conflicts, revisions, export/import, and maintenance safely.
- [ ] Test export/import portability across machines and project-ID conflicts.

Gate:

- [ ] Humans can inspect exports, imports reconcile rather than overwrite, and
      any UI preserves the same project, scope, and user-authority contracts.

## Optional work after the core design passes

- [ ] Evaluate MCP sampling as an optional proposal convenience.
- [ ] Evaluate reviewed import of arbitrary Markdown prose.
- [ ] Evaluate GPU embeddings only after CPU profiling justifies them.
- [ ] Evaluate alternative vector backends behind the same contract.
- [ ] Design an explicit user-authorized cross-project consolidation report;
      never silently scan all projects.

## Release checklist

- [ ] Ruff, formatting, type checks, unit tests, security tests, and real stdio
      integration pass.
- [ ] Offline CPU operation passes after dependencies/models are cached.
- [ ] README tool/configuration examples match the implemented release exactly.
- [ ] AGENT_GUIDE matches server instructions and all tool descriptions.
- [ ] AGENTS reflects the actual architecture and supported commands.
- [ ] UltraRAG version/commit, license, attribution, and independent-project
      disclaimer are correct.
- [ ] No project data, model binaries, logs, databases, or generated indexes are
      committed.
