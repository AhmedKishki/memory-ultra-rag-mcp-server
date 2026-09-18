# UltraRAG API Audit

Status: complete for Milestone 0's API-inspection task

Audit date: 2026-09-18

## Scope

This audit answers one question: which interfaces in the reviewed UltraRAG
snapshot are actually public and usable by a standalone, headless memory MCP
server?

Reviewed upstream snapshot:

- repository: <https://github.com/OpenBMB/UltraRAG>
- commit: [`3a709a2aea3fbe46acca59c422621c94b6e86857`](https://github.com/OpenBMB/UltraRAG/tree/3a709a2aea3fbe46acca59c422621c94b6e86857)
- source package version: `0.3.0.2`
- declared Python range: `>=3.11,<3.13`
- declared FastMCP range: `>=3.3.1,<3.4.5`

The commit was inspected in the local UltraRAG checkout and verified against
the `OpenBMB/UltraRAG` `main` ref. The `v0.3.0.2` Git tag currently points to a
different commit, so a version string or tag alone is not a reproducible pin.
Dependency pinning is a separate Milestone 0 decision.

Primary upstream documentation:

- [Server development](https://ultrarag.openbmb.cn/pages/cn/rag_servers/overview)
- [Code integration](https://ultrarag.openbmb.cn/pages/en/develop_guide/code_integration)
- [Retriever server](https://ultrarag.openbmb.cn/pages/cn/rag_servers/retriever)

## Findings

### MCP server API

`ultrarag.server.UltraRAG_MCP_Server` is installed package code and is the
documented way to author an UltraRAG server. It subclasses FastMCP and publicly
supports:

- server instructions and lifespan;
- `tool`, `prompt`, and inherited `resource` registration;
- standard MCP tool annotations and descriptions;
- UltraRAG's additional pipeline `output="inputs->outputs"` metadata; and
- `run(transport="stdio")`.

The exact installed snapshot was probed successfully with UltraRAG `0.3.0.2`
and FastMCP `3.4.0`.

Two behaviors require an explicit reuse decision before scaffolding:

1. Constructing `UltraRAG_MCP_Server` initializes UltraRAG logging, creates a
   relative `logs/` directory, and opens a timestamped file. That default does
   not satisfy this project's single `.memory-rag/` project-state boundary.
2. The constructor automatically registers UltraRAG's pipeline-oriented
   `build` tool. A direct agent-facing memory server does not otherwise need
   that tool, although inherited FastMCP exposes a public `remove_tool` method.

The wrapper is therefore a valid public API, but not yet an unconditional reuse
choice.

### Python integration API

`ultrarag.api.initialize`, `ToolCall`, and `PipelineCall` are installed and
documented public interfaces. They are not suitable for the memory server's
basic runtime because they expect an UltraRAG `servers/` source tree,
`parameter.yaml`, and generated `server.yaml` files at a supplied
`server_root`. That would reintroduce the local UltraRAG checkout/runtime
requirement this standalone server is intended to avoid.

### Upstream memory server

The reviewed `servers/memory/src/memory.py` exposes two tools:

- `get_global_memory`, which reads one user's `MEMORY.md`; and
- `save_memory`, which appends the first user question and assistant answer to a
  date-named Markdown file.

It is useful as an UltraRAG pipeline example, but it does not provide this
project's canonical ledger, project/global physical isolation, typed records,
rules, revisions, duplicate reconciliation, contradiction workflow, or bounded
growth. It also creates files while serving reads. The server source is not
installed in the UltraRAG Python distribution.

Conclusion: inspect and credit it, but do not use it as canonical storage or
import its source implementation.

### Upstream retriever server

The retriever is a public MCP/pipeline component in the UltraRAG source tree. It
supports BM25 and dense retrieval with FAISS, Milvus, or Qdrant backends.
However:

- the `servers/retriever` Python modules are not installed in the UltraRAG
  distribution;
- the documented `ToolCall` route requires an UltraRAG server source tree;
- initialization is stateful and configured around corpus/index paths;
- BM25 and dense search return passage strings, not stable memory IDs with
  component scores, lifecycle state, scope, or rule applicability; and
- the backend classes under `servers/retriever/src/index_backends` are internal
  source modules rather than an installed public library API.

Conclusion: do not import retriever or index-backend source modules. Reconsider
the retriever only at the semantic-retrieval milestone, through a pinned public
MCP boundary and only if it can meet the scored candidate contract without
project-isolation workarounds. SQLite FTS5 remains the planned local-memory MVP.

### Installed package boundary

The reviewed `pyproject.toml` uses setuptools package discovery under `src/`.
An installed-distribution probe found zero `servers/` files. The reusable Python
surface is therefore the `ultrarag` package, not arbitrary modules copied or
imported from the repository's `servers/` directory.

This distinction prevents accidental reliance on a nearby checkout and keeps
the future server reproducible across machines.

## Public API inventory

| Interface | Public evidence | Initial suitability |
| --- | --- | --- |
| `ultrarag.server.UltraRAG_MCP_Server` | Installed and documented | Candidate; logging and automatic `build` tool must be resolved |
| `app.tool`, `app.prompt`, inherited `app.resource` | Installed FastMCP surface and UltraRAG server docs | Suitable MCP primitives |
| `app.run(transport="stdio")` | Installed and documented | Suitable transport entry point |
| `ultrarag.api.ToolCall` / `PipelineCall` | Installed and documented | Not suitable for the standalone basic runtime; requires a server source tree/config |
| Upstream memory tools | Public MCP tools in repository source | Behavior does not meet the product contract |
| Upstream retriever tools | Public MCP tools in repository source | Defer to semantic milestone; output/storage contract is insufficient now |
| Retriever backend Python classes | Repository-internal, not installed | Do not import |

## Inspection result

The first Milestone 0 task is complete. The audit found one plausible direct
reuse surface—the packaged MCP server class—and ruled out private imports from
the memory and retriever source trees.

The resulting reuse decision is recorded in
[ADR 0001](../decisions/0001-ultrarag-boundary.md).
