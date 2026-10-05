"""``memory-ultra-rag-reindex``: bring a scope's derived state up to date.

A read keeps the word index in step with the record itself, so a memory recovers
from a document left by an older version on its own. What this command still does
is work a read will not: settle a whole scope rather than what one query touches,
embed the vectors a write left pending, and rebuild a vector table that was
deleted or left by another model.

```bash
uv run --frozen memory-ultra-rag-reindex --project-root /path/to/project
```
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from config_ultra_rag_mcp import describe_settings

from . import __version__
from .config import (
    PROJECT_ROOT_ENV_VAR,
    STORAGE_ENV_VAR,
    ConfigurationError,
    resolve_config,
)
from .index import fts5_available
from .maintenance import reindex as rebuild_scopes
from .models import ModelError
from .server import _local_retrieval
from .settings import SETTINGS

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-ultra-rag-reindex",
        description=(
            "Rebuild the search indexes of one project's memory and the account's "
            "global memory, from the Markdown files themselves."
        ),
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help=(
            "Repository whose local memory is rebuilt. Local memory is in "
            f"<project-root>/.memory-rag. Required, or {PROJECT_ROOT_ENV_VAR}."
        ),
    )
    parser.add_argument(
        "--storage-root",
        default=None,
        help=(
            "Where the global memory lives. Defaults to the environment, then "
            f"${STORAGE_ENV_VAR}, then this account's home data directory."
        ),
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help=(
            "Rebuild the word index only, and report what still needs a vector. "
            "Used when the model cannot be fetched."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            "A config.toml whose settings apply to this run, so a memory is "
            "embedded with the models and policy the read will use."
        ),
    )
    parser.add_argument(
        "--set",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        dest="set_overrides",
        help="Override one setting for this run. Repeatable, and the strongest layer.",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print every setting in force and the layer that supplied it, then exit.",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help=(
            "Write each memory out as MEMORY.md, the prose a person reads. The "
            "database is the memory; this is a rendering of it."
        ),
    )
    parser.add_argument(
        "--export-to",
        metavar="PATH",
        default=None,
        help=(
            "Write the rendering to this path instead of the scope's MEMORY.md. "
            "A run over both memories writes one file per scope into a directory "
            "of that name, so name a directory rather than a file."
        ),
    )
    parser.add_argument(
        "--retire-document",
        action="store_true",
        help=(
            "Remove a MEMORY.md that adds nothing to the record. Only asks for the "
            "file to go: one holding a statement the record lacks is read and kept."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def _export(
    scope_directory: Path,
    arguments: argparse.Namespace,
    total: int,
) -> str:
    """Write one scope's memory out as prose, and say where it went.

    One scope writes to the path it was given, whatever its name, because there is
    nothing there for it to overwrite. Several scopes write one file each into a
    directory, because two memories cannot share a rendering file and the local
    scope's own name begins with a dot. ``main`` refuses the one case that cannot be
    decided — a name that reads as a file and is not already a directory.
    """

    from .index import MemoryIndex

    destination = Path(arguments.export_to) if arguments.export_to else None
    if destination is not None and total > 1:
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination / f"{scope_directory.name}.md"
    with MemoryIndex(scope_directory) as index:
        return str(index.write_render(destination))


def main(argv: Sequence[str] | None = None) -> int:
    """Rebuild what a read would otherwise report as behind, and report it."""

    arguments = _parser().parse_args(argv)
    if not arguments.project_root:
        print(
            "memory-ultra-rag-reindex: --project-root is required; point it at "
            "the repository whose memory is rebuilt",
            file=sys.stderr,
        )
        return 2
    try:
        config = resolve_config(
            project_root=arguments.project_root,
            storage_root=arguments.storage_root,
            config_path=arguments.config,
            overrides=arguments.set_overrides,
        )
    except ConfigurationError as error:
        print(f"memory-ultra-rag-reindex: {error}", file=sys.stderr)
        return 2
    if not fts5_available():
        print(
            "memory-ultra-rag-reindex: this Python's SQLite has no FTS5, so the "
            "word index cannot be built; run scripts/check_sqlite_fts5.py",
            file=sys.stderr,
        )
        return 2

    scopes = [config.local_directory, config.global_directory]

    if arguments.print_config:
        # Every setting in force, and where it came from, without rebuilding
        # anything or fetching a model to do it.
        print(
            describe_settings(
                SETTINGS, config.settings.as_values(), config.settings_provenance
            )
        )
        return 0

    # The reindex embeds with the settings the read ranks with: a memory embedded
    # by one model and matched by another is a memory nobody can find anything in.
    retrieval = None if arguments.no_embed else _local_retrieval(config.settings)
    if retrieval is not None:
        try:
            retrieval.warm()
        except ModelError as error:  # pragma: no cover - warm() reports, not raises
            print(f"memory-ultra-rag-reindex: {error}", file=sys.stderr)

    try:
        # `rebuild_scopes` uses the worker only when there is one scope, because a
        # worker writes into the file of the scope it was built for.
        report = rebuild_scopes(
            scopes,
            retrieval.embedder if retrieval is not None else None,
            worker=retrieval.worker_for(scopes[0]) if retrieval is not None else None,
            retire_document=arguments.retire_document,
        )
    except (OSError, ValueError) as error:
        print(f"memory-ultra-rag-reindex: {error}", file=sys.stderr)
        return 2
    finally:
        if retrieval is not None:
            for scope in scopes:
                retrieval.worker_for(scope).drain(timeout=30.0)
            for scope in scopes:
                retrieval.worker_for(scope).stop()

    if arguments.export or arguments.export_to:
        named = Path(arguments.export_to) if arguments.export_to else None
        if (
            named is not None
            and named.suffix
            and not named.is_dir()
            and len(scopes) > 1
        ):
            # A path that is already a directory is one whatever it is called. A path
            # that is not there yet and reads as a file cannot hold two renderings,
            # and making a directory out of a name the caller gave as a file is not
            # what they asked for either.
            print(
                "memory-ultra-rag-reindex: --export-to names one file, but this run "
                "renders both memories; name a directory instead",
                file=sys.stderr,
            )
            return 2
        for scope_directory, scope_report in zip(scopes, report["scopes"].values()):
            written = _export(scope_directory, arguments, len(scopes))
            scope_report["exported_to"] = written

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
