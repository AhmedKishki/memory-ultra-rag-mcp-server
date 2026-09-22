"""UltraRAG's memory behaviour, for one scope directory.

The behaviour, the file names, and the byte-for-byte formats are UltraRAG's, from
``servers/memory/src/memory.py`` at the pinned revision; the reference files are
committed under ``tests/fixtures/upstream``. What this module changes is only
where a scope's directory is: the caller decides that, which is what lets one
project keep its memory inside its own repository while every instance shares
the global one.

Upstream's own constants, kept verbatim:

* the standing document is ``MEMORY.md``, created from a template when it is first
  read;
* a round is appended to ``project/<YYYY-MM-DD>.md``, which starts with a
  ``# Project Memory <date>`` header;
* each round is written as ``## <date> <time>`` followed by the user's line and
  the assistant's line.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

__all__ = [
    "EMPTY_MESSAGE_ERRORS",
    "TEMPLATE",
    "StoreError",
    "append_round",
    "daily_rounds",
    "read_standing",
    "standing_document",
    "user_id_error",
]

#: What upstream writes into a standing document it has to create.
TEMPLATE = "# MEMORY\ni am jack. i like LLMs.\n"

STANDING_FILENAME = "MEMORY.md"
ROUNDS_DIRNAME = "project"

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

EMPTY_MESSAGE_ERRORS = (
    "user_message cannot be empty.",
    "assistant_message cannot be empty.",
)


class StoreError(ValueError):
    """Raised when a scope or a round cannot be written as upstream writes it."""


def user_id_error(user_id: str) -> str | None:
    """Return upstream's error text for an unusable ``user_id``, else None.

    Upstream accepts letters, digits, ``_`` and ``-``; the same shape is also what
    keeps a scope inside the storage tree, since the identifier is a directory
    name.
    """
    normalized = str(user_id or "default").strip() or "default"
    if not USER_ID_PATTERN.fullmatch(normalized):
        return "Invalid user_id format. Only letters, numbers, '_' and '-' are allowed."
    return None


def standing_document(scope_directory: Path) -> Path:
    """Return the standing document of one scope."""
    return scope_directory / STANDING_FILENAME


def daily_rounds(scope_directory: Path) -> Path:
    """Return the directory holding one scope's dated rounds."""
    return scope_directory / ROUNDS_DIRNAME


def read_standing(scope_directory: Path) -> str:
    """Return one scope's standing document, creating it as upstream does.

    Upstream creates the directory and the file from the template on the first
    read, so the first read of a scope also initializes it.
    """
    scope_directory.mkdir(parents=True, exist_ok=True)
    document = standing_document(scope_directory)
    if not document.exists():
        document.write_text(TEMPLATE, encoding="utf-8")
    return document.read_text(encoding="utf-8")


def append_round(
    scope_directory: Path,
    q_ls: list[str],
    ans_ls: list[str],
    now: datetime | None = None,
) -> Path:
    """Append one round to a scope, exactly as upstream writes it.

    Returns the file the round was written to. The first round of a day creates
    the file with upstream's header; later rounds append to it.
    """
    user_text = str(q_ls[0] or "").strip() if q_ls else ""
    assistant_text = str(ans_ls[0] or "").strip() if ans_ls else ""
    if not user_text:
        raise StoreError(EMPTY_MESSAGE_ERRORS[0])
    if not assistant_text:
        raise StoreError(EMPTY_MESSAGE_ERRORS[1])

    read_standing(scope_directory)
    rounds = daily_rounds(scope_directory)
    rounds.mkdir(parents=True, exist_ok=True)

    # Upstream stamps a round with local time and writes no zone; that stamp is
    # part of the format this package reproduces, so the call is the same one.
    moment = now if now is not None else datetime.now()  # noqa: DTZ005
    date_str = moment.strftime("%Y-%m-%d")
    time_str = moment.strftime("%H:%M:%S")
    daily_file = rounds / f"{date_str}.md"

    entry = (
        f"\n## {date_str} {time_str}\n"
        f"- user: {user_text}\n"
        f"- assistant: {assistant_text}\n"
    )
    if not daily_file.exists():
        daily_file.write_text(f"# Project Memory {date_str}\n{entry}", encoding="utf-8")
    else:
        with daily_file.open("a", encoding="utf-8") as handle:
            handle.write(entry)
    return daily_file
