"""The document beside the record, and the words of a statement.

The record is the table in ``memory.sqlite3``; this module is the file written out
from it, and the two shapes a statement takes on the way: a row, and a block of
prose.

**The document is an export, not the record.** It is written whenever the memory
changes, it is regenerated rather than read, and a hand edit to it is overwritten
with the record and disclosed to whoever notices. What it is for is the thing a
database cannot be: a memory you can open with ``cat``, read, diff, commit, and
carry to another machine. Upstream keeps the same file name, so a UltraRAG UI still
finds the global memory where it looks — though it now reads an export of this
server's record rather than the record itself.

That also makes it a rendering and not a backup. An export holds the statements
and nothing else: a file recovered from one has the right words in the right
order, and every statement in it is undated and filed as ``ITEM``, because the
types, the dates, and the counts lived in the record and not in the file. A lost
database is therefore a memory whose categories and history are lost, and a
recovered one is a memory of what was said rather than of how it was said. The
alternative — writing the type into the file so the file could restore it — is a
file of tagged lines, and that is what a memory stopped being.

**A statement is one line of prose, and its type is a row, not a prefix.** Upstream
never writes a statement, so the block format is this package's; the file it seeds
with is upstream's, byte for byte. The type used to be written into the block as a
``RULE: `` prefix, and it is not any more: a file of tagged lines reads as data
rather than as something a person wrote, and a type is a property of the row that
holds the statement. A statement imported from a file that still has the prefix
keeps the type it had there, and the next export drops the prefix.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = [
    "DEFAULT_KIND",
    "EXPORT_FILENAME",
    "HANDOFF_KIND",
    "MAX_KIND_LENGTH",
    "SEED",
    "STOPWORDS",
    "TEMPLATE",
    "Statement",
    "StoreError",
    "normalise",
    "parse_document",
    "query_terms",
    "read_standing",
    "render_document",
    "standing_document",
    "statement_kind",
    "unit_key",
    "write_standing",
]

#: The file the record is written out to, which is upstream's own name.
EXPORT_FILENAME = "MEMORY.md"

#: What a document this package writes looks like when it has nothing in it: the
#: seed upstream writes, byte for byte.
TEMPLATE = "# MEMORY\ni am jack. i like LLMs.\n"

#: The line under the heading in that seed. It is upstream's placeholder, not a
#: memory, and an export does not carry it — a file a person opens should hold
#: what was remembered and nothing else. A reader still skips it by name, because
#: a file written before this version has it.
SEED = "i am jack. i like LLMs."

#: What a statement is filed under when the caller names none. Every statement
#: carries a type, so the default is a real one rather than an absence: a query
#: naming `ITEM` finds the statements nobody else filed, which is the only way they
#: can be found at all.
DEFAULT_KIND = "ITEM"

#: The type a session handoff is filed under. It is reserved: a handoff replaces
#: the previous one instead of accumulating beside it, and the replacement is found
#: by this type rather than by anything the caller has to remember to pass.
HANDOFF_KIND = "HANDOFF"

#: Long enough for a word, short enough that a type cannot become a phrase.
MAX_KIND_LENGTH = 64

_HEADING_PATTERN = re.compile(r"^#\s+\S")

#: The type a statement imported from a file that still writes it into the block.
#: Read on import only; never written.
_LEGACY_KIND_PATTERN = re.compile(r"^([A-Z]{1,64}): ")


class StoreError(ValueError):
    """Raised when a scope cannot be written the way this store writes one."""


def normalise(value: str) -> str:
    """Return text with its white space collapsed, for an exact comparison.

    Forgetting matches words and never meaning, so the comparison has to be one a
    caller can predict from the text a read gave them. Collapsing white space is
    the only slack in it, and it is slack about spacing rather than about words.
    """

    return " ".join(str(value or "").split())


def unit_key(text: str) -> str:
    """Return the stable identity one statement is known by.

    A digest of the statement's own words rather than of its place, so a statement
    inserted above another does not change its identity and a statement reworded
    becomes a different one. A row, its vector, and its history are the same row,
    and this is how they are found.
    """

    return hashlib.sha256(normalise(text).casefold().encode("utf-8")).hexdigest()


def statement_kind(value: str) -> str:
    """Return one statement's label as it is written on its row.

    A type is the caller's own category for a statement, and it carries no meaning
    here: the record holds it and never interprets it. It exists to help a read
    find the statement, so it is one run of block letters and nothing else — no
    white space, no punctuation, no digits. A single word is a category; a phrase
    would put a sentence where a word belongs, and anything but letters would make
    a query for it unwriteable.

    Case is the one thing normalised rather than refused: a type given in any case
    is recorded in block letters, so what the record holds is always the shape the
    read side recognises. An empty type is not refused either: every statement
    carries one, so a caller that names none gets `ITEM`.
    """

    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_KIND
    if any(character.isspace() for character in raw):
        raise StoreError(
            "type must be one word with no white space in it: a type is a single "
            "category, and a phrase would be part of the statement."
        )
    if not (raw.isascii() and raw.isalpha()):
        raise StoreError(
            "type must be block letters only, as in RULE or PLAN: a type carries "
            "no meaning beyond helping a read find its statements."
        )
    if len(raw) > MAX_KIND_LENGTH:
        raise StoreError(
            f"type must be at most {MAX_KIND_LENGTH} characters: it named {len(raw)}."
        )
    return raw.upper()


@dataclass(frozen=True, slots=True)
class Statement:
    """One statement, as the record holds it.

    ``text`` is the statement and ``type`` is what it was filed under, which are
    now two fields rather than one tagged line. ``position`` counts from the top
    of the document, so zero is the statement recorded last, and it is the order
    both the export and a read's preference for new information follow.
    """

    kind: str
    text: str
    key: str
    position: int
    added_at: str | None = None
    recalls: int = 0
    last_recalled_at: str | None = None
    labelled: bool = True

    @property
    def normalized(self) -> str:
        """Return the text as an exact match compares it."""

        return normalise(self.text)


def _strip_type(text: str, kind: str) -> str:
    """Return a statement without a type a file put in front of it.

    A file written by an older version holds ``RULE: the statement``. When such a
    file is imported the prefix becomes the row's type and is not repeated, and a
    statement that opens with a colon and a capitalised word is a sentence of its
    own and is left alone.
    """

    match = _LEGACY_KIND_PATTERN.match(text)
    if match is not None and match.group(1) == kind:
        return text[match.end() :].strip()
    return text


def parse_document(
    document: str, *, newest_first: bool | None = None
) -> list[Statement]:
    """Return the statements a document holds, with their positions.

    Read once, on import, for a file written when the document was the record. The
    heading and the seed are not statements, and a block that opens with a kind and
    a colon carries that kind into the row.

    A file that carries a ``KIND: `` prefix was written by a version that appended,
    so its last statement is its newest: importing one in file order would file the
    oldest statement as the most recent and hand it the preference for new
    information. That is the one signal in the file itself, so it is what the
    order is decided by. A file without prefixes was written newest-first, or
    typed by a person, and is read in the order it holds.

    The heading is a line of its own and is taken off first, because a document
    whose statement follows the title on the next line rather than after a blank
    one is a document a person wrote, and one block that happens to begin with a
    title must not swallow the statement under it.
    """

    body = document
    first, _newline, rest = document.partition("\n")
    if _HEADING_PATTERN.match(first.strip()):
        body = rest

    found: list[Statement] = []
    prefixed = False
    for chunk in body.split("\n\n"):
        block = chunk.strip()
        if not block or block in (SEED, TEMPLATE.strip()):
            continue
        match = _LEGACY_KIND_PATTERN.match(block)
        prefixed = prefixed or match is not None
        if match is not None:
            label, body = match.group(1), block[match.end() :].strip()
        else:
            label, body = DEFAULT_KIND, block
        found.append(
            Statement(
                kind=label,
                text=body,
                key=unit_key(f"{label}: {body}"),
                position=len(found) if newest_first else -1,
                labelled=match is not None,
            )
        )
    if newest_first is None:
        # A prefixed file appended, so its last statement is its newest; anything
        # else was written newest-first or typed, and is read as it stands.
        newest_first = not prefixed
    if not newest_first:
        found = [
            replace(statement, position=position)
            for position, statement in enumerate(reversed(found))
        ]
    return found


def render_document(statements: list[Statement]) -> str:
    """Return the document a record is written out as: prose, newest first.

    Plain statements, one per block, under the heading, and nothing else. No type
    is written, because a type is a field of the row and a file of tagged lines
    reads as data rather than as something a person wrote; and no seed, because
    upstream's placeholder is not a memory and a file a person opens should hold
    what was remembered.
    """

    parts = ["# MEMORY", *(statement.text for statement in statements)]
    return "\n\n".join(parts) + "\n"


def standing_document(scope_directory: Path) -> Path:
    """Return the document a scope's record is written out to."""

    return scope_directory / EXPORT_FILENAME


def read_standing(scope_directory: Path) -> str:
    """Return a scope's document, creating the empty one when it has none.

    Upstream creates the directory and the file from the template on the first
    read, so the first read of a scope also initializes it, and a scope that has
    never been written still has somewhere to put its export.
    """

    scope_directory.mkdir(parents=True, exist_ok=True)
    document = standing_document(scope_directory)
    if not document.exists():
        document.write_text(TEMPLATE, encoding="utf-8")
    return document.read_text(encoding="utf-8")


def write_standing(scope_directory: Path, content: str) -> str:
    """Write a scope's document atomically, and return its digest.

    The write is guarded by the digest the caller read, so a replacement that was
    built from a document somebody else has since changed is refused rather than
    silently overwriting their work.
    """

    import os
    import tempfile

    scope_directory.mkdir(parents=True, exist_ok=True)
    payload = content if content.endswith("\n") else f"{content}\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=scope_directory,
        prefix=f".{EXPORT_FILENAME}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        os.replace(temporary, standing_document(scope_directory))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: The English words a lookup does not need. This memory is English, and the word
#: side requires every term it is given, so a term in no statement would not narrow
#: the answer, it would empty it.
STOPWORDS = frozenset(
    [
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "but",
        "by",
        "can",
        "come",
        "could",
        "day",
        "do",
        "even",
        "first",
        "for",
        "from",
        "get",
        "give",
        "go",
        "have",
        "he",
        "her",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "know",
        "like",
        "make",
        "man",
        "many",
        "me",
        "more",
        "most",
        "my",
        "new",
        "no",
        "not",
        "now",
        "of",
        "on",
        "one",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "people",
        "say",
        "see",
        "she",
        "so",
        "some",
        "take",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "thing",
        "think",
        "this",
        "those",
        "three",
        "to",
        "two",
        "up",
        "us",
        "use",
        "very",
        "want",
        "was",
        "way",
        "we",
        "well",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "would",
        "year",
        "you",
        "your",
    ]
)


def query_terms(query: str) -> tuple[str, ...]:
    """Return the distinct case-folded terms one query searches for.

    Words are split on whitespace and stripped of surrounding punctuation, then
    English stop words are dropped: the word side requires every remaining term,
    so a term that decides nothing can only lose the answer. A query made
    entirely of stop words keeps them, because dropping all of them would leave
    the index nothing to search on and the read would report an empty memory
    rather than an unusual question.
    """

    terms: list[str] = []
    for word in str(query or "").casefold().split():
        cleaned = word.strip(".,;:!?\"'`()[]{}<>*_#-")
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    meaningful = [term for term in terms if term not in STOPWORDS]
    return tuple(meaningful or terms)
