"""UltraRAG's memory behaviour, for one scope directory.

The behaviour, the file name, and the byte-for-byte format of the standing
document are UltraRAG's, from ``servers/memory/src/memory.py`` at the pinned
revision; the reference file is committed under ``tests/fixtures/upstream``. What
this module changes is only where a scope's directory is: the caller decides
that, which is what lets one project keep its memory inside its own repository
while every instance shares the global one.

Upstream's own constant, kept verbatim: the standing document is ``MEMORY.md``,
created from a template when it is first read.

Upstream also keeps a dated exchange log beside it, at
``project/<YYYY-MM-DD>.md``. This package does not: a memory here is a set of
statements, and when a statement is worth keeping it is recorded as one. Nothing
in this module writes, reads, or looks for a dated file, and a scope is one
``MEMORY.md`` and whatever the search side derives from it.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

__all__ = [
    "MAX_STATEMENT_TYPE_LENGTH",
    "STATEMENT_LABEL_PATTERN",
    "STATEMENT_LABEL_SEPARATOR",
    "STOPWORDS",
    "TEMPLATE",
    "StoreError",
    "append_statement",
    "query_terms",
    "read_standing",
    "standing_digest",
    "standing_document",
    "statement_label",
    "write_standing",
]

#: What upstream writes into a standing document it has to create.
TEMPLATE = "# MEMORY\ni am jack. i like LLMs.\n"

STANDING_FILENAME = "MEMORY.md"

#: Long enough for a word, short enough that a label cannot become a phrase.
MAX_STATEMENT_TYPE_LENGTH = 64

#: What separates a statement's label from its text, on the block's first line.
STATEMENT_LABEL_SEPARATOR = ": "

#: A recorded statement's label: one run of block letters, then the separator. The
#: read path strips a label with this same pattern, so a label written is a label
#: recognised — which is why the store admits no other shape. The separator is
#: written literally because a colon and a space are not special in a pattern.
STATEMENT_LABEL_PATTERN = re.compile(rf"^[A-Z]{{1,{MAX_STATEMENT_TYPE_LENGTH}}}: ")


class StoreError(ValueError):
    """Raised when a scope cannot be written the way this store writes one."""


def standing_document(scope_directory: Path) -> Path:
    """Return the standing document of one scope."""

    return scope_directory / STANDING_FILENAME


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


def statement_label(value: str) -> str:
    """Return one statement's label as it is written on the block's first line.

    A label is the caller's own category for a statement, and it carries no meaning
    here: the store holds it and never interprets it. It exists to help a read find
    the statement, so it is one run of block letters and nothing else — no white
    space, no punctuation, no digits. A single word is a category; a phrase would
    put a sentence where a word belongs, and anything but letters would make the
    boundary between label and statement unreadable.

    Case is the one thing normalised rather than refused: a type given in any case
    is recorded in block letters, so what the document holds is always the shape
    the read side recognises.
    """

    raw = str(value or "").strip()
    if not raw:
        raise StoreError("type must not be empty.")
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
    if len(raw) > MAX_STATEMENT_TYPE_LENGTH:
        raise StoreError(
            f"type must be at most {MAX_STATEMENT_TYPE_LENGTH} characters: it "
            f"named {len(raw)}."
        )
    return raw.upper()


def append_statement(
    scope_directory: Path,
    content: str,
    *,
    statement_type: str,
) -> str:
    """Record one labelled statement in a scope's standing document, with its digest.

    A statement is a plain block of text appended to the curated document, with
    no speaker attached: it is what the caller chose to remember, not something
    the user said and not something an assistant replied. Upstream never writes
    this file, so the shape is this package's, inside upstream's file and format.

    The block is the statement, prefixed by its own label and a colon. The label
    is required, and it is the caller's: this store never checks what one means,
    because the meaning is the caller's to define. It is recorded and not
    returned — a read answers with the statement alone, so the label stays where
    it was written, in the document a person can open.

    The append is a single ``O_APPEND`` write, so a concurrent writer adds its own
    block instead of losing either write. Nothing here replaces the document;
    that is :func:`write_standing`'s job, and it is guarded by a digest.
    """

    statement = str(content or "").strip()
    if not statement:
        raise StoreError("content must not be empty.")
    label = statement_label(statement_type)

    current = read_standing(scope_directory)
    document = standing_document(scope_directory)
    separator = (
        "" if current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
    )
    block = f"{label}{STATEMENT_LABEL_SEPARATOR}{statement}"
    with document.open("a", encoding="utf-8") as handle:
        handle.write(f"{separator}{block}\n")
    return standing_digest(scope_directory) or ""


#: The English words a lookup does not need. This memory is English, and the word
#: side asks FTS5 for *every* query term at once, so a term that appears in no
#: statement does not narrow the answer — it empties it, and the fallback then has
#: to rescue the query by accident. That is the case for a query like "what is
#: research-rag for": no statement contains "what", "is", "the" or "for", so the
#: conjunction finds nothing and the two rarest words stand in for the whole query.
#: Dropping them first leaves "research-rag", which is the query.
#:
#: This is the NLTK-derived, fuller list rather than the short one of thirty-three
#: words, because the wh-words are the ones that hurt here: "what", "who" and "how"
#: carry no signal for a memory of statements, yet the short list keeps them. It is
#: written out here rather than imported, since this package takes no dependency to
#: get a word list. Only the query is filtered; a statement keeps every word it was
#: written with, because the words stored are the words that were remembered.
STOPWORDS = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "aren't",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "cannot",
        "could",
        "couldn't",
        "did",
        "didn't",
        "do",
        "does",
        "doesn't",
        "doing",
        "don't",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "hadn't",
        "has",
        "hasn't",
        "have",
        "haven't",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "isn't",
        "it",
        "its",
        "itself",
        "just",
        "let's",
        "me",
        "more",
        "most",
        "mustn't",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shan't",
        "she",
        "should",
        "shouldn't",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "wasn't",
        "we",
        "were",
        "weren't",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "won't",
        "would",
        "wouldn't",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
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


def standing_digest(scope_directory: Path) -> str | None:
    """Return the digest of one scope's standing document, or None if absent."""
    document = standing_document(scope_directory)
    if not document.is_file():
        return None
    return hashlib.sha256(document.read_bytes()).hexdigest()


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
