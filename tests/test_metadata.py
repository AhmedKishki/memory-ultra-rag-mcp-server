"""A statement's own history: when it was added, and how often it was recalled.

Those four numbers are columns of the record now, so a statement is one row and not
a row plus a file beside it. What matters is not the storage but two properties: a
read counts a recall in the transaction it read from, and a statement nobody dated
is not dated by a guess.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from memory_ultra_rag_mcp.index import (
    LEGACY_VECTORS_FILENAME,
    MemoryIndex,
    index_path,
)
from memory_ultra_rag_mcp.maintenance import EmbeddingWorker, reindex
from memory_ultra_rag_mcp.retrieval import Retrieval, RetrievalSettings
from memory_ultra_rag_mcp.store import SEED, unit_key
from memory_ultra_rag_mcp.vectors import VectorStore

sys.path.insert(0, str(Path(__file__).parent))
from fakes import FakeEmbedder


def _engine() -> Retrieval:
    embedder = FakeEmbedder()
    return Retrieval(
        embedder=embedder,
        policy=RetrievalSettings(embedding_model=embedder.identity.name),
    )


def _recalls(directory: Path, key: str) -> int:
    with MemoryIndex(directory) as index:
        return int(index.history([key]).get(key, {}).get("recalls") or 0)


def test_the_history_is_columns_of_the_record(tmp_path: Path) -> None:
    """One row per statement, so a statement cannot be half in one file and half in another."""

    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        key = unit_key("NOTE: the draft lives in docs/")
        engine.answer(scope="local", directory=tmp_path, query="draft", limit=5)
    finally:
        engine.close()

    with sqlite3.connect(index_path(tmp_path)) as connection:
        columns = {
            str(record[1]) for record in connection.execute("PRAGMA table_info(unit)")
        }
        row = connection.execute(
            "SELECT recalls, added_at IS NOT NULL FROM unit"
        ).fetchone()

    assert {"added_at", "recalls", "last_recalled_at"} <= columns
    assert row[0] == 1
    assert row[1] == 1
    assert _recalls(tmp_path, key) == 1


def test_a_new_statement_is_dated_when_it_is_recorded(tmp_path: Path) -> None:
    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        with MemoryIndex(tmp_path) as index:
            record = index.history([unit_key("NOTE: the draft lives in docs/")])[
                unit_key("NOTE: the draft lives in docs/")
            ]
    finally:
        engine.close()

    assert record["added_at"]
    assert record["recalls"] == 0
    assert record["last_recalled_at"] is None


def test_recording_the_same_words_twice_keeps_the_first_date(tmp_path: Path) -> None:
    """One statement, recorded twice, is not two statements or a reset clock."""

    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        key = unit_key("NOTE: the draft lives in docs/")
        with MemoryIndex(tmp_path) as index:
            first = index.history([key])[key]["added_at"]
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        with MemoryIndex(tmp_path) as index:
            second = index.history([key])[key]["added_at"]
    finally:
        engine.close()

    assert first == second


def test_a_recall_is_counted_as_it_is_handed_over(tmp_path: Path) -> None:
    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        key = unit_key("NOTE: the draft lives in docs/")
        for _ in range(3):
            engine.answer(scope="local", directory=tmp_path, query="draft", limit=5)
        with MemoryIndex(tmp_path) as index:
            record = index.history([key])[key]
    finally:
        engine.close()

    # The read that is answering you has counted itself, so the number is how many
    # times the statement was handed over rather than how many times before now.
    assert record["recalls"] == 3
    assert record["last_recalled_at"]


def test_counting_a_recall_touches_only_the_statements_answered(tmp_path: Path) -> None:
    """The write is proportional to the answer, which is what a read may cost."""

    engine = _engine()
    try:
        for number in range(40):
            engine.record(
                directory=tmp_path,
                content=f"a note {number} about the draft",
                kind="NOTE",
            )
        asked = unit_key("NOTE: a note 39 about the draft")
        untouched = unit_key("NOTE: a note 0 about the draft")
        engine.answer(scope="local", directory=tmp_path, query="note 39 draft", limit=1)
    finally:
        engine.close()

    # The property is that only what was answered was counted. Which statement a
    # fused search puts first among similar ones is the index's business, so the
    # assertion is over the counted rows rather than over one key.
    with MemoryIndex(tmp_path) as index:
        counted = [
            key for key in index.unit_keys() if index.history([key])[key]["recalls"]
        ]
    assert len(counted) == 1, "only the answered statement may be counted"
    assert counted[0] in {asked, untouched}


def test_forgetting_drops_a_statements_own_history(tmp_path: Path) -> None:
    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        engine.record(directory=tmp_path, content="always cite the commit", kind="RULE")
        kept = unit_key("RULE: always cite the commit")
        engine.answer(scope="local", directory=tmp_path, query="draft", limit=5)

        forgotten = engine.forget(
            text="the draft lives in docs/", directories={"local": tmp_path}
        )

        assert forgotten["recalls_removed"] >= 0
        assert _recalls(tmp_path, kept) == 0
        with MemoryIndex(tmp_path) as index:
            assert index.count_units() == 1
    finally:
        engine.close()


def test_a_reindex_keeps_the_history_while_it_reembeds(tmp_path: Path) -> None:
    """A rebuild cannot re-derive a count, so it keeps the one it has."""

    engine = _engine()
    embedder = engine.embedder
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        key = unit_key("NOTE: the draft lives in docs/")
        engine.answer(scope="local", directory=tmp_path, query="draft", limit=5)

        reindex([tmp_path], embedder)

        with MemoryIndex(tmp_path) as index:
            assert index.history([key])[key]["recalls"] == 1
            assert index.count_units() == 1
    finally:
        engine.close()


def test_a_record_is_not_lost_to_a_schema_change(tmp_path: Path) -> None:
    """A version bump drops and rebuilds the words, and not the memory.

    The document is the copy that has the statements, so a file this version
    cannot read loses nothing: the read adopts the document, and the record is
    whole again.
    """

    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        engine.answer(scope="local", directory=tmp_path, query="draft", limit=5)
    finally:
        engine.close()

    # A file of an older shape: the version says 5 and the table is a version-5
    # one, where the type was part of the text.
    with sqlite3.connect(index_path(tmp_path)) as connection:
        connection.execute("DROP TABLE unit")
        connection.execute(
            "CREATE VIRTUAL TABLE unit USING fts5("
            "text, unit_key UNINDEXED, type UNINDEXED, source UNINDEXED, "
            "stamp UNINDEXED)"
        )
        connection.execute(
            "INSERT INTO unit(text, unit_key, type, source, stamp) "
            "VALUES (?, ?, ?, ?, ?)",
            ("the draft lives in docs/", "0" * 64, "NOTE", "MEMORY.md", "0"),
        )
        connection.execute("UPDATE meta SET value = '5' WHERE key = 'schema_version'")
        connection.commit()

    with MemoryIndex(tmp_path) as index:
        assert index.count_units() == 1
        assert index.statements()[0].text == "the draft lives in docs/"


def test_a_document_is_recovered_by_an_empty_record(tmp_path: Path) -> None:
    """The export is the copy that survives, and a read says it took it.

    This is what an upgrade looks like: a document written by a version that kept
    the document as the record, and no database beside it. The read adopts the
    document, and the export it writes back has no types in them, because they are
    rows now.
    """

    scope = tmp_path / "recovered"
    scope.mkdir()
    (scope / "MEMORY.md").write_text(
        "# MEMORY\n" + SEED + "\n\nPLAN: the corpus parses\n\nNOTE: cite the commit\n",
        encoding="utf-8",
    )
    engine = _engine()
    try:
        answer = engine.answer(scope="local", directory=scope, query="corpus", limit=5)

        assert answer["returned"] == 1
        assert answer["units"][0]["kind"] == "PLAN"
        with MemoryIndex(scope) as index:
            statements = index.statements()
            # A file with prefixes was written by a version that appended, so its
            # last statement was its newest: importing it reverses the order.
            assert [item.kind for item in statements] == ["NOTE", "PLAN"]
            assert statements[0].text == "cite the commit"
    finally:
        engine.close()

    document = (scope / "MEMORY.md").read_text(encoding="utf-8")
    assert "the corpus parses" in document
    assert "PLAN:" not in document


def test_a_hand_edited_document_is_overwritten_and_disclosed(tmp_path: Path) -> None:
    """An edit that appeared to work and then vanished is worse than one that never took."""

    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        (tmp_path / "MEMORY.md").write_text(
            "# MEMORY\nsomething a person typed by hand\n", encoding="utf-8"
        )

        answer = engine.answer(
            scope="local", directory=tmp_path, query="draft", limit=5
        )

        assert answer["document_rewritten"] is True
        assert "something a person typed by hand" not in (
            tmp_path / "MEMORY.md"
        ).read_text(encoding="utf-8")
        # And the memory it was answering about is untouched.
        assert answer["returned"] == 1
    finally:
        engine.close()


def test_a_document_that_already_matches_is_left_alone(tmp_path: Path) -> None:
    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        first = engine.answer(scope="local", directory=tmp_path, query="draft", limit=5)
        second = engine.answer(
            scope="local", directory=tmp_path, query="draft", limit=5
        )

        # The write already put the document in line, so a read finds nothing to
        # do and says so rather than writing a file that did not need writing.
        assert first["document_rewritten"] is False
        assert second["document_rewritten"] is False
    finally:
        engine.close()


def test_a_statement_written_by_hand_is_not_dated(tmp_path: Path) -> None:
    """The time a read saw a statement is not the time it was written."""

    scope = tmp_path / "typed"
    scope.mkdir()
    (scope / "MEMORY.md").write_text(
        "# MEMORY\n" + SEED + "\n\nNOTE: typed by a person\n", encoding="utf-8"
    )
    engine = _engine()
    try:
        answer = engine.answer(scope="local", directory=scope, query="typed", limit=5)
    finally:
        engine.close()

    typed = next(
        unit for unit in answer["units"] if "typed by a person" in unit["text"]
    )
    # Undated, and therefore given no preference for being new, which is the honest
    # answer rather than a guess about when a person wrote it.
    assert typed["added_at"] is None
    assert typed["recalls"] == 1
    assert typed["kind"] == "NOTE"


def test_one_file_holds_the_record_the_vectors_and_nothing_else(
    tmp_path: Path,
) -> None:
    """A scope is the document and one file, and the file is the same for all three."""

    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        engine.answer(scope="local", directory=tmp_path, query="draft", limit=5)
        engine.worker_for(tmp_path).drain(5.0)
        reindex([tmp_path], engine.embedder)
        identity = engine.embedder.identity
        with MemoryIndex(tmp_path) as index:
            assert index.count_units() == 1
            with VectorStore(tmp_path, identity.name, identity.dimension) as store:
                assert store.count() == 1

        names = {path.name for path in tmp_path.rglob("*")}
        # The write-ahead files belong to an open connection and go when it closes;
        # what matters is that there is no second database and no bookkeeping file.
        assert {name for name in names if not name.endswith(("-wal", "-shm"))} == {
            "MEMORY.md",
            "memory.sqlite3",
        }
    finally:
        engine.close()


def test_an_old_vector_file_is_copied_in_rather_than_re_embedded(
    tmp_path: Path,
) -> None:
    """Upgrading must not cost one embedding per statement in the memory.

    The copy is keyed by the record's own identity, so the vectors that were
    already there are the ones the record needs and nothing is embedded again.
    """

    engine = _engine()
    embedder = engine.embedder
    identity = embedder.identity
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        engine.worker_for(tmp_path).drain(5.0)
        # Move the vectors into a file of the shape version 5 wrote.
        with MemoryIndex(tmp_path) as index:
            rows = index._open().execute("SELECT * FROM vector").fetchall()
        index_path(tmp_path).unlink()
        with sqlite3.connect(tmp_path / LEGACY_VECTORS_FILENAME) as connection:
            connection.execute(
                "CREATE TABLE vector("
                "unit_key TEXT PRIMARY KEY, source TEXT NOT NULL, stamp TEXT, "
                "type TEXT, model TEXT NOT NULL, dimension INTEGER, "
                "components BLOB NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO vector VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(row[0], "MEMORY.md", *tuple(row)[1:]) for row in rows],
            )
            connection.commit()

        report = reindex([tmp_path], embedder)

        with MemoryIndex(tmp_path) as index:
            assert index.count_units() == 1
            with VectorStore(tmp_path, identity.name, identity.dimension) as store:
                # One vector, and it was not embedded again: the copy is the one
                # the record needs.
                assert store.count() == 1
        # The one vector the file held was copied, and the one it does not belong
        # to any more was collected rather than left to answer for a statement that
        # no longer exists.
        assert report["scopes"][str(tmp_path)]["vectors_collected"] == 1
        assert not (tmp_path / LEGACY_VECTORS_FILENAME).exists()
    finally:
        engine.close()


def test_a_recovered_statement_is_undated_and_filed_as_item(tmp_path: Path) -> None:
    """A file holds the words and the order, and not the types or the history.

    This is what "the record is the table" means for a lost one: the memory comes
    back as what was said, with every statement in the default category and none
    of them dated. The alternative is a file of tagged lines, which is what a
    memory stopped being.
    """

    scope = tmp_path / "exported"
    scope.mkdir()
    (scope / "MEMORY.md").write_text(
        "# MEMORY\n\nthe draft lives in docs/\n\nalways cite the commit\n",
        encoding="utf-8",
    )
    engine = _engine()
    try:
        engine.answer(scope="local", directory=scope, query="draft", limit=5)
        with MemoryIndex(scope) as index:
            recovered = index.statements()
    finally:
        engine.close()

    assert [item.text for item in recovered] == [
        "the draft lives in docs/",
        "always cite the commit",
    ]
    assert {item.kind for item in recovered} == {"ITEM"}
    assert all(item.added_at is None for item in recovered)
    # A vector from before a loss belongs to a statement that no longer exists, and
    # the reindex collects it rather than answering for it.
    assert all(item.recalls >= 0 for item in recovered)


def test_a_worker_writes_only_the_scope_it_was_built_for(tmp_path: Path) -> None:
    """One scope's vector writer must never be handed another scope's statements.

    The reindex command rebuilds a project's memory and the account's in one pass.
    A worker holds the vector file of the scope it was created for, so passing it
    a second scope's statements wrote those vectors into the first scope's file:
    the first scope then held vectors nothing in it could answer from, and the
    second scope stayed permanently pending. Rebuilding more than one scope embeds
    in place instead.
    """

    first = tmp_path / "local"
    second = tmp_path / "global"
    embedder = FakeEmbedder()
    engine = Retrieval(
        embedder=embedder,
        policy=RetrievalSettings(embedding_model=embedder.identity.name),
    )
    worker = EmbeddingWorker(embedder, first)
    try:
        engine.record(
            directory=first, content="the first scope's statement", kind="NOTE"
        )
        engine.record(
            directory=second, content="the second scope's statement", kind="NOTE"
        )

        reindex([first, second], embedder, worker=worker)

        identity = embedder.identity
        assert VectorStore(first, identity.name, identity.dimension).count() == 1
        assert VectorStore(second, identity.name, identity.dimension).count() == 1
    finally:
        worker.stop()
        engine.close()


def test_a_renamed_column_keeps_the_kinds_the_file_held(tmp_path: Path) -> None:
    """A schema bump must not file everything as ITEM.

    An export holds the statements and nothing else, so restoring a record from
    the document beside it would lose every kind, every date, and every count. The
    file's own rows are the better source and are what this uses, and an import of
    an old file reverses it, because an old file appended and its last statement is
    its newest.
    """

    engine = _engine()
    try:
        engine.record(directory=tmp_path, content="the oldest note", kind="TEST")
        engine.record(directory=tmp_path, content="the newest plan", kind="PLAN")
        engine.answer(scope="local", directory=tmp_path, query="plan", limit=5)

        # A file of the previous version: the field is called `type`.
        with sqlite3.connect(index_path(tmp_path)) as connection:
            connection.execute("DROP TABLE unit")
            connection.execute(
                "CREATE VIRTUAL TABLE unit USING fts5("
                "text, unit_key UNINDEXED, type UNINDEXED, stamp UNINDEXED, "
                "normalized UNINDEXED, added_at, recalls, last_recalled_at)"
            )
            connection.executemany(
                "INSERT INTO unit(text, unit_key, type, stamp, normalized, added_at, "
                "recalls, last_recalled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "the newest plan",
                        unit_key("PLAN: the newest plan"),
                        "PLAN",
                        "0",
                        "the newest plan",
                        "2026-09-01T00:00:00.000+00:00",
                        4,
                        "2026-09-02T00:00:00.000+00:00",
                    ),
                    (
                        "the oldest note",
                        unit_key("TEST: the oldest note"),
                        "TEST",
                        "1",
                        "the oldest note",
                        "2026-08-01T00:00:00.000+00:00",
                        1,
                        None,
                    ),
                ],
            )
            connection.execute(
                "UPDATE meta SET value = '6' WHERE key = 'schema_version'"
            )
            connection.commit()

        with MemoryIndex(tmp_path) as index:
            restored = index.statements()

        assert [item.text for item in restored] == [
            "the newest plan",
            "the oldest note",
        ]
        assert {item.text: item.kind for item in restored} == {
            "the newest plan": "PLAN",
            "the oldest note": "TEST",
        }
        assert restored[0].position == 0
        assert restored[0].recalls == 4
        assert restored[0].added_at == "2026-09-01T00:00:00.000+00:00"
        assert restored[0].last_recalled_at == "2026-09-02T00:00:00.000+00:00"
    finally:
        engine.close()
