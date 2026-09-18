# ADR 0001: UltraRAG Reuse Boundary

Status: accepted

Date: 2026-09-18

## Context

Memory UltraRAG MCP must genuinely reuse UltraRAG where it satisfies the
product contract, while keeping the first local-memory release small, headless,
project-isolated, and free of unrelated work.

The preceding [API audit](../audits/ultrarag-api-audit.md) found that:

- `UltraRAG_MCP_Server` is a documented installed API;
- constructing it creates a relative `logs/` directory and automatically
  registers a pipeline `build` tool;
- the existing UltraRAG memory server is append-only Markdown and lacks the
  required ledger semantics;
- retriever and index-backend source modules are not installed as importable
  package components; and
- the retriever is publicly usable as an MCP server from a verified UltraRAG
  source snapshot.

### Measured dependency cost

A clean-target installation was measured with CPython 3.12.3 and `uv`:

| Runtime | Installed distributions | Target size |
| --- | ---: | ---: |
| FastMCP 3.4.0 | 68 | 64 MiB |
| UltraRAG commit `3a709a2` base dependencies | 117 | 310 MiB |

These figures are comparison measurements, not release pins. The UltraRAG
installation includes FastMCP plus unrelated corpus, UI, HTTP, document,
tabular, and Milvus dependencies. Warm construction in the existing environment
showed little runtime difference (approximately 0.64 seconds for FastMCP and
0.69 seconds for the UltraRAG wrapper, including `uv run` overhead); installation
surface and side effects are the deciding costs.

## Decision

### Base server

Milestones 1 and 2 use FastMCP directly for the stdio MCP server. They do not
install the UltraRAG package merely to subclass `UltraRAG_MCP_Server`.

This avoids:

- roughly 246 MiB and 49 unrelated installed distributions in the measured
  environment;
- a process-start `logs/` directory outside `.memory-rag/`;
- an irrelevant agent-visible `build` tool; and
- pipeline output metadata that a direct MCP client does not consume.

The base server follows UltraRAG's documented MCP module pattern—typed tools,
stdio transport, explicit descriptions, and modular retrieval—but uses the
underlying FastMCP public interface because it is the smallest interface that
actually satisfies the requirement.

### Concrete UltraRAG runtime reuse

Milestone 3 must evaluate UltraRAG's unmodified retriever server as the first
dense-candidate implementation. The integration boundary is MCP, not imports
from `servers/retriever/src`.

The intended adapter will:

1. acquire and verify one pinned official UltraRAG source snapshot without
   requiring a user Git clone;
2. run only the retriever server in a managed subprocess;
3. keep corpus and index paths beneath the active project's `.memory-rag/`;
4. encode stable memory IDs into candidate records and resolve returned
   candidates against SQLite;
5. treat returned order as retrieval rank, never truth or identity; and
6. expose stale/failure state without changing canonical memory.

The semantic milestone may reject this adapter only with recorded tests showing
that the public retriever MCP contract cannot meet project isolation, stable-ID
mapping, CPU latency, update cost, or reproducible offline operation. Such a
rejection must amend this ADR before custom dense retrieval is introduced.

SQLite FTS5 remains the lexical engine for the local-memory MVP. Running
UltraRAG BM25 alongside it would duplicate an index, require a source runtime,
and weaken transactional add/correct/retract behavior without adding a needed
capability.

### UltraRAG components not reused

| Component | Decision | Reason |
| --- | --- | --- |
| `UltraRAG_MCP_Server` wrapper | Do not use in the base server | Dependency footprint, relative log creation, and automatic `build` tool provide no required memory behavior |
| `ultrarag.api.ToolCall` and `PipelineCall` | Do not use for normal operation | Require an UltraRAG server source tree and generated configuration |
| Upstream memory server | Do not reuse | Stores dialogue in Markdown; no typed ledger, rules, isolation, revisions, or conflict workflow |
| Retriever backend Python classes | Never import | Repository-internal and absent from the installed package |
| UltraRAG BM25 | Do not use for the MVP | Duplicates SQLite FTS5 and does not share its transactional lifecycle |
| UltraRAG retriever MCP server | Evaluate first in Milestone 3 | Public process boundary can provide CPU dense candidates without private imports |
| UltraRAG pipeline runner and UI | Defer | Neither is needed for the first complete memory slices |

## Necessary custom components

Each custom component exists because the reviewed UltraRAG snapshot has no
public component with the required contract:

| Custom component | Why it is necessary |
| --- | --- |
| Project registry and session binding | UltraRAG tools accept paths/configuration but do not enforce exact registered-name selection and physical project isolation |
| `.memory-rag` storage policy | UltraRAG has no single-directory project memory boundary |
| SQLite ledger and migrations | Memory requires transactional typed records, revisions, provenance, and auditable lifecycle state rather than appended dialogue |
| Rule applicability and precedence | Rules must bypass similarity ranking, remain operative during review, and support explicit project-over-global overrides |
| Proposal/reconciliation workflow | Exact or semantic similarity cannot decide new/equivalent/refinement/contradiction dispositions |
| Conflict workflow | Contradictions require pending state and an exact user-mediated three-choice resolution |
| SQLite FTS5 adapter | Provides immediate incremental lexical recall in the same authoritative transaction domain |
| Dense-retriever adapter | Maps UltraRAG candidates to stable SQLite IDs, project paths, generation state, and failure reporting |
| Direct relation ledger and bounded traversal | UltraRAG retrieval does not store typed directional edges or enforce graph-search budgets |
| Activation and retention policy | Access frequency, confirmed use, expiry, protected rules, and dry-run compaction are memory-specific semantics |
| Markdown export/import reconciliation | Human-readable transfer must preserve IDs/lifecycle and stage conflicts rather than ingest prose as chunks |

## Consequences

- The first working server can install and start without UltraRAG model or
  corpus dependencies.
- The project does not claim that FastMCP itself is an UltraRAG component; it
  credits UltraRAG for the architecture and reserves concrete runtime reuse for
  the retriever milestone.
- No UltraRAG source is copied, patched, or imported through private paths.
- The dense retrieval dependency remains absent until it has a tested use.
- If the released product ultimately contains no executable UltraRAG component,
  its README and name must be reviewed so they do not imply otherwise.

## Revisit conditions

Revisit this decision if UltraRAG publishes:

- a lightweight MCP-server package without the logging/build side effects;
- installed retriever library APIs with stable IDs, scores, and filters; or
- a memory ledger matching the isolation, lifecycle, and conflict contract.
