# 0004 — LLM-generated todo titles

| Field | Value |
|-------|-------|
| Status | complete |
| Tracks | — |
| Depends on | `jobs/2026-05-14-llm-title-service.md` — Ollama + `llm-service` endpoint must be up on `organizer-ct` before this job starts |
| Part of | [`jobs/2026-05-14-llm-todo-title-harness.md`](../../jobs/2026-05-14-llm-todo-title-harness.md) |

---

## Objective

When the user types a long or stream-of-consciousness description into
"Add todo...", the Organizer should:

1. Immediately create the todo with a truncated-words placeholder title and
   the raw input preserved as the first context Note.
2. Show a visible pulsing state on the todo item while the LLM generates a
   concise title.
3. Replace the placeholder title with the generated 1–2 line title when the
   LLM returns.
4. Fall back gracefully (keep the truncated title, clear the pulse) if the
   LLM service is unavailable.

Success looks like: the existing "Add todo..." UX is unchanged for short
inputs; long inputs silently upgrade themselves to concise titles within a
few seconds; the raw input is always recoverable from the Notes panel.

---

## Background — what's already in place

- `POST /api/todos` creates a todo with `text`, `notes` (list of strings),
  `paths_json`, etc. `TodoCreate` schema in `main.py` line ~135.
- `PATCH /api/todos/<id>` updates any subset of fields including `text` and
  `notes`. `TodoUpdate` schema in `main.py` line ~155.
- `_todo_to_dict` serialises rows to JSON; notes come back as a list of
  strings. `main.py` line ~54.
- The SPA renders each todo from `state.data` (a project/todo tree). Edits
  call `apiFetch` helpers and then `reload()`.
- The existing `in_progress` boolean on todos is rendered as a left-border
  visual indicator in the UI. We do **not** overload `in_progress` for LLM
  state — it already has a user-facing meaning (task is currently being
  worked on). We use a new `title_status` column instead.
- The Settings pane (job 0003, planning) will add an `organizer.settings`
  localStorage key — we can put LLM-related settings (threshold input
  length, enable/disable) there in a later job. Out of scope here.

---

## Design decisions

### New DB column: `title_status`

Add `title_status TEXT NOT NULL DEFAULT ''` to the `todos` table.
No NOT NULL violation because the default is `''`.

Values:
| Value | Meaning |
|-------|---------|
| `''` | Normal todo — no LLM involved (existing todos + short-input new todos) |
| `'pending'` | LLM call in-flight or not yet started |
| `'generated'` | Title was successfully replaced by the LLM |
| `'failed'` | LLM call failed/timed out; text holds the fallback truncated title |

The status is persisted so the UI survives a browser reload mid-generation.
A server startup sweep (in the FastAPI `lifespan` handler) finds any rows
still `'pending'` after a restart (abandoned background tasks) and marks
them `'failed'`. This prevents a spinner that never clears.

### When to trigger LLM generation

Trigger when the submitted `raw_input` is "long enough to benefit" — not
every todo. Threshold: `len(raw_input.strip()) > 60` characters (roughly
10+ words). This avoids burning LLM time on already-concise inputs like
"buy milk" or "fix typo in README". The threshold will move to settings
once job 0003 ships.

Short input path (`len ≤ 60`): `text = raw_input`, `title_status = ''`,
no LLM call. Identical to existing behaviour.

Long input path (`len > 60`): `text = fallback_title(raw_input)`,
`notes[0] = raw_input`, `title_status = 'pending'`, background task spawned.

### Fallback title

```python
def _fallback_title(raw: str, max_words: int = 9) -> str:
    words = raw.split()
    if len(words) <= max_words:
        return raw.strip()
    return " ".join(words[:max_words]) + "…"
```

Used as the initial placeholder and as the recovery text on failure.

### Backend async plumbing

The `POST /api/todos` handler will:
1. Write the todo synchronously (returns 201 immediately with the new row).
2. If `title_status = 'pending'`, call `asyncio.create_task(_generate_title(todo_id, raw_input))`.

`_generate_title(todo_id, raw_input)`:
- Posts to `http://127.0.0.1:8553/generate-todo-title` via `httpx.AsyncClient`
  with a timeout matching `LLM_TIMEOUT_S` (read from `LLM_SERVICE_URL` env).
- On success (`status == "ok"`): `UPDATE todos SET text=?, title_status='generated', updated_at=? WHERE id=?`
- On failure/timeout: `UPDATE todos SET title_status='failed', updated_at=? WHERE id=?`

The LLM service URL is read from the `LLM_SERVICE_URL` environment variable
(default `http://127.0.0.1:8553`). If unset or if the background task crashes,
the todo degrades gracefully to `title_status='failed'`.

`httpx` must be added to `organizer/requirements.txt`.

### Frontend pending-state UI

When a todo has `title_status == 'pending'`:
- Render the todo title in a `<span class="title-pending">` wrapper.
- CSS: `@keyframes title-pulse` — subtle opacity oscillation (0.5 → 1.0 →
  0.5) over 1.4 s, infinite. No spinner (keeps the list visually clean).
- The item is fully draggable and interactive while pending.

When `title_status == 'generated'` or `'failed'` or `''`:
- Render normally (no pulse wrapper).

The animation replaces the existing plain text render for that one state;
everything else (drag handles, action buttons, Notes panel) is unchanged.

### Frontend polling

While any visible todo has `title_status == 'pending'`, the SPA polls
`GET /api/projects` every 2 seconds (using `setTimeout`, not `setInterval`,
so polls don't stack). The poll stops as soon as no pending todos remain.

Polling starts automatically when the initial load or any `reload()` call
finds at least one pending todo. A page visibility listener (`visibilitychange`)
pauses polling when the tab is hidden and resumes on focus — avoids background
battery drain on mobile.

### `raw_input` in `POST /api/todos`

`TodoCreate` gains a new optional field:

```python
raw_input: Optional[str] = None  # if provided, triggers LLM title generation
```

The frontend sends `raw_input` only when the "Add todo..." text is long
enough. For short inputs (or todos created programmatically), `raw_input`
is absent/null and the behaviour is identical to today.

### No new API endpoint needed

The existing `PATCH /api/todos/<id>` endpoint already accepts `text` and
`notes` updates. The background task writes directly to the DB rather than
calling its own API (avoids the overhead and circular dependency of a
self-call). `title_status` updates require a small addition to `TodoUpdate`
(internal use only, not exposed to the frontend in v1).

---

## Plan

### Step 1 — DB migration: add `title_status` column

In `organizer/db.py`, add to `SCHEMA_SQL` after the `todos` table definition:

```sql
ALTER TABLE todos ADD COLUMN title_status TEXT NOT NULL DEFAULT '';
```

Wrap in the existing pattern used for additive migrations — add the `ALTER`
statement in a separate execution block guarded by a check for the column:

```python
# In init_schema(), after the main CREATE TABLE block:
cols = {r[1] for r in conn.execute("PRAGMA table_info(todos)").fetchall()}
if "title_status" not in cols:
    conn.execute("ALTER TABLE todos ADD COLUMN title_status TEXT NOT NULL DEFAULT ''")
```

Expected outcome: existing todos have `title_status = ''`; new deployments
get the column from the base `CREATE TABLE` (add to `SCHEMA_SQL` as well for
clean installs).

### Step 2 — Serialisation: expose `title_status` in `_todo_to_dict`

```python
d["title_status"] = d.get("title_status") or ""
```

This ensures the field appears in `GET /api/projects` responses. The
frontend will use it to drive the pending-pulse rendering.

### Step 3 — `TodoCreate` + `TodoUpdate` schema changes

`TodoCreate`:
```python
raw_input: Optional[str] = None
```

`TodoUpdate` (internal flag, not normally sent by frontend):
```python
title_status: Optional[str] = None  # internal: 'pending'|'generated'|'failed'|''
```

Add a validator that only permits known values.

### Step 4 — Fallback title helper + background task in `main.py`

Add at module level (after imports):

```python
import asyncio
import httpx

LLM_SERVICE_URL = os.environ.get("LLM_SERVICE_URL", "http://127.0.0.1:8553")
LLM_TITLE_THRESHOLD = 60  # chars; inputs longer than this trigger LLM generation

def _fallback_title(raw: str, max_words: int = 9) -> str:
    words = raw.split()
    return raw.strip() if len(words) <= max_words else " ".join(words[:max_words]) + "…"

async def _generate_title(todo_id: int, raw_input: str) -> None:
    """Background task: call LLM service, update todo on success or failure."""
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(
                f"{LLM_SERVICE_URL}/generate-todo-title",
                json={"raw_text": raw_input, "prompt_version": "todo_title_v1"},
            )
            data = resp.json()
        if resp.status_code == 200 and data.get("status") == "ok" and data.get("title"):
            new_text = data["title"]
            new_status = "generated"
        else:
            new_text = None
            new_status = "failed"
    except Exception:
        new_text = None
        new_status = "failed"

    conn = db.get_conn()
    now = _now()
    if new_text:
        conn.execute(
            "UPDATE todos SET text=?, title_status=?, updated_at=? WHERE id=?",
            (new_text, new_status, now, todo_id),
        )
    else:
        conn.execute(
            "UPDATE todos SET title_status=?, updated_at=? WHERE id=?",
            (new_status, now, todo_id),
        )
```

### Step 5 — Modify `POST /api/todos` handler

In the `create_todo` handler, after writing the row to DB:

```python
# ... existing INSERT ...
new_id = cur.lastrowid

if payload.raw_input and len(payload.raw_input.strip()) > LLM_TITLE_THRESHOLD:
    asyncio.create_task(_generate_title(new_id, payload.raw_input.strip()))
```

And when constructing the INSERT:

- If `raw_input` is long enough: `text = _fallback_title(raw_input)`,
  `title_status = 'pending'`, prepend `raw_input` to `notes`.
- Otherwise: `text = payload.text` (existing behaviour), `title_status = ''`.

### Step 6 — Startup sweep for abandoned `'pending'` todos

In the `lifespan` handler (runs at startup):

```python
conn = db.get_conn()
abandoned = conn.execute(
    "SELECT COUNT(*) FROM todos WHERE title_status = 'pending'"
).fetchone()[0]
if abandoned:
    conn.execute(
        "UPDATE todos SET title_status='failed', updated_at=? WHERE title_status='pending'",
        (_now(),),
    )
    print(f"[startup] marked {abandoned} abandoned pending-title todos as failed")
```

### Step 7 — Add `httpx` to requirements

```
httpx>=0.27
```

### Step 8 — Frontend: pending-pulse CSS

In `organizer/static/index.html`, in the `<style>` block:

```css
@keyframes title-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.45; }
}
.title-pending {
  animation: title-pulse 1.4s ease-in-out infinite;
}
```

### Step 9 — Frontend: render `title_status` on todo items

In the `renderTodo` (or equivalent) function, when rendering the todo's
title text node:

```js
const titleEl = el('span', {}, todo.text);
if (todo.title_status === 'pending') {
  titleEl.classList.add('title-pending');
}
// Attach titleEl to the todo row DOM as before
```

No change to click handlers, drag logic, or the Notes panel — the animation
class is purely presentational.

### Step 10 — Frontend: polling loop

Add near the top of the JS section (alongside `state`):

```js
let _pendingPollTimer = null;

function _hasPendingTitles() {
  for (const cat of Object.values(state.data?.projects ?? {})) {
    for (const proj of cat) {
      for (const todo of proj.todos ?? []) {
        if (todo.title_status === 'pending') return true;
      }
    }
  }
  return false;
}

function _schedulePendingPoll() {
  if (_pendingPollTimer) return; // already scheduled
  _pendingPollTimer = setTimeout(async () => {
    _pendingPollTimer = null;
    await reload();            // existing reload() function
    if (_hasPendingTitles()) _schedulePendingPoll();
  }, 2000);
}
```

Call `_schedulePendingPoll()` at the end of `reload()` if `_hasPendingTitles()`.

Pause on hidden tabs:

```js
document.addEventListener('visibilitychange', () => {
  if (document.hidden && _pendingPollTimer) {
    clearTimeout(_pendingPollTimer);
    _pendingPollTimer = null;
  } else if (!document.hidden && _hasPendingTitles()) {
    _schedulePendingPoll();
  }
});
```

### Step 11 — Frontend: submit `raw_input` when adding a todo

In the "Add todo..." submit handler, when calling `POST /api/todos`:

```js
const rawInput = inputEl.value.trim();
const isLong = rawInput.length > 60;
const body = {
  project_id: projectId,
  text: isLong ? _fallbackTitle(rawInput) : rawInput,
  raw_input: isLong ? rawInput : undefined,
  // ... other fields ...
};
```

Add a matching `_fallbackTitle` JS helper:

```js
function _fallbackTitle(raw, maxWords = 9) {
  const words = raw.trim().split(/\s+/);
  return words.length <= maxWords ? raw.trim() : words.slice(0, maxWords).join(' ') + '…';
}
```

The `text` field still needs to be set in the POST (the backend uses it as
the canonical value when `raw_input` is absent or short); when both are
present and `raw_input` is long, the backend will override `text` with the
`_fallback_title` anyway.

### Step 12 — Deploy

Backend changes (DB migration fires automatically on restart):

```bash
scp organizer/main.py organizer/db.py organizer/requirements.txt \
    root@100.90.75.10:/opt/organizer/
ssh root@100.90.75.10 '/opt/organizer/venv/bin/pip install -r /opt/organizer/requirements.txt \
  && systemctl restart organizer'
```

Frontend (no restart needed):

```bash
scp organizer/static/index.html root@100.90.75.10:/opt/organizer/static/index.html
```

### Step 13 — End-to-end smoke test

1. Open `https://organizer.manx-celsius.ts.net/`.
2. In any project, type a long messy task into "Add todo..." (>60 chars).
3. Submit. Verify the new todo appears immediately with a pulsing title.
4. Wait ≤30 s. Verify the pulse clears and the title is concise.
5. Open the Notes panel on the todo. Verify the first note is the original
   raw input, unmodified.
6. Stop `llm-service` on the LXC. Repeat steps 2–3. Verify:
   - Todo still appears immediately.
   - Pulse eventually clears (within `LLM_TIMEOUT_S + 5` s).
   - Title falls back to the truncated-words form.
   - Notes still contain the raw input.
7. Restart `llm-service`.

---

## Open questions

- **Short-input threshold (60 chars):** may need tuning. If too many false
  positives (good short todos getting pulsed), increase it. Will move to the
  settings pane once job 0003 ships.
- **`raw_input` visibility in the Notes panel:** the first note will just
  show the raw text. No special labelling ("original entry:") for v1. If
  that's confusing, we can prefix it in a future job.
- **iOS PWA:** confirm the `@keyframes` animation runs in standalone mode.
  Safari sometimes throttles animations in background tabs, which is fine
  (tab hidden = polling paused), but the animation should still be smooth
  when the PWA is in the foreground.

---

## Files touched

- `organizer/db.py` — `title_status` column migration in `init_schema`
- `organizer/main.py` — `TodoCreate` `raw_input` field; `_fallback_title`;
  `_generate_title` background task; `create_todo` handler changes; lifespan
  startup sweep; `_todo_to_dict` `title_status` field; `httpx` import;
  `LLM_SERVICE_URL` env read
- `organizer/requirements.txt` — add `httpx`
- `organizer/static/index.html` — `title-pending` CSS keyframe; render
  `title_status`; `_fallbackTitle` JS helper; `raw_input` in submit handler;
  `_hasPendingTitles` + `_schedulePendingPoll` polling loop

---

## Execution log

### 2026-05-14 13:28 — Steps 1–7: backend changes

**Step 1 — db.py:** Added `title_status TEXT NOT NULL DEFAULT ''` to `SCHEMA_SQL` `CREATE TABLE todos`. Added `_migrate_add_title_status(conn)` migration function (PRAGMA table_info guard pattern, identical to existing migrations). Called at end of `init_schema()`.

**Step 2 — `_todo_to_dict`:** Added `d["title_status"] = d.get("title_status") or ""` to expose field in all API responses.

**Step 3 — schemas:**
- `TodoCreate` gained `raw_input: Optional[str] = None`.
- `TodoUpdate` gained `title_status: Optional[str] = None` with `_check_title_status` validator permitting `''|'pending'|'generated'|'failed'`.

**Step 4 — helpers + background task:** Added `import asyncio`, `import httpx`, `import os` at module top. Added module-level `LLM_SERVICE_URL = os.environ.get("LLM_SERVICE_URL", "http://127.0.0.1:8553")` and `LLM_TITLE_THRESHOLD = 60`. Implemented `_fallback_title(raw, max_words=9)` and `async def _generate_title(todo_id, raw_input)` per spec.

**Step 5 — `create_todo` handler:** When `raw_input` is present and `len > 60`, sets `todo_text = _fallback_title(raw)`, prepends raw to notes, sets `title_status = 'pending'` in INSERT, spawns `asyncio.create_task(_generate_title(tid, raw))`. Short-input path is unchanged.

**Step 6 — startup sweep:** Added abandoned-pending sweep in `lifespan` after `db.init_schema()`.

**Step 7 — requirements.txt:** Added `httpx>=0.27`.

### 2026-05-14 13:35 — Steps 8–11: frontend changes

**Step 8 — CSS:** Added `@keyframes title-pulse` (opacity 1→0.45→1, 1.4s ease-in-out) and `.title-pending { animation: title-pulse ... }` after `.todo-text:focus` block.

**Step 9 — `renderTodoNode`:** `todo-text` div class now conditionally includes `title-pending` when `t.title_status === 'pending'`.

**Step 10 — polling:** Added `_pendingPollTimer`, `_hasPendingTitles()`, `_schedulePendingPoll()` after `state` block. Added `if (_hasPendingTitles()) _schedulePendingPoll()` call at end of `reload()`. Added `visibilitychange` listener after boot `reload()` call. Corrected job plan's `state.data?.projects` → `state.projects` (actual frontend variable name).

**Step 11 — submit + drag-drop:** Added `_fallbackTitle(raw, maxWords=9)` JS helper before `renderAddTodoInput`. Updated `submit()` to send `raw_input` when `rawInput.length > 60`. Updated `drop` handler identically.

### 2026-05-14 13:40 — Step 12: deploy

```
# .env written
ssh root@100.90.75.10 'echo "LLM_SERVICE_URL=http://100.68.34.113:8553" > /opt/organizer/.env'
# → LLM_SERVICE_URL=http://100.68.34.113:8553

# files copied
scp organizer/requirements.txt organizer/db.py organizer/main.py root@100.90.75.10:/opt/organizer/
# → scp ok

# httpx installed
ssh root@100.90.75.10 '/opt/organizer/venv/bin/pip install -r /opt/organizer/requirements.txt --quiet'
# → pip ok

# service restarted
ssh root@100.90.75.10 'systemctl restart organizer && sleep 2 && systemctl is-active organizer'
# → active

# frontend deployed
scp organizer/static/index.html root@100.90.75.10:/opt/organizer/static/index.html
# → frontend scp ok
```

Startup log: clean — no errors, no abandoned pending todos on first start.

Migration verified:
```
sqlite3 /opt/organizer/data/organizer.db "PRAGMA table_info(todos);" | grep title_status
# → 15|title_status|TEXT|1|''|0
```

API response verified: `title_status: ""` present on existing todos.

### 2026-05-14 13:44 — Step 13: end-to-end smoke test

**Happy path (LLM available):**

```
POST /api/todos { project_id: 1, raw_input: "I need to fix the pipeline stage 1 OOM issue by profiling memory usage during the parquet sink phase and reducing batch sizes accordingly" }
→ id: 151, text: "I need to fix the pipeline stage 1 OOM…", title_status: "pending"
→ notes[0]: "I need to fix the pipeline stage 1 OOM issue..." (raw input preserved)
```

After ~40s:
```
sqlite3: 151|Profile Parquet Sink Memory Usage|generated|[...]
```

LLM generated "Profile Parquet Sink Memory Usage". `title_status = "generated"`. Raw input in `notes[0]`. ✓

**Fallback path (LLM service stopped):**

```
pct exec 302 -- systemctl stop llm-service
POST /api/todos { project_id: 1, raw_input: "I need to refactor the data ingestion pipeline to handle large files more efficiently by implementing streaming reads and chunked processing" }
→ id: 152, text: "I need to refactor the data ingestion pipeline to…", title_status: "pending"
```

After 40s (httpx 35s timeout + buffer):
```
sqlite3: 152|I need to refactor the data ingestion pipeline to…|failed
```

Fallback title preserved. `title_status = "failed"`. Raw input in `notes[0]`. ✓

llm-service restarted. Test todos deleted.

---

## Post-completion state

All 13 steps executed and verified. The feature is live at `https://organizer.manx-celsius.ts.net/`.

- `organizer/db.py`: `title_status TEXT NOT NULL DEFAULT ''` column added via `_migrate_add_title_status`; existing rows all have `''`.
- `organizer/main.py`: `httpx>=0.27` dependency; `LLM_SERVICE_URL` env var; `_fallback_title`; `_generate_title` background task; `TodoCreate.raw_input`; `TodoUpdate.title_status`; lifespan startup sweep; `create_todo` async dispatch.
- `organizer/requirements.txt`: `httpx>=0.27` added.
- `organizer/static/index.html`: `title-pulse` CSS keyframe; `renderTodoNode` conditional class; `_fallbackTitle` JS helper; `submit()` + `drop` handler send `raw_input`; polling loop (`_schedulePendingPoll`, `_hasPendingTitles`) with `visibilitychange` pause.
- `organizer-ct` env: `/opt/organizer/.env` contains `LLM_SERVICE_URL=http://100.68.34.113:8553`.
- End-to-end verified: happy path generates "Profile Parquet Sink Memory Usage" in ~40s; failure path falls back cleanly to truncated title with `title_status='failed'`.
