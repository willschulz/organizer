"""
Organizer -- SQLite layer.

Single-file connection helper plus schema migration on startup.

Schema is small: two tables (projects, todos) with parent_id on todos for
future nested rendering. No Alembic, no migrations directory -- if the
schema needs to change later, add a new IF-NOT-EXISTS or ALTER below the
initial CREATE block.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path(os.environ.get(
    "ORGANIZER_DB_PATH",
    str(Path(__file__).resolve().parent / "data" / "organizer.db"),
))

CATEGORIES = (
    "top_of_mind",
    "near_publication",
    "in_development",
    "early_stage",
    "side_project",
)

SCHEMA_SQL = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  name            TEXT    NOT NULL,
  category        TEXT    NOT NULL CHECK (category IN
                    {tuple(CATEGORIES)}),
  display_order   INTEGER NOT NULL DEFAULT 0,
  deadline        TEXT,
  notes           TEXT    NOT NULL DEFAULT '',
  paths_json      TEXT    NOT NULL DEFAULT '[]',
  archived        INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT    NOT NULL,
  updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  parent_id       INTEGER REFERENCES todos(id) ON DELETE CASCADE,
  text            TEXT    NOT NULL,
  is_blocker      INTEGER NOT NULL DEFAULT 0,
  is_followup     INTEGER NOT NULL DEFAULT 0,
  display_order   INTEGER NOT NULL DEFAULT 0,
  in_progress     INTEGER NOT NULL DEFAULT 0,
  completed       INTEGER NOT NULL DEFAULT 0,
  completed_at    TEXT,
  notes           TEXT    NOT NULL DEFAULT '',
  paths_json      TEXT    NOT NULL DEFAULT '[]',
  created_at      TEXT    NOT NULL,
  updated_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_todos_project      ON todos(project_id);
CREATE INDEX IF NOT EXISTS idx_todos_parent       ON todos(parent_id);
CREATE INDEX IF NOT EXISTS idx_todos_completed_at ON todos(completed_at);

CREATE TABLE IF NOT EXISTS day_overrides (
  date TEXT PRIMARY KEY   -- ISO date YYYY-MM-DD; presence = "flipped" from natural state
);
"""


def _connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """Return the lazily-initialized module-level connection."""
    global _conn
    if _conn is None:
        _conn = _connect()
    return _conn


def _migrate_add_todo_context(conn: sqlite3.Connection) -> None:
    """Idempotent: add notes + paths_json to existing todos tables."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(todos)").fetchall()}
    if "notes" not in cols:
        conn.execute(
            "ALTER TABLE todos ADD COLUMN notes TEXT NOT NULL DEFAULT ''"
        )
    if "paths_json" not in cols:
        conn.execute(
            "ALTER TABLE todos ADD COLUMN paths_json TEXT NOT NULL DEFAULT '[]'"
        )


def _migrate_add_todo_inprogress(conn: sqlite3.Connection) -> None:
    """Idempotent: add in_progress column to existing todos tables."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(todos)").fetchall()}
    if "in_progress" not in cols:
        conn.execute(
            "ALTER TABLE todos ADD COLUMN in_progress INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_add_todo_effort(conn: sqlite3.Connection) -> None:
    """Idempotent: add effort column to existing todos tables."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(todos)").fetchall()}
    if "effort" not in cols:
        conn.execute("ALTER TABLE todos ADD COLUMN effort INTEGER")


def _migrate_add_day_overrides(conn: sqlite3.Connection) -> None:
    """Idempotent: create day_overrides table if it doesn't exist yet."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day_overrides (
            date TEXT PRIMARY KEY
        )
    """)


def _migrate_add_top_of_mind_category(conn: sqlite3.Connection) -> None:
    """Idempotent: widen projects.category CHECK to include top_of_mind.

    SQLite does not support ALTER TABLE to change CHECK constraints, so we
    recreate the projects table with the widened constraint via the standard
    create-copy-drop-rename pattern.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'"
    ).fetchone()
    if row and "top_of_mind" in row[0]:
        return  # already migrated
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute("""
            CREATE TABLE projects_new (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              name            TEXT    NOT NULL,
              category        TEXT    NOT NULL CHECK (category IN
                                ('top_of_mind','near_publication','in_development',
                                 'early_stage','side_project')),
              display_order   INTEGER NOT NULL DEFAULT 0,
              deadline        TEXT,
              notes           TEXT    NOT NULL DEFAULT '',
              paths_json      TEXT    NOT NULL DEFAULT '[]',
              archived        INTEGER NOT NULL DEFAULT 0,
              created_at      TEXT    NOT NULL,
              updated_at      TEXT    NOT NULL
            )
        """)
        conn.execute("INSERT INTO projects_new SELECT * FROM projects")
        conn.execute("DROP TABLE projects")
        conn.execute("ALTER TABLE projects_new RENAME TO projects")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_notes_to_json_array(conn: sqlite3.Connection) -> None:
    """Idempotent: convert notes TEXT blobs to JSON arrays of strings.

    Rows that already contain a valid JSON array are left untouched.
    Rows with a non-empty blob become ["<blob>"].
    Rows with an empty string become [].
    """
    for table in ("projects", "todos"):
        rows = conn.execute(f"SELECT id, notes FROM {table}").fetchall()
        for r in rows:
            raw = r["notes"] or ""
            try:
                val = json.loads(raw)
                if isinstance(val, list):
                    continue
            except Exception:
                pass
            new_val = json.dumps([raw]) if raw.strip() else "[]"
            conn.execute(f"UPDATE {table} SET notes = ? WHERE id = ?", (new_val, r["id"]))


def init_schema() -> None:
    """Apply schema (idempotent)."""
    conn = get_conn()
    conn.executescript(SCHEMA_SQL)
    _migrate_add_todo_context(conn)
    _migrate_add_todo_inprogress(conn)
    _migrate_add_todo_effort(conn)
    _migrate_add_top_of_mind_category(conn)
    _migrate_notes_to_json_array(conn)
    _migrate_add_day_overrides(conn)


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
