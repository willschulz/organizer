"""
Organizer -- FastAPI backend.

Run locally:
    uvicorn main:app --reload --port 8551

In production this is run by systemd via organizer.service.

Tailnet is the auth perimeter; there is no app-level auth.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import db

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    """ISO-8601 UTC timestamp, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _project_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["paths"] = json.loads(d.pop("paths_json") or "[]")
    except Exception:
        d["paths"] = []
    try:
        notes = json.loads(d.get("notes") or "[]")
        d["notes"] = notes if isinstance(notes, list) else [notes]
    except Exception:
        d["notes"] = []
    d["archived"] = bool(d["archived"])
    return d


def _todo_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    d["is_blocker"] = bool(d["is_blocker"])
    d["is_followup"] = bool(d["is_followup"])
    d["in_progress"] = bool(d.get("in_progress", 0))
    d["completed"] = bool(d["completed"])
    try:
        d["paths"] = json.loads(d.pop("paths_json") or "[]")
    except Exception:
        d["paths"] = []
    try:
        notes = json.loads(d.get("notes") or "[]")
        d["notes"] = notes if isinstance(notes, list) else [notes]
    except Exception:
        d["notes"] = []
    return d


def _root_is_followup(conn, parent_id: int) -> bool:
    """Walk up the parent chain to find the root todo's is_followup value."""
    row = conn.execute("SELECT parent_id, is_followup FROM todos WHERE id = ?", (parent_id,)).fetchone()
    if row is None:
        return False
    if row["parent_id"] is None:
        return bool(row["is_followup"])
    return _root_is_followup(conn, row["parent_id"])


def _next_display_order(table: str, where_sql: str, params: tuple) -> int:
    cur = db.get_conn().execute(
        f"SELECT COALESCE(MAX(display_order), -1) + 1 AS next FROM {table} WHERE {where_sql}",
        params,
    )
    return int(cur.fetchone()["next"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: str
    deadline: Optional[str] = None  # ISO date "YYYY-MM-DD"
    notes: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    display_order: Optional[int] = None

    @field_validator("category")
    @classmethod
    def _check_category(cls, v: str) -> str:
        if v not in db.CATEGORIES:
            raise ValueError(f"category must be one of {db.CATEGORIES}")
        return v


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category: Optional[str] = None
    deadline: Optional[str] = None       # pass null to clear
    deadline_set: bool = False           # explicit: True means use deadline (incl null)
    notes: Optional[list[str]] = None
    paths: Optional[list[str]] = None
    display_order: Optional[int] = None
    archived: Optional[bool] = None

    @field_validator("category")
    @classmethod
    def _check_category(cls, v):
        if v is not None and v not in db.CATEGORIES:
            raise ValueError(f"category must be one of {db.CATEGORIES}")
        return v


class TodoReorderItem(BaseModel):
    id: int
    parent_id: Optional[int] = None
    display_order: int
    is_followup: Optional[bool] = None


class TodoCreate(BaseModel):
    project_id: int
    text: str = Field(..., min_length=1)
    parent_id: Optional[int] = None
    is_blocker: bool = False
    is_followup: bool = False
    in_progress: bool = False
    display_order: Optional[int] = None
    notes: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    effort: Optional[int] = None

    @field_validator("effort")
    @classmethod
    def _check_effort(cls, v):
        if v is not None and v not in (1, 2, 3, 5, 8, 13):
            raise ValueError("effort must be one of 1, 2, 3, 5, 8, 13")
        return v


class TodoUpdate(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1)
    parent_id: Optional[int] = None
    parent_id_set: bool = False
    is_blocker: Optional[bool] = None
    is_followup: Optional[bool] = None
    in_progress: Optional[bool] = None
    display_order: Optional[int] = None
    completed: Optional[bool] = None
    notes: Optional[list[str]] = None
    paths: Optional[list[str]] = None
    effort: Optional[int] = None
    effort_set: bool = False   # True means write effort (including null to clear)

    @field_validator("effort")
    @classmethod
    def _check_effort(cls, v):
        if v is not None and v not in (1, 2, 3, 5, 8, 13):
            raise ValueError("effort must be one of 1, 2, 3, 5, 8, 13")
        return v


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    yield


app = FastAPI(title="Organizer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Static / PWA routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon():
    return FileResponse(STATIC_DIR / "icons" / "apple-touch-icon.png", media_type="image/png")


@app.get("/icon-192.png", include_in_schema=False)
async def icon_192():
    return FileResponse(STATIC_DIR / "icons" / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png", include_in_schema=False)
async def icon_512():
    return FileResponse(STATIC_DIR / "icons" / "icon-512.png", media_type="image/png")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    fav = STATIC_DIR / "icons" / "favicon.ico"
    if fav.exists():
        return FileResponse(fav, media_type="image/x-icon")
    return FileResponse(STATIC_DIR / "icons" / "icon-192.png", media_type="image/png")


# ---------------------------------------------------------------------------
# API: projects
# ---------------------------------------------------------------------------

@app.get("/api/projects")
async def list_projects(include_archived: bool = Query(default=False)):
    """All projects with their todos, grouped by category."""
    conn = db.get_conn()
    where = "" if include_archived else "WHERE archived = 0"
    proj_rows = conn.execute(
        f"SELECT * FROM projects {where} "
        "ORDER BY category, display_order, id"
    ).fetchall()
    todo_rows = conn.execute(
        "SELECT * FROM todos ORDER BY project_id, "
        "is_followup, (parent_id IS NOT NULL), display_order, id"
    ).fetchall()

    todos_by_project: dict[int, list[dict]] = {}
    for r in todo_rows:
        todos_by_project.setdefault(r["project_id"], []).append(_todo_to_dict(r))

    grouped: dict[str, list[dict]] = {c: [] for c in db.CATEGORIES}
    for r in proj_rows:
        proj = _project_to_dict(r)
        proj["todos"] = todos_by_project.get(proj["id"], [])
        grouped.setdefault(proj["category"], []).append(proj)

    return {
        "categories": db.CATEGORIES,
        "projects": grouped,
    }


@app.post("/api/projects", status_code=201)
async def create_project(payload: ProjectCreate):
    conn = db.get_conn()
    now = _now()
    order = (
        payload.display_order
        if payload.display_order is not None
        else _next_display_order(
            "projects", "category = ? AND archived = 0", (payload.category,)
        )
    )
    cur = conn.execute(
        "INSERT INTO projects "
        "(name, category, display_order, deadline, notes, paths_json, archived, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (
            payload.name.strip(),
            payload.category,
            order,
            payload.deadline,
            json.dumps(payload.notes),
            json.dumps(payload.paths),
            now,
            now,
        ),
    )
    pid = cur.lastrowid
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    proj = _project_to_dict(row)
    proj["todos"] = []
    return proj


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: int, payload: ProjectUpdate):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")

    fields: list[str] = []
    values: list[Any] = []
    if payload.name is not None:
        fields.append("name = ?")
        values.append(payload.name.strip())
    if payload.category is not None:
        fields.append("category = ?")
        values.append(payload.category)
    if payload.deadline_set:
        fields.append("deadline = ?")
        values.append(payload.deadline)
    if payload.notes is not None:
        fields.append("notes = ?")
        values.append(json.dumps(payload.notes))
    if payload.paths is not None:
        fields.append("paths_json = ?")
        values.append(json.dumps(payload.paths))
    if payload.display_order is not None:
        fields.append("display_order = ?")
        values.append(payload.display_order)
    if payload.archived is not None:
        fields.append("archived = ?")
        values.append(1 if payload.archived else 0)

    if not fields:
        return _project_with_todos(project_id)

    fields.append("updated_at = ?")
    values.append(_now())
    values.append(project_id)
    conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", values)
    return _project_with_todos(project_id)


@app.delete("/api/projects/{project_id}", status_code=204)
async def delete_project(project_id: int):
    conn = db.get_conn()
    cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="project not found")
    return None


def _project_with_todos(project_id: int) -> dict[str, Any]:
    conn = db.get_conn()
    pr = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if pr is None:
        raise HTTPException(status_code=404, detail="project not found")
    proj = _project_to_dict(pr)
    todos = conn.execute(
        "SELECT * FROM todos WHERE project_id = ? "
        "ORDER BY is_followup, (parent_id IS NOT NULL), display_order, id",
        (project_id,),
    ).fetchall()
    proj["todos"] = [_todo_to_dict(t) for t in todos]
    return proj


# ---------------------------------------------------------------------------
# API: todos
# ---------------------------------------------------------------------------

@app.post("/api/todos", status_code=201)
async def create_todo(payload: TodoCreate):
    conn = db.get_conn()
    proj = conn.execute("SELECT id FROM projects WHERE id = ?", (payload.project_id,)).fetchone()
    if proj is None:
        raise HTTPException(status_code=404, detail="project not found")
    # Determine the effective is_followup: children inherit from their root ancestor.
    effective_followup = payload.is_followup
    if payload.parent_id is not None:
        parent = conn.execute(
            "SELECT id, project_id FROM todos WHERE id = ?", (payload.parent_id,)
        ).fetchone()
        if parent is None:
            raise HTTPException(status_code=404, detail="parent todo not found")
        if parent["project_id"] != payload.project_id:
            raise HTTPException(status_code=400, detail="parent todo belongs to a different project")
        effective_followup = _root_is_followup(conn, payload.parent_id)

    now = _now()
    order = (
        payload.display_order
        if payload.display_order is not None
        else _next_display_order(
            "todos",
            "project_id = ? AND parent_id IS ?",
            (payload.project_id, payload.parent_id),
        )
    )
    cur = conn.execute(
        "INSERT INTO todos "
        "(project_id, parent_id, text, is_blocker, is_followup, in_progress, display_order, "
        " completed, completed_at, notes, paths_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?)",
        (
            payload.project_id,
            payload.parent_id,
            payload.text.strip(),
            int(payload.is_blocker),
            int(effective_followup),
            int(payload.in_progress),
            order,
            json.dumps(payload.notes),
            json.dumps(payload.paths),
            now,
            now,
        ),
    )
    tid = cur.lastrowid
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (tid,)).fetchone()
    return _todo_to_dict(row)


@app.patch("/api/todos/{todo_id}")
async def update_todo(todo_id: int, payload: TodoUpdate):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="todo not found")

    fields: list[str] = []
    values: list[Any] = []
    if payload.text is not None:
        fields.append("text = ?")
        values.append(payload.text.strip())
    if payload.parent_id_set:
        fields.append("parent_id = ?")
        values.append(payload.parent_id)
        # When reparenting, inherit is_followup from the new root ancestor.
        if payload.parent_id is not None:
            inherited = _root_is_followup(conn, payload.parent_id)
            fields.append("is_followup = ?")
            values.append(int(inherited))
    if payload.is_blocker is not None:
        fields.append("is_blocker = ?")
        values.append(int(payload.is_blocker))
    if payload.is_followup is not None:
        fields.append("is_followup = ?")
        values.append(int(payload.is_followup))
    if payload.in_progress is not None:
        fields.append("in_progress = ?")
        values.append(int(payload.in_progress))
    if payload.display_order is not None:
        fields.append("display_order = ?")
        values.append(payload.display_order)
    if payload.completed is not None:
        fields.append("completed = ?")
        values.append(int(payload.completed))
        if payload.completed and not row["completed"]:
            fields.append("completed_at = ?")
            values.append(_now())
            fields.append("in_progress = ?")
            values.append(0)
        elif not payload.completed:
            fields.append("completed_at = ?")
            values.append(None)
    if payload.notes is not None:
        fields.append("notes = ?")
        values.append(json.dumps(payload.notes))
    if payload.paths is not None:
        fields.append("paths_json = ?")
        values.append(json.dumps(payload.paths))
    if payload.effort_set:
        fields.append("effort = ?")
        values.append(payload.effort)

    if not fields:
        return _todo_to_dict(row)

    fields.append("updated_at = ?")
    values.append(_now())
    values.append(todo_id)
    conn.execute(
        f"UPDATE todos SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    return _todo_to_dict(row)


@app.delete("/api/todos/{todo_id}", status_code=204)
async def delete_todo(todo_id: int):
    conn = db.get_conn()
    cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="todo not found")
    return None


@app.post("/api/todos/reorder")
async def reorder_todos(updates: list[TodoReorderItem]):
    """
    Atomically update parent_id and display_order for a set of todos.
    All todos must belong to the same project. Frontend sends the full
    card's worth of todos after each drag, re-indexed in 10s gaps.
    """
    if not updates:
        return {"updated": 0}
    conn = db.get_conn()

    # Validate all ids exist and belong to the same project.
    ids = [u.id for u in updates]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, project_id FROM todos WHERE id IN ({placeholders})", ids
    ).fetchall()
    found = {r["id"]: r["project_id"] for r in rows}
    if len(found) != len(ids):
        missing = set(ids) - set(found)
        raise HTTPException(status_code=404, detail=f"todo ids not found: {missing}")
    project_ids = set(found.values())
    if len(project_ids) > 1:
        raise HTTPException(status_code=400, detail="all todos must belong to the same project")

    now = _now()
    with db.transaction() as txn:
        for u in updates:
            txn.execute(
                "UPDATE todos SET parent_id = ?, display_order = ?, updated_at = ? WHERE id = ?",
                (u.parent_id, u.display_order, now, u.id),
            )
        # When a todo is reparented, sync its is_followup to the root ancestor.
        for u in updates:
            if u.parent_id is not None:
                inherited = _root_is_followup(txn, u.parent_id)
                txn.execute(
                    "UPDATE todos SET is_followup = ? WHERE id = ?",
                    (int(inherited), u.id),
                )
        # For root items, apply the explicit is_followup flag when provided.
        for u in updates:
            if u.parent_id is None and u.is_followup is not None:
                txn.execute(
                    "UPDATE todos SET is_followup = ? WHERE id = ?",
                    (int(u.is_followup), u.id),
                )

    return {"updated": len(updates)}


@app.post("/api/todos/{todo_id}/complete")
async def complete_todo(todo_id: int):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="todo not found")
    now = _now()
    conn.execute(
        "UPDATE todos SET completed = 1, in_progress = 0, completed_at = ?, updated_at = ? WHERE id = ?",
        (now, now, todo_id),
    )
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    return _todo_to_dict(row)


@app.post("/api/todos/{todo_id}/uncomplete")
async def uncomplete_todo(todo_id: int):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="todo not found")
    now = _now()
    conn.execute(
        "UPDATE todos SET completed = 0, completed_at = NULL, updated_at = ? WHERE id = ?",
        (now, todo_id),
    )
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    return _todo_to_dict(row)


# ---------------------------------------------------------------------------
# API: stats (data ready for v2 progress viz)
# ---------------------------------------------------------------------------

@app.get("/api/stats")
async def stats(
    since: Optional[str] = Query(default=None),
    tz_offset_minutes: int = Query(default=0),
):
    """
    Return completed-todo effort sums grouped by local calendar day.

    `since` is a local ISO date; defaults to 30 days ago.
    `tz_offset_minutes` is JS Date.getTimezoneOffset() — minutes UTC is ahead
    of local time (e.g. 420 for UTC-7). The backend negates it to shift UTC
    timestamps into the caller's local date before grouping.
    """
    conn = db.get_conn()
    if since is None:
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()

    # SQLite modifier to convert UTC stored timestamps → local calendar date.
    # getTimezoneOffset() is positive when local is behind UTC, so negate it.
    offset_modifier = f"{-tz_offset_minutes} minutes"

    rows = conn.execute(
        "SELECT date(completed_at, ?) AS day, project_id, "
        "       SUM(COALESCE(effort, 0.5)) AS n "
        "FROM todos "
        "WHERE completed = 1 AND completed_at IS NOT NULL "
        "  AND date(completed_at, ?) >= date(?) "
        "GROUP BY day, project_id "
        "ORDER BY day, project_id",
        (offset_modifier, offset_modifier, since),
    ).fetchall()

    by_day: dict[str, dict[str, float]] = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[str(r["project_id"])] = float(r["n"])

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM todos "
        "WHERE completed = 1 AND date(completed_at, ?) >= date(?)",
        (offset_modifier, since),
    ).fetchone()["n"]

    return {
        "since": since,
        "total_completed": int(total),
        "by_day": by_day,
    }


# ---------------------------------------------------------------------------
# Day overrides (click-to-toggle holiday / workday on heatmap dots)
# ---------------------------------------------------------------------------

class DayOverrideIn(BaseModel):
    date: str  # YYYY-MM-DD


@app.get("/api/day-overrides")
async def get_day_overrides():
    rows = db.get_conn().execute("SELECT date FROM day_overrides").fetchall()
    return {"dates": [r["date"] for r in rows]}


@app.post("/api/day-overrides/toggle")
async def toggle_day_override(body: DayOverrideIn):
    conn = db.get_conn()
    existing = conn.execute(
        "SELECT 1 FROM day_overrides WHERE date = ?", (body.date,)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM day_overrides WHERE date = ?", (body.date,))
    else:
        conn.execute("INSERT INTO day_overrides (date) VALUES (?)", (body.date,))
    rows = conn.execute("SELECT date FROM day_overrides").fetchall()
    return {"dates": [r["date"] for r in rows]}
