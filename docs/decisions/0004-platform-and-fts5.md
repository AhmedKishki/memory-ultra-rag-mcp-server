# ADR 0004: Initial Platform Matrix and SQLite FTS5

Status: proposed pending the first complete CI matrix

Date: 2026-09-18

## Supported platform matrix

The initial package supports these 64-bit CPython targets:

| Operating system | Architecture | CI representative |
| --- | --- | --- |
| Linux | x86-64 | Ubuntu 24.04 x64 |
| Windows | x86-64 | Windows Server 2025 x64 |
| macOS | arm64 | macOS 15 Apple silicon |
| macOS | x86-64 | macOS 15 Intel |

Each target must pass on CPython 3.11 and 3.12. Linux arm64, Windows arm64,
32-bit systems, PyPy, and other operating systems are unsupported until their
complete dependency and behavior matrices pass. They may work, but the project
does not promise or silently infer support.

The CI operating-system versions are repeatable representatives, not minimum
end-user OS versions. Minimum OS versions will follow the supported CPython and
dependency wheels and must be documented before the first release.

## Required FTS5 behavior

The base memory server uses the `sqlite3` module from the selected CPython
installation. It does not download a SQLite binary, load an extension, or add a
second lexical-search dependency.

On every supported target, SQLite must be able to:

1. create an FTS5 virtual table with the built-in `unicode61` tokenizer;
2. insert and phrase-match text;
3. execute a prefix query;
4. return a `bm25()` score; and
5. update the index so removed terms disappear and replacement terms appear.

`PRAGMA compile_options` reporting `ENABLE_FTS5` is recorded for diagnosis, but
actual SQL behavior is authoritative. Some SQLite builds can provide a feature
without exposing an identical compile-option string.

The pure-standard-library probe is
[`scripts/check_sqlite_fts5.py`](../../scripts/check_sqlite_fts5.py). The
[`platform-prerequisites` workflow](../../.github/workflows/platform-prerequisites.yml)
runs it across all eight OS/architecture/Python combinations. The same probe is
available locally:

```bash
python scripts/check_sqlite_fts5.py
```

## Failure policy

Milestone 1 must run this check before creating a project's database. If FTS5 is
missing or fails its behavior check, initialization stops with a concise error
containing the Python version, SQLite version, platform, and remediation link.
It must not create a partially usable project, silently replace lexical search,
or fall back to a database belonging to another project.

Dependency, Python, runner-image, or platform-matrix changes must rerun the
probe. A new target becomes supported only after its complete matrix passes.

## Evidence

The decision becomes accepted only after every job in the first GitHub Actions
matrix passes. Record the workflow URL, exact interpreter and SQLite versions,
and local Linux result here before checking the TODO item complete.
