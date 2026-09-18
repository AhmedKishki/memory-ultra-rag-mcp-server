# AGENTS.md

This is the engineering guide for agents working in this repository. Read it
with `PLAN.md` and execute work in the order defined by `TODO.md`.

## Project status

Memory UltraRAG MCP is in the design phase. There is currently no package,
server executable, database schema, or implemented MCP tool. Do not describe a
planned interface as working software.

Current files:

- `README.md`: user-facing purpose, expected behavior, and status;
- `PLAN.md`: complete end-state architecture and invariants;
- `TODO.md`: ordered implementation checklist and acceptance gates;
- `AGENT_GUIDE.md`: runtime behavior expected from an AI agent; and
- `AGENTS.md`: engineering rules for this repository.

## Product boundary

This server preserves operational continuity between AI-agent sessions. It
stores rules, status, progress, preferences, style, tone, formatting, decisions,
commitments, constraints, procedures, outcomes, and open questions.

It is not a source-document RAG, conversation archive, general factual knowledge
base, or quotation system. Avoid feature creep that duplicates document
ingestion, bibliographic indexing, evidence retrieval, or answer generation.

The agent proposes durable meaning. The server validates and manages state. The
user is authoritative over ambiguous conflicts, destructive operations, and
global rules.

## Non-negotiable invariants

1. Project-local content never crosses project boundaries.
2. Every project's complete state lives under `<project>/.memory-rag/`; do not
   create a second project-state tree.
3. Global memory is a physically separate, deliberate shard, never a union or
   scan of project databases.
4. SQLite is authoritative. Lexical, dense, and graph indexes are rebuildable.
5. Active rules are operative state and are returned separately from ranked
   memories.
6. An active rule stays operative until an atomic committed replacement or
   explicit retraction.
7. Similarity never establishes identity, truth, contradiction, causality, or
   trustworthiness.
8. Contradictions are not silently resolved; the user receives exactly the
   three choices defined in `PLAN.md`.
9. Retrieval alone does not increase activation.
10. Store only direct justified relations; never materialize transitive closure.
11. Index failure cannot corrupt canonical memory or expose partial state.
12. Upstream UltraRAG source is never patched by this project.

## Incremental delivery rule

Implement one `TODO.md` milestone at a time and pass its gate before beginning
the next. Do not add speculative abstractions for embeddings, graph traversal,
global state, compaction, import, or UI during the local storage milestones.

Within a milestone, prefer the smallest end-to-end implementation over broad
scaffolding. Every dependency, table, module, index, and background process must
serve an accepted requirement and have a test.

Do not expose placeholder MCP tools. A listed tool becomes public only when its
complete behavior, field descriptions, annotations, failure modes, and stdio
integration are tested.

## Responsibility boundary

The accepted boundary is [ADR 0001](docs/decisions/0001-ultrarag-boundary.md):

- use FastMCP directly for Milestones 1 and 2;
- do not add base UltraRAG merely to subclass `UltraRAG_MCP_Server`;
- use UltraRAG's documented MCP/stdio architecture and conventions;
- evaluate the unmodified UltraRAG retriever over MCP first in Milestone 3;
- never import repository-internal retriever or index-backend modules; and
- defer pipeline/UI integration until a public interface satisfies the contract.

This repository owns:

- safe project registration and session binding;
- `.memory-rag` storage and SQLite migrations;
- record types, scope, lifecycle, revisions, and provenance;
- rule applicability and precedence;
- proposal/reconciliation and conflict state;
- deliberate global memory;
- direct relations and bounded path search;
- activation, retention, compaction, and portability; and
- public MCP descriptions and agent instructions.

Before adding custom dense retrieval code, test the UltraRAG retriever MCP
adapter required by ADR 0001. Reject it only with recorded contract or benchmark
evidence and amend the ADR. Do not use private imports merely to reduce local
code. Pin the supported upstream version/commit before implementing the adapter.

## Storage model

The project-local target layout is:

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

Use `pathlib.Path`, canonicalize configured roots, validate boundaries before
opening files, and reject traversal and symlink escape. Ordinary MCP tools use
the session-bound project and accept no arbitrary storage paths.

Keep model binaries in an OS user cache. Never place project records, query
content, indexes, exports, or content-bearing logs in a shared cache.

The global shard uses its own database, indexes, lock, and migrations in a
documented per-user application-data directory. Never fall back from one scope's
store to another.

## Data and transaction rules

- Use forward-only, versioned SQLite migrations, foreign keys, and explicit
  transactions.
- Serialize writes with process and cross-process locks.
- Preserve immutable revision history for corrections and supersession.
- Use soft lifecycle transitions by default; destructive deletion is explicit
  and auditable.
- Keep fingerprints limited to exact duplicate detection.
- Deduplicate same-origin provenance and idempotent operation retries.
- Build derivative indexes in staging and switch a verified pointer atomically.
- Return stale/index health clearly when canonical writes succeed but derivative
  indexing fails.
- Keep logs concise and avoid full memory content, secrets, or protocol output on
  stdout.

## MCP contract

Use typed schemas with descriptions for every parameter and result field. Tool
descriptions must tell an agent when to call the tool, important preconditions,
and what a result means. Set read-only, destructive, idempotent, and open-world
hints accurately.

The server instructions and `AGENT_GUIDE.md` must remain equivalent. In
particular, they must require project initialization, applicable-rule recall,
atomic proposals, semantic comparison by the agent, user-mediated conflicts,
explicit global scope, and honest bounded-path language.

MCP stdout is reserved for protocol messages. Diagnostics go to stderr or the
project-local log.

## Coding standards

Once scaffolding begins:

- encode the exact direct dependency versions from
  [ADR 0002](docs/decisions/0002-dependency-pins.md) and commit `uv.lock`;
- implement the CPython 3.11–3.12, PEP 621, `src/`, setuptools, and `uv`
  contract from [ADR 0003](docs/decisions/0003-python-and-packaging.md);
- test both supported Python minor versions and use CPython 3.12 as the local
  development default;
- preserve the supported Linux x86-64 target and SQLite FTS5 behavior probe from
  [ADR 0004](docs/decisions/0004-platform-and-fts5.md);
- use type hints on public and internal function signatures;
- prefer small cohesive modules and the standard library where practical;
- use Pydantic only at external/schema boundaries unless evidence supports
  broader use;
- keep database access explicit and testable rather than hiding it behind a
  large generic repository framework;
- keep return values deterministic and JSON-serializable;
- avoid import-time model downloads, database writes, or project selection;
- lazy-load dense models only in the milestone that needs them; and
- do not add multiprocessing or a native-language component without profiling.

Do not add embedding, vector, UI, or autonomous-model dependencies before their
milestone. Direct-pin upgrades require an ADR update and the relevant complete
test gates; transitive versions belong in `uv.lock`, not duplicated lists.

Use `apply_patch` for manual edits. Do not modify generated databases, indexes,
exports, locks, logs, or model caches as source files.

## Testing requirements

Each behavior needs unit tests plus real stdio integration where it crosses the
MCP boundary. At minimum test:

- exact project-name resolution and session binding;
- traversal, symlink, alias ambiguity, malformed IDs, and project isolation;
- migrations, rollback, locking, and crash-safe transactions;
- exact duplicate and idempotency behavior;
- active-rule recall and atomic replacement;
- revision, retraction, and conflict history;
- global opt-in and project-over-global precedence;
- bounded directional graph traversal;
- stale-index and failed-build recovery;
- dry-run compaction and protected records; and
- deterministic export plus staged import security.

Run the format, lint, type, unit, security, and stdio suites named by the package
once those commands are established. Update this file and the README with the
canonical commands rather than inventing alternatives.

## Documentation discipline

- Keep `README.md` self-contained and user-facing. It must state what works now.
- Keep design rationale and end-state constraints in `PLAN.md`.
- Check completed implementation work in `TODO.md` only after its gate passes.
- Keep runtime agent behavior in `AGENT_GUIDE.md` and MCP server instructions.
- Do not duplicate long internal implementation discussions into the README.
- Do not compare or depend on sibling MCP-server repositories in standalone
  documentation.

## Attribution

Credit UltraRAG, its contributors, and the organizations named by the project:
THUNLP, NEUIR, OpenBMB, and AI9stars. Link to the canonical UltraRAG repository
and documentation. Preserve the independent-project/no-endorsement statement,
the root Apache-2.0 `LICENSE`, and the complete root `NOTICE`. Preserve an
upstream snapshot's own license and notices whenever it is downloaded or
redistributed. Any direct dependency or UltraRAG snapshot change requires a
fresh license and attribution review.
