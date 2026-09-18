# ADR 0002: Initial Dependency Pins

Status: accepted

Date: 2026-09-18

## Scope

This decision pins the direct runtime and development dependencies needed for
Milestones 1 and 2, plus the deferred UltraRAG source snapshot required by
[ADR 0001](0001-ultrarag-boundary.md).

It does not select an embedding model, vector backend, UI package, or import
model. Those dependencies are prohibited until their own milestone provides
measurements and an accepted use.

## Base runtime pins

| Dependency | Exact version | Required use |
| --- | --- | --- |
| `fastmcp` | `3.4.0` | MCP schemas, tool registration, lifespan, and stdio transport |
| `pydantic` | `2.13.5` | Directly imported public input/output validation models |
| `platformdirs` | `4.11.10` | Cross-platform user configuration, registry, data, state, and cache paths |
| `filelock` | `3.32.7` | Cross-platform inter-process project and registry locks |

These four versions resolve and pass a construction, schema-validation,
platform-path, and lock-acquisition probe together on CPython 3.12.3.

Reasons for the less obvious choices:

- FastMCP `3.4.0` is the already tested 3.x API used in the reviewed environment.
  The project will not absorb the newer 4.x API during initial implementation.
- Pydantic and platformdirs are declared directly because project code will
  import them, even though FastMCP also depends on them transitively.
- Filelock remains on the latest verified 3.x release rather than taking the
  newly released 4.x major version without migration evidence.
- SQLite, JSON, hashing, paths, UUIDs, and dataclasses use the Python standard
  library and add no dependency.

## Development pins

| Dependency | Exact version | Required use |
| --- | --- | --- |
| `pytest` | `9.1.1` | Unit, security, and stdio integration tests |
| `ruff` | `0.16.8` | Formatting and linting |

No type-checker dependency is added until the packaging decision defines the
actual command and demonstrates value beyond runtime type annotations and test
coverage.

## UltraRAG snapshot pin

UltraRAG is not a base dependency. Its Milestone 3 retriever evaluation is
pinned to this official source snapshot:

| Field | Value |
| --- | --- |
| Project | `OpenBMB/UltraRAG` |
| Reported package version | `0.3.0.2` |
| Commit | `3a709a2aea3fbe46acca59c422621c94b6e86857` |
| Archive | `https://github.com/OpenBMB/UltraRAG/archive/3a709a2aea3fbe46acca59c422621c94b6e86857.tar.gz` |
| Archive SHA-256 | `cb7b7b10dd8eacecd43d7e6705baea4cd431710e7016a0b66673c56821accece` |

The archive hash was verified while resolving the official archive. The exact
commit is authoritative because the upstream `v0.3.0.2` tag points to a
different commit and therefore does not identify the audited retriever code.

The retriever adapter must not follow `main`, a mutable version range, or an
unpinned model artifact. Updating the snapshot requires a new API audit,
license review, integration tests, and an explicit amendment to this ADR.

## Enforcement

Milestone 1 scaffolding must encode the exact base and development versions in
`pyproject.toml` and commit the generated `uv.lock`. The lock file is the
authority for transitive versions and artifact hashes. Normal installation and
CI use `uv sync --frozen`.

The UltraRAG archive URL and hash must have one runtime source of truth when the
Milestone 3 adapter is implemented; do not duplicate them across modules.

Dependency upgrades are deliberate maintenance changes. An upgrade must:

1. explain the need;
2. regenerate the lock;
3. run formatting, lint, unit, security, and real stdio tests;
4. repeat relevant isolation and offline tests; and
5. update this ADR when a direct pin or the UltraRAG snapshot changes.

## Consequences

- The local-memory MVP has four direct runtime dependencies and no embedding or
  vector stack.
- Direct dependency drift is prevented before implementation begins.
- Transitive pins are intentionally deferred to the generated lock rather than
  copied into documentation.
- Supported Python versions and the build backend remain the next separate
  packaging decision.
