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

import hashlib
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

__all__ = [
    "EMPTY_MESSAGE_ERRORS",
    "TEMPLATE",
    "StoreError",
    "append_round",
    "count_rounds",
    "daily_rounds",
    "latest_round_date",
    "list_rounds",
    "read_standing",
    "standing_digest",
    "standing_document",
    "user_id_error",
    "write_standing",
]

#: What upstream writes into a standing document it has to create.
TEMPLATE = "# MEMORY\ni am jack. i like LLMs.\n"

STANDING_FILENAME = "MEMORY.md"
ROUNDS_DIRNAME = "project"

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

#: The round heading upstream writes, which is also how a round is recognized.
ROUND_HEADING_PATTERN = re.compile(r"^## (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})$")

USER_LINE_PREFIX = "- user: "
ASSISTANT_LINE_PREFIX = "- assistant: "

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


def standing_digest(scope_directory: Path) -> str | None:
    """Return the digest of one scope's standing document, or None if absent."""
    document = standing_document(scope_directory)
    if not document.is_file():
        return None
    return hashlib.sha256(document.read_bytes()).hexdigest()


def count_rounds(scope_directory: Path) -> int:
    """Count one scope's rounds without reading their text."""
    rounds = daily_rounds(scope_directory)
    if not rounds.is_dir():
        return 0
    total = 0
    for path in sorted(rounds.glob("*.md")):
        with path.open(encoding="utf-8") as handle:
            total += sum(1 for line in handle if line.startswith("## "))
    return total


def latest_round_date(scope_directory: Path) -> str | None:
    """Return the newest date one scope has a round for, or None."""
    rounds = daily_rounds(scope_directory)
    if not rounds.is_dir():
        return None
    dates = sorted(path.stem for path in rounds.glob("*.md"))
    return dates[-1] if dates else None


def list_rounds(
    scope_directory: Path,
    limit: int = 20,
) -> tuple[list[dict[str, str]], bool]:
    """Return a scope's newest rounds first, and whether older ones were left out.

    Only rounds written in the format ``append_round`` writes are recognized: a
    ``## <date> <time>`` heading followed by the user's line and the assistant's
    line. Text under a heading that carries neither is kept with the round, so a
    message that wrapped onto a second line survives the round trip.
    """
    rounds = daily_rounds(scope_directory)
    if not rounds.is_dir():
        return [], False

    selected: list[dict[str, str]] = []
    truncated = False
    for path in sorted(rounds.glob("*.md"), key=lambda item: item.stem, reverse=True):
        parsed = _parse_rounds(path.read_text(encoding="utf-8"), path.name)
        for entry in reversed(parsed):
            if len(selected) >= limit:
                truncated = True
                break
            selected.append(entry)
        if truncated:
            break
    return selected, truncated


def _parse_rounds(text: str, source_file: str) -> list[dict[str, str]]:
    """Return the rounds one daily file holds, oldest first."""
    parsed: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    field: str | None = None

    for line in text.splitlines():
        heading = ROUND_HEADING_PATTERN.match(line)
        if heading:
            current = {
                "date": heading.group(1),
                "time": heading.group(2),
                "user": "",
                "assistant": "",
                "source_file": source_file,
            }
            parsed.append(current)
            field = None
            continue
        if current is None:
            continue
        for name, prefix in (
            ("user", USER_LINE_PREFIX),
            ("assistant", ASSISTANT_LINE_PREFIX),
        ):
            if line.startswith(prefix):
                current[name] = line[len(prefix) :]
                field = name
                break
        else:
            if field is not None and line.strip():
                current[field] = f"{current[field]}\n{line}"
    return parsed


def write_standing(
    scope_directory: Path,
    content: str,
    *,
    expected_sha256: str | None = None,
) -> str:
    """Replace one scope's standing document, returning its new digest.

    This write is this package's own: upstream only creates the document from its
    template, and the agent tools never replace it. The file is replaced
    atomically, and when ``expected_sha256`` is given it must match the bytes on
    disk, so a document that changed since it was read is never overwritten.
    """
    if not content.strip():
        raise StoreError("standing memory must not be empty")

    scope_directory.mkdir(parents=True, exist_ok=True)
    document = standing_document(scope_directory)
    current = standing_digest(scope_directory)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if current == digest:
        return digest
    if expected_sha256 is not None:
        if current is None:
            raise StoreError(
                "the standing document no longer exists; read it again before saving"
            )
        if current != expected_sha256:
            raise StoreError(
                "the standing document changed since it was read; read it again "
                "before saving"
            )

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=scope_directory,
        prefix=".MEMORY-",
        suffix=".md",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, document)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return digest
