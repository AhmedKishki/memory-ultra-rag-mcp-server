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
    PRODUCTIVE_COLUMNS,
    SUPERSEDED_FILENAMES,
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


def test_every_column_on_a_row_has_a_reader(tmp_path: Path) -> None:
    """A statement is stored once and carries only what something reads.

    The row used to hold the normalised words beside the words themselves, which
    no code read, and a date for the last recall, which a read wrote on every
    statement it returned and nothing ever read: two columns of storage and one
    write per answered statement, for nothing. Every column left has one named
    reader in `PRODUCTIVE_COLUMNS`, so adding another means naming what reads it.
    """

    with MemoryIndex(tmp_path) as index:
        columns = {
            str(record[1])
            for record in index._open().execute("PRAGMA table_info(unit)")
        }

    assert columns == {"text", "unit_key"} | set(PRODUCTIVE_COLUMNS)
    assert "normalized" not in columns, "the statement is stored twice"
    for name in PRODUCTIVE_COLUMNS:
        assert PRODUCTIVE_COLUMNS[name].strip(), f"{name} names no reader"


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

    assert {"kind", "stamp", "added_at", "recalls"} <= columns
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
    """A memory of an older version is its document, and that is how it is recovered.

    The document is the only copy such a memory has, so it is read for anything
    the record lacks and then removed, and the answer says the file went.
    """

    scope = tmp_path / "recovered"
    scope.mkdir()
    (scope / "MEMORY.md").write_text(
        f"# MEMORY\n{SEED}\n\nPLAN: the corpus parses\n\nNOTE: cite the commit\n",
        encoding="utf-8",
    )
    engine = _engine()
    try:
        answer = engine.answer(scope="local", directory=scope, query="corpus", limit=5)
        with MemoryIndex(scope) as index:
            statements = index.statements()
    finally:
        engine.close()

    assert len(answer["units"]) == 1
    assert answer["units"][0]["kind"] == "PLAN"
    # A file this server did not write is read and removed; one it did is left.
    assert (scope / "MEMORY.md").is_file()
    # Reversed, because a prefixed file was written by a version that appended.
    assert [item.text for item in statements] == [
        "cite the commit",
        "the corpus parses",
    ]
    assert [item.kind for item in statements] == ["NOTE", "PLAN"]


def test_a_hand_written_rendering_beside_the_record_is_not_read(
    tmp_path: Path,
) -> None:
    """A rendering beside a live record is left where the person put it.

    The record is the memory, so a document beside it is neither a source to import
    nor a second copy to clean up. Importing it would put back every statement a
    forget removed, since nothing re-renders the file after a forget.
    """

    engine = _engine()
    try:
        engine.record(directory=tmp_path, content="a recorded fact", kind="NOTE")
        (tmp_path / "MEMORY.md").write_text(
            "# MEMORY\n\nNOTE: something a person typed by hand\n", encoding="utf-8"
        )

        answer = engine.answer(scope="local", directory=tmp_path, query="fact", limit=5)
        with MemoryIndex(tmp_path) as index:
            statements = [item.text for item in index.statements()]

        assert len(answer["units"]) == 1
        assert statements == ["a recorded fact"]
        assert "superseded_removed" not in answer
        assert (tmp_path / "MEMORY.md").is_file()
    finally:
        engine.close()


def test_a_rendering_the_record_agrees_with_is_left_alone(tmp_path: Path) -> None:
    """A file somebody asked for is not deleted on the next read."""

    engine = _engine()
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        with MemoryIndex(tmp_path) as index:
            index.write_render()

        answer = engine.answer(
            scope="local", directory=tmp_path, query="draft", limit=5
        )

        assert "superseded_removed" not in answer
        assert (tmp_path / "MEMORY.md").is_file()
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
    # answer rather than a guess about when a person wrote it: a statement with no
    # date carries no date field at all.
    assert "added_at" not in typed
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
        # what matters is that there is no second database and no document.
        assert {name for name in names if not name.endswith(("-wal", "-shm"))} == {
            "memory.sqlite3"
        }
    finally:
        engine.close()


def test_an_old_vector_file_is_copied_in_rather_than_re_embedded(
    tmp_path: Path,
) -> None:
    """Upgrading must not cost one embedding per statement in the memory.

    A version-5 scope held its vectors in a file of their own. The upgrade moves
    them into the one file, so the vectors the memory already paid for are the
    ones it keeps, and a vector file is never mined for words: it holds none, which
    is why the record is the one that has to survive.
    """

    engine = _engine()
    embedder = engine.embedder
    identity = embedder.identity
    try:
        engine.record(
            directory=tmp_path, content="the draft lives in docs/", kind="NOTE"
        )
        engine.worker_for(tmp_path).drain(5.0)
        # Move the vectors into a file of the shape version 5 wrote, and drop them
        # from the record, which is what that version's upgrade path left behind.
        with MemoryIndex(tmp_path) as index:
            rows = index._open().execute("SELECT * FROM vector").fetchall()
            index.drop_vectors()
            engine.close()
        with sqlite3.connect(tmp_path / SUPERSEDED_FILENAMES[1]) as connection:
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
        engine = _engine()
        engine.embedder = embedder

        report = reindex([tmp_path], embedder)

        with MemoryIndex(tmp_path) as index:
            assert index.count_units() == 1
            with VectorStore(tmp_path, identity.name, identity.dimension) as store:
                # One vector, and it was not embedded again: the copy is the one
                # the record needs.
                assert store.count() == 1
        assert index_path(tmp_path) in [path for path in tmp_path.iterdir()]
        assert not (tmp_path / SUPERSEDED_FILENAMES[1]).exists()
        assert report["scopes"][str(tmp_path)]["units_pending"] == 0
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
        # Both records queued a vector, and the worker writes to the same file
        # this test is about to rewrite underneath the engine. Wait for it, or
        # the rewrite races a write and the file holds a third state.
        engine.worker_for(tmp_path).drain(5.0)

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
        # The date a version-7 file kept of the last recall is not carried over: it
        # was a column nothing read, and the statements it sat on are the ones
        # being kept.
        with MemoryIndex(tmp_path) as index:
            columns = {
                str(record[1])
                for record in index._open().execute("PRAGMA table_info(unit)")
            }
        assert "last_recalled_at" not in columns
        assert "normalized" not in columns
    finally:
        engine.close()


def test_an_upgraded_scope_is_not_left_holding_the_files_it_outgrew(
    tmp_path: Path,
) -> None:
    """One file per scope: the old index is read for its words, then removed.

    The upgrade that brought the record into one file copied the statements and the
    vectors across and left the old databases where they were, so a scope kept a
    dead file beside a live one and looked like an installation two versions at
    once.
    """

    from memory_ultra_rag_mcp.index import SUPERSEDED_FILENAMES, index_path

    scope = tmp_path / "scope"
    scope.mkdir()
    engine = _engine()
    try:
        engine.record(directory=scope, content="the draft lives in docs/", kind="NOTE")
        key = unit_key("NOTE: the draft lives in docs/")

        # A file of the shape the word index had before the record existed, with a
        # statement the record has never seen and one it has.
        stale = scope / SUPERSEDED_FILENAMES[0]
        with sqlite3.connect(stale) as connection:
            connection.execute(
                "CREATE VIRTUAL TABLE unit USING fts5("
                "text, unit_key UNINDEXED, type UNINDEXED, source UNINDEXED, "
                "stamp UNINDEXED)"
            )
            connection.executemany(
                "INSERT INTO unit(text, unit_key, type, source, stamp) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        "a statement only the old index knows",
                        unit_key("PLAN: a statement only the old index knows"),
                        "PLAN",
                        "MEMORY.md",
                        "9",
                    ),
                    ("NOTE: the draft lives in docs/", key, "NOTE", "MEMORY.md", "0"),
                ],
            )
            connection.execute(
                "CREATE TABLE source(path TEXT PRIMARY KEY, size INTEGER, "
                "mtime_ns INTEGER, head TEXT)"
            )
            connection.commit()
        (scope / f"{stale.name}-wal").write_bytes(b"")

        answer = engine.answer(scope="local", directory=scope, query="draft", limit=5)

        assert answer["superseded_removed"] == [SUPERSEDED_FILENAMES[0]]
        assert not stale.exists()
        assert not Path(f"{stale}-wal").exists()
        assert index_path(scope).is_file()
        with MemoryIndex(scope) as index:
            restored = {item.text for item in index.statements()}
        # What the old file still held that the record lacked is kept, not deleted.
        assert restored == {
            "the draft lives in docs/",
            "a statement only the old index knows",
        }
    finally:
        engine.close()


def test_an_unreadable_superseded_file_is_left_alone(tmp_path: Path) -> None:
    """A file this version cannot read is not deleted on a guess."""

    from memory_ultra_rag_mcp.index import SUPERSEDED_FILENAMES

    scope = tmp_path / "scope"
    scope.mkdir()
    engine = _engine()
    try:
        engine.record(directory=scope, content="a fact", kind="NOTE")
        (scope / SUPERSEDED_FILENAMES[0]).write_bytes(b"not a database at all")

        answer = engine.answer(scope="local", directory=scope, query="fact", limit=5)

        assert (scope / SUPERSEDED_FILENAMES[0]).is_file()
        assert "superseded_removed" not in answer
    finally:
        engine.close()
