"""weights.weight NOT NULL -> nullable, on a copy of the production schema.

    python3 test/test_migrations.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from migrations import ensure_weight_nullable  # noqa: E402

failed = 0
passed = 0


def check(cond, msg):
    global failed, passed
    if cond:
        passed += 1
    else:
        print("FAIL:", msg)
        failed += 1


# Byte-for-byte the schema SQLAlchemy created on the Linode, waist column and all.
PROD_SCHEMA = """
CREATE TABLE users (
	id INTEGER NOT NULL,
	email VARCHAR(320) NOT NULL,
	PRIMARY KEY (id)
);
CREATE TABLE weights (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	date VARCHAR(32) NOT NULL,
	logged_at VARCHAR(64),
	weight FLOAT NOT NULL,
	body_fat FLOAT,
	note TEXT,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, waist FLOAT,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_weights_user_id ON weights (user_id);
"""


def fresh_db():
    path = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(path)
    c.executescript(PROD_SCHEMA)
    c.execute("INSERT INTO users VALUES (1, 'a@b.c')")
    c.execute("INSERT INTO users VALUES (2, 'other@b.c')")
    for i in range(28):
        c.execute(
            "INSERT INTO weights (user_id, date, logged_at, weight, body_fat, note, waist) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                1 if i % 3 else 2,
                f"2026-08-{i + 1:02d}",
                f"2026-08-{i + 1:02d}T07:00:00+00:00",
                200.0 - 0.25 * i,
                25.0,
                "renpho-ble" if i % 2 else None,
                35.5 if i == 27 else None,
            ),
        )
    c.commit()
    return path, c


def notnull(conn):
    return {r[1]: bool(r[3]) for r in conn.execute("PRAGMA table_info(weights)")}


path, conn = fresh_db()
before = conn.execute("SELECT id, user_id, date, logged_at, weight, body_fat, note, waist "
                      "FROM weights ORDER BY id").fetchall()
check(notnull(conn)["weight"] is True, "starts NOT NULL, as production does")

did = ensure_weight_nullable(conn)
check(did is True, "migration reports that it ran")

# --- every row survives, byte for byte ---
after = conn.execute("SELECT id, user_id, date, logged_at, weight, body_fat, note, waist "
                     "FROM weights ORDER BY id").fetchall()
check(after == before, f"all 28 rows preserved exactly ({len(before)} -> {len(after)})")
check(len(after) == 28, "row count unchanged")
check(any(r[7] == 35.5 for r in after), "the existing waist value survived")
check(len({r[1] for r in after}) == 2, "both users' rows survived")

# --- the constraint is gone, everything else stayed ---
nn = notnull(conn)
check(nn["weight"] is False, "weight is now nullable")
check(nn["user_id"] is True, "user_id still NOT NULL")
check(nn["date"] is True, "date still NOT NULL")
check(nn["created_at"] is True, "created_at still NOT NULL")
check(nn["updated_at"] is True, "updated_at still NOT NULL")
cols = [r[1] for r in conn.execute("PRAGMA table_info(weights)")]
check(
    cols == ["id", "user_id", "date", "logged_at", "weight", "body_fat", "note",
             "created_at", "updated_at", "waist"],
    f"column order and set unchanged: {cols}",
)

# --- the index came back ---
idx = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE tbl_name='weights' AND type='index' AND sql IS NOT NULL")]
check("ix_weights_user_id" in idx, f"index recreated: {idx}")

# --- the FK still bites ---
conn.execute("PRAGMA foreign_keys=ON")
try:
    conn.execute("INSERT INTO weights (user_id, date, weight) VALUES (999, '2026-01-01', 1.0)")
    conn.commit()
    check(False, "FK to users should still be enforced")
except sqlite3.IntegrityError:
    conn.rollback()
    check(True, "FK to users still enforced after rebuild")

# --- cascade delete still works (the point of the FK) ---
conn.execute("DELETE FROM users WHERE id=2")
conn.commit()
left = conn.execute("SELECT COUNT(*) FROM weights WHERE user_id=2").fetchone()[0]
check(left == 0, f"ON DELETE CASCADE survived the rebuild ({left} orphans)")

# --- and the whole point: a waist-only row is now insertable ---
conn.execute(
    "INSERT INTO weights (user_id, date, logged_at, weight, waist) "
    "VALUES (1, '2026-08-28', '2026-08-28T21:15:00+00:00', NULL, 35.5)"
)
conn.commit()
row = conn.execute("SELECT weight, waist FROM weights WHERE weight IS NULL").fetchone()
check(row == (None, 35.5), f"waist-only row stored: {row}")

# --- idempotent, and cheap when there is nothing to do ---
settled = conn.execute("SELECT id, weight, waist FROM weights ORDER BY id").fetchall()
check(ensure_weight_nullable(conn) is False, "second run is a no-op")
check(ensure_weight_nullable(conn) is False, "third run is still a no-op")
check(
    conn.execute("SELECT id, weight, waist FROM weights ORDER BY id").fetchall() == settled,
    "no-op runs did not touch the data",
)
conn.close()
os.unlink(path)

# --- a schema it does not recognise is refused, not rebuilt blind ---
path2 = tempfile.mktemp(suffix=".db")
c2 = sqlite3.connect(path2)
c2.executescript("CREATE TABLE weights (id INTEGER PRIMARY KEY, weight NUMERIC NOT NULL);")
try:
    ensure_weight_nullable(c2)
    check(False, "unrecognised weight column type should raise")
except RuntimeError as e:
    check("refusing" in str(e), f"refuses to guess: {e}")
check(
    c2.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='weights'").fetchone()[0] == 1,
    "refusal left the original table in place",
)
c2.close()
os.unlink(path2)

# --- the legacy single-user schema: REAL NOT NULL, AUTOINCREMENT id ---
path4 = tempfile.mktemp(suffix=".db")
c4 = sqlite3.connect(path4)
c4.executescript("""
CREATE TABLE weights (
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, weight REAL NOT NULL,
  note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, waist REAL);
CREATE INDEX idx_weights_date ON weights(date);
CREATE INDEX idx_weights_logged_at ON weights(date);
INSERT INTO weights (date, weight, note, created_at, updated_at)
  VALUES ('2026-02-01', 200.0, 'legacy', 'x', 'x');
""")
c4.commit()
check(ensure_weight_nullable(c4) is True, "legacy REAL NOT NULL schema is migrated")
check(
    {r[1]: bool(r[3]) for r in c4.execute("PRAGMA table_info(weights)")}["weight"] is False,
    "legacy weight is now nullable",
)
sql4 = c4.execute("SELECT sql FROM sqlite_master WHERE name='weights'").fetchone()[0]
check("AUTOINCREMENT" in sql4, "AUTOINCREMENT survived the rebuild")
check(
    c4.execute("SELECT date, weight, note FROM weights").fetchall()
    == [("2026-02-01", 200.0, "legacy")],
    "legacy row preserved",
)
idx4 = sorted(r[0] for r in c4.execute(
    "SELECT name FROM sqlite_master WHERE tbl_name='weights' AND type='index' AND sql IS NOT NULL"))
check(idx4 == ["idx_weights_date", "idx_weights_logged_at"], f"both legacy indexes back: {idx4}")
c4.execute("INSERT INTO weights (date, weight, waist, created_at, updated_at) "
           "VALUES ('2026-02-10', NULL, 35.5, 'x', 'x')")
c4.commit()
new_id = c4.execute("SELECT id FROM weights WHERE weight IS NULL").fetchone()[0]
check(new_id == 2, f"ids still auto-assign after the rebuild, got {new_id}")
c4.close()
os.unlink(path4)

# --- an already-nullable DB (fresh create_all) is left alone ---
path3 = tempfile.mktemp(suffix=".db")
c3 = sqlite3.connect(path3)
c3.executescript(PROD_SCHEMA.replace("weight FLOAT NOT NULL", "weight FLOAT"))
check(ensure_weight_nullable(c3) is False, "fresh nullable schema needs no work")
c3.close()
os.unlink(path3)

print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
