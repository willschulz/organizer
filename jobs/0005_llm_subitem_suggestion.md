# 0005 — LLM sub-item suggestion (V2)

| Field | Value |
|-------|-------|
| Status | planning |
| Tracks | Organizer todo — V2: LLM sub-item suggestion |
| Depends on | 0004 — `title_status` / `_generate_title` pattern; llm-service running qwen3:8b |
| Behavioral context | When a user enters a long todo (>60 chars), the Organizer already generates a short title in the background. This job adds a second background task that uses qwen3:8b with thinking enabled to suggest 3–5 concrete sub-items. Suggestions appear non-intrusively below the todo card; nothing is written until the user explicitly accepts. The goal is to surface useful structure without polluting the workspace with slop. |

---

## Objective

After creating a long todo, the Organizer fires a second async LLM call that
produces 3–5 suggested sub-item texts. When they arrive, a collapsible
"Suggested sub-items" panel appears beneath the todo card. The user can accept
any subset (each becomes a new todo on the same project) or dismiss all.
Nothing is written automatically.

Success looks like: entering "Design and implement a reproducible sweep
infrastructure for the capping experiment, including parameter logging,
artifact storage, and comparison tooling" creates the todo, triggers title
generation (existing), and within 40–90s a suggestions panel appears with
sensible subtasks. Accepting one creates a new todo; dismissing hides the
panel and clears the stored suggestions.

---

## Background — what's already in place

- `title_status` column + `_generate_title` background task in
  `organizer/main.py` — the suggestions feature mirrors this pattern exactly.
- Frontend 2s polling loop (triggered when any todo has `title_status ==
  'pending'`) — extend to also poll when `suggestions_status == 'pending'`.
- llm-service at `http://100.68.34.113:8553` with `_call_ollama` function;
  `think` is currently hardcoded to `False` in the payload. We need to make
  `think` a parameter so the subitem endpoint can pass `True`.
- `prompts.py` holds versioned prompt dicts — add `subitem_v1` there.
- `qwen3:8b` already pulled and tested on `llm-ct`; thinking mode produces
  good sub-item suggestions at 40–90s latency.

---

## Design decisions

- **DB columns:** `suggestions_json TEXT NOT NULL DEFAULT '[]'` and
  `suggestions_status TEXT NOT NULL DEFAULT ''` on the `todos` table.
  `suggestions_status` follows the same state machine as `title_status`:
  `''` → `'pending'` → `'generated'` | `'failed'`.
  `suggestions_json` holds a JSON array of strings (the suggested task texts).

- **`_call_ollama` refactor:** add a `think: bool = False` keyword argument.
  Title calls pass `think=False` (unchanged behaviour); sub-item calls pass
  `think=True`.

- **New llm-service endpoint:** `POST /suggest-subitems`
  - Request: `{ "raw_text": "...", "prompt_version": "subitem_v1" }`
  - Response: `{ "items": ["...", "..."], "status": "ok", "duration_ms": ... }`
  - The prompt instructs the model to return a JSON array of 3–5 task strings,
    no prose, no numbering, each ≤12 words.
  - `_sanitise_subitems` validates: is a list, 1–7 items, each a non-empty
    string ≤80 chars, not starting with a list marker.

- **New organizer background task:** `_generate_subitems(todo_id, raw_text)`
  mirrors `_generate_title`. Fired from `create_todo` alongside
  `_generate_title` when the todo is long.

- **API surface change:** `_todo_to_dict` gains `suggestions_json` and
  `suggestions_status` fields. No new GET endpoint — piggybacked on the
  existing `/api/projects` response the frontend already polls.

- **Frontend suggestions panel:** rendered inside `renderTodoNode` when
  `t.suggestions_status === 'generated'` and `t.suggestions_json.length > 0`.
  Panel is below the todo text, visually subordinate. Each suggested item is
  a row with text + **Accept** button + (only on hover) **×** to remove that
  item. A "Dismiss all" link at the bottom clears the panel.

  - Accept: `POST /api/todos` with `project_id`, `text` set to the suggestion,
    `is_followup: false`. Then refetch.
  - Dismiss a single item: `PATCH /api/todos/{id}` with the updated
    `suggestions_json` (array minus that item); set `suggestions_status` to
    `''` if array becomes empty.
  - Dismiss all: `PATCH /api/todos/{id}` with `suggestions_json: '[]'` and
    `suggestions_status: ''`.

- **Polling extension:** `_hasPendingTitles` (already exists) is renamed/
  extended to `_hasPendingLLM` and also returns `true` if any todo has
  `suggestions_status === 'pending'`.

- **Startup sweep:** the lifespan handler already resets abandoned
  `title_status == 'pending'` rows to `'failed'`. Extend to also reset
  abandoned `suggestions_status == 'pending'` rows.

- **Cautious rollout:** suggestions only appear for long todos. A short todo
  entered manually never triggers the feature. No suggestions are accepted
  without an explicit click. The UI panel is collapsible so it doesn't
  dominate the card.

---

## Plan

### Step 1 — DB migration

In `organizer/db.py`:

```python
def _migrate_add_suggestions(conn: sqlite3.Connection) -> None:
    """Idempotent: add suggestions columns to todos for LLM sub-item suggestions."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(todos)").fetchall()}
    if "suggestions_json" not in cols:
        conn.execute(
            "ALTER TABLE todos ADD COLUMN suggestions_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "suggestions_status" not in cols:
        conn.execute(
            "ALTER TABLE todos ADD COLUMN suggestions_status TEXT NOT NULL DEFAULT ''"
        )
```

Call it from `init_schema()` after the existing `_migrate_add_title_status` call.

### Step 2 — llm-service: make `think` a parameter

In `llm-service/main.py`, change `_call_ollama` signature and payload:

```python
async def _call_ollama(
    model: str,
    system_prompt: str,
    user_prompt: str,
    think: bool = False,
) -> tuple[str, float]:
    payload = {
        "model": model,
        "stream": False,
        "think": think,
        "options": { ... },
        "messages": [ ... ],
    }
```

The title endpoint continues to call `_call_ollama(..., think=False)` (default,
no change in behaviour).

### Step 3 — llm-service: add `subitem_v1` prompt

In `llm-service/prompts.py`, add a new entry:

```python
"subitem_v1": {
    "system": (
        "You are a task decomposition assistant. "
        "Given a task description, produce a JSON array of 3 to 5 concrete sub-tasks "
        "that together would complete the parent task. "
        "Rules: output ONLY the JSON array, no prose, no markdown, no numbering. "
        "Each element is a plain string, ≤12 words, imperative mood. "
        "Do not repeat the parent task. "
        "Example output: [\"Draft outline\", \"Run baseline experiment\", \"Write results section\"]"
    ),
    "user_template": "Task: {raw_text}",
},
```

### Step 4 — llm-service: add `/suggest-subitems` endpoint

In `llm-service/main.py`:

```python
class SubitemRequest(BaseModel):
    raw_text: str = Field(..., min_length=1)
    prompt_version: str = Field(default="subitem_v1")
    model: Optional[str] = Field(default=None)

class SubitemResponse(BaseModel):
    items: list[str]
    model: str
    prompt_version: str
    duration_ms: int
    status: str  # "ok" | "parse_error" | "empty_output" | "timeout" | "ollama_error"

def _sanitise_subitems(raw: str) -> tuple[list[str], str]:
    import json, re
    text = raw.strip()
    # strip markdown code fences if present
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        items = json.loads(text)
    except Exception:
        return [], "parse_error"
    if not isinstance(items, list) or not items:
        return [], "empty_output"
    cleaned = []
    for item in items[:7]:
        s = str(item).strip()
        if s and len(s) <= 80 and not s[0] in ("-", "*", "•", "1."):
            cleaned.append(s)
    return (cleaned, "ok") if cleaned else ([], "empty_output")

@app.post("/suggest-subitems", response_model=SubitemResponse)
async def suggest_subitems(req: SubitemRequest) -> SubitemResponse:
    model = req.model or config.DEFAULT_MODEL
    prompt = get_prompt(req.prompt_version)
    user_message = prompt["user_template"].format(raw_text=req.raw_text)
    try:
        raw_output, elapsed = await _call_ollama(
            model=model,
            system_prompt=prompt["system"],
            user_prompt=user_message,
            think=True,
        )
    except httpx.TimeoutException:
        ...  # return SubitemResponse with status="timeout"
    except httpx.HTTPError:
        ...  # return SubitemResponse with status="ollama_error"
    items, status = _sanitise_subitems(raw_output)
    return SubitemResponse(items=items, model=model, prompt_version=req.prompt_version,
                           duration_ms=int(elapsed*1000), status=status)
```

### Step 5 — organizer backend: `_generate_subitems` task

In `organizer/main.py`:

```python
async def _generate_subitems(todo_id: int, raw_input: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{LLM_SERVICE_URL}/suggest-subitems",
                json={"raw_text": raw_input, "prompt_version": "subitem_v1"},
            )
            data = resp.json()
        if resp.status_code == 200 and data.get("status") == "ok" and data.get("items"):
            new_json = json.dumps(data["items"])
            new_status = "generated"
        else:
            new_json = "[]"
            new_status = "failed"
    except Exception:
        new_json = "[]"
        new_status = "failed"
    conn = db.get_conn()
    conn.execute(
        "UPDATE todos SET suggestions_json=?, suggestions_status=?, updated_at=? WHERE id=?",
        (new_json, new_status, _now(), todo_id),
    )
```

Fire from `create_todo` alongside `asyncio.create_task(_generate_title(...))`:

```python
asyncio.create_task(_generate_subitems(tid, raw))
```

Extend `TodoUpdate` to accept `suggestions_json` and `suggestions_status` (with
the same validator pattern as `title_status`). Extend `update_todo` handler
to write them.

Extend the lifespan startup sweep to also reset `suggestions_status = 'pending'`
→ `'failed'`.

Expose both fields in `_todo_to_dict`:

```python
d["suggestions_json"]   = json.loads(d.get("suggestions_json") or "[]")
d["suggestions_status"] = d.get("suggestions_status") or ""
```

### Step 6 — Frontend

**Polling:** rename/extend `_hasPendingTitles` to `_hasPendingLLM`:

```js
function _hasPendingLLM() {
  for (const cat of Object.values(state.projects)) {
    for (const proj of cat) {
      for (const todo of proj.todos ?? []) {
        if (todo.title_status === 'pending' || todo.suggestions_status === 'pending') return true;
      }
    }
  }
  return false;
}
```

Update all call sites (`_schedulePendingPoll`, `reload`, `visibilitychange` listener).

**Suggestions panel CSS:**

```css
.suggestions-panel {
  margin-top: 6px;
  padding: 8px 10px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 0.82rem;
}
.suggestions-panel .suggestions-label {
  color: var(--text-muted);
  font-size: 0.75rem;
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.suggestion-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
}
.suggestion-row .suggestion-text { flex: 1; }
.suggestion-row .btn-accept {
  font-size: 0.75rem;
  padding: 2px 7px;
  border-radius: 4px;
  background: var(--accent);
  color: #fff;
  border: none;
  cursor: pointer;
}
.suggestions-dismiss {
  margin-top: 4px;
  font-size: 0.75rem;
  color: var(--text-muted);
  cursor: pointer;
  text-decoration: underline;
}
```

**`renderTodoNode` extension:** after the existing todo-text div, if
`t.suggestions_status === 'generated' && t.suggestions_json.length > 0`, append
a `.suggestions-panel` built with the `el(...)` helper. Each row's Accept button:

```js
btn.addEventListener('click', async () => {
  await api('POST', '/api/todos', { project_id: project.id, text: suggestion, is_followup: false });
  // remove this item from suggestions_json
  const newItems = t.suggestions_json.filter(s => s !== suggestion);
  await api('PATCH', `/api/todos/${t.id}`, {
    suggestions_json: JSON.stringify(newItems),
    suggestions_status: newItems.length ? 'generated' : '',
  });
  await reload();
});
```

Dismiss-all button:

```js
btn.addEventListener('click', async () => {
  await api('PATCH', `/api/todos/${t.id}`, { suggestions_json: '[]', suggestions_status: '' });
  await reload();
});
```

### Step 7 — Deploy and smoke test

```bash
# llm-service changes
scp llm-service/main.py llm-service/prompts.py root@100.67.230.29:/tmp/
ssh root@100.67.230.29 '
  pct push 302 /tmp/main.py     /opt/llm-service/main.py
  pct push 302 /tmp/prompts.py  /opt/llm-service/prompts.py
  pct exec 302 -- systemctl restart llm-service
'
# Organizer changes
scp organizer/db.py organizer/main.py root@100.90.75.10:/opt/organizer/
ssh root@100.90.75.10 systemctl restart organizer
scp organizer/static/index.html root@100.90.75.10:/opt/organizer/static/index.html
```

Smoke test: create a long todo → watch title populate (existing) → wait 40–90s →
watch suggestions panel appear → accept one → confirm new todo created → dismiss
remaining → confirm panel gone.

---

## Files touched

- `organizer/db.py` — `_migrate_add_suggestions`, `init_schema`
- `organizer/main.py` — `_generate_subitems`, `create_todo`, `TodoUpdate`,
  `update_todo`, `_todo_to_dict`, lifespan sweep
- `organizer/static/index.html` — CSS, `_hasPendingLLM`, suggestions panel
  rendering and event handlers
- `llm-service/main.py` — `_call_ollama` `think` param, `SubitemRequest/Response`,
  `_sanitise_subitems`, `/suggest-subitems` endpoint
- `llm-service/prompts.py` — `subitem_v1` entry

---

## Execution log

<!-- Fill in when work begins. -->

---

## Post-completion state

<!-- Fill in when complete. -->
