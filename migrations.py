"""Schema migrations shared by the multi-user app and the legacy server.

Both keep their weigh-in rows in a SQLite `weights` table with the same
columns, and both need the same structural change, so the risky one lives here
once rather than being written twice.
"""

from __future__ import annotations

import sqlite3


def _weight_is_not_null(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA table_info(weights)"):
        # (cid, name, type, notnull, dflt_value, pk)
        if row[1] == "weight":
            return bool(row[3])
    return False


def ensure_weight_nullable(conn: sqlite3.Connection) -> bool:
    """Drop NOT NULL from weights.weight so a row can carry only a waist.

    Waist is measured with a tape every week or two while weight arrives from
    the scale daily; forcing the two into one row meant a waist measurement had
    to be edited onto an existing weigh-in. A weight-less row is a real entry,
    not a placeholder, so nothing is invented to satisfy the constraint.

    SQLite cannot drop NOT NULL in place, so this is the documented
    create/copy/drop/rename rebuild. Returns True if it did any work.

    Indexes are recreated from sqlite_master rather than hardcoded, so an index
    added later is not silently dropped by this migration.
    """
    if not _weight_is_not_null(conn):
        return False

    indexes = [
        sql
        for (sql,) in conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE tbl_name='weights' AND type='index' AND sql IS NOT NULL"
        ).fetchall()
    ]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(weights)").fetchall()]
    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='weights'"
    ).fetchone()[0]

    # Rewrite only the weight column's constraint, leaving every other column,
    # default and foreign key exactly as the live schema has it.
    patched = create_sql.replace("weight FLOAT NOT NULL", "weight FLOAT", 1)
    if patched == create_sql:
        patched = create_sql.replace("weight REAL NOT NULL", "weight REAL", 1)
    if patched == create_sql:
        raise RuntimeError(
            "could not find 'weight ... NOT NULL' in the weights schema; "
            "refusing to rebuild the table blind"
        )
    patched = patched.replace("weights", "weights_migrate_new", 1)

    col_list = ", ".join(f'"{c}"' for c in cols)
    # foreign_keys must be toggled outside a transaction to take effect.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(patched)
        conn.execute(
            f"INSERT INTO weights_migrate_new ({col_list}) SELECT {col_list} FROM weights"
        )
        conn.execute("DROP TABLE weights")
        conn.execute("ALTER TABLE weights_migrate_new RENAME TO weights")
        for sql in indexes:
            conn.execute(sql)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    violations = conn.execute("PRAGMA foreign_key_check(weights)").fetchall()
    if violations:
        raise RuntimeError(f"weights rebuild left FK violations: {violations[:3]}")
    return True
