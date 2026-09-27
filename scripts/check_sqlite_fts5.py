"""Report whether this Python's SQLite can build the search index.

Run this before installing the server anywhere it matters:

```bash
uv run --frozen python scripts/check_sqlite_fts5.py
```

The search index is a SQLite FTS5 virtual table, and FTS5 is a compile-time
option of the SQLite library a Python was built against. A Python without it
would leave a read with no choice but to pass over every file a memory holds,
which is the cost the index exists to remove, so the server refuses to start
rather than serve reads that grow with the memory.
"""

from __future__ import annotations

import sqlite3


def main() -> int:
    version = sqlite3.sqlite_version
    try:
        connection = sqlite3.connect(":memory:")
    except sqlite3.Error as error:  # pragma: no cover - a Python without sqlite3
        print(f"sqlite3 is unusable in this Python: {error}")
        return 1

    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        connection.execute("INSERT INTO probe(text) VALUES ('the draft lives in docs')")
        found = connection.execute(
            "SELECT count(*) FROM probe WHERE probe MATCH 'draft'"
        ).fetchone()
        matched = int(found[0]) if found else 0
    except sqlite3.Error as error:
        print(f"SQLite {version} is present but FTS5 is not: {error}")
        print("use a Python whose sqlite3 module was built with FTS5")
        return 1
    finally:
        connection.close()

    if matched != 1:
        print(f"SQLite {version}: FTS5 created a table but a search returned {matched}")
        return 1

    print(f"SQLite {version}: FTS5 is available and a search works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
