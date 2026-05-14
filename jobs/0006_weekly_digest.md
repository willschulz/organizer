# 0006 — Weekly standup digest (V3)

| Field | Value |
|-------|-------|
| Status | planning |
| Tracks | Organizer todo — V3: Weekly standup digest |
| Depends on | 0005 — llm-service `/suggest-subitems` pattern; `think` param in `_call_ollama`; qwen3:8b default |
| Behavioral context | On-demand endpoint that assembles the last 7 days of completed todos and all open non-followup todos, sends them to qwen3:8b with thinking enabled, and returns a short markdown digest: what got done, what's active, what's blocked. Surfaced via a "Digest" button in the topbar. No new DB state — entirely ephemeral/on-demand. Cautious rollout: user explicitly triggers it, no automatic delivery yet. |

---

## Objective

A "Digest" button in the topbar fires `POST /api/digest`. The endpoint
assembles todo state, calls qwen3:8b (think: true), and returns a markdown
summary. The frontend renders it in a modal with a spinner while the LLM
thinks (~30–90s). The result answers three questions: what did I get done this
week? what's in flight? what's blocked?

Success looks like: clicking Digest, waiting ~60s, reading a crisp 10–20 line
markdown summary that I'd be comfortable pasting into a weekly standup message.
Closing the modal discards the digest (no persistence needed for v1).

---

## Background — what's already in place

- `llm-service` running qwen3:8b with thinking support (`think` param added in
  job 0005).
- Organizer DB has `completed_at` on todos (or `updated_at` + `completed` flag)
  to filter last-7-days completions.
- The organizer frontend already has a `<dialog>`-based modal pattern
  (project/todo edit modals). The digest modal follows the same infrastructure.
- No existing endpoint aggregates cross-project todo state — this is new.

---

## Design decisions

- **No DB columns.** The digest is ephemeral: generated on request, shown once,
  discarded on modal close. No caching in v1 — re-clicking Digest reruns the
  full LLM call. This keeps the scope small and avoids stale-digest UX issues.

- **New llm-service endpoint:** `POST /generate-digest`
  - Request: `{ "structured_input": "...", "prompt_version": "digest_v1" }`
    where `structured_input` is a pre-formatted text block the organizer backend
    assembles (see below).
  - Response: `{ "markdown": "...", "status": "ok", "duration_ms": ... }`
  - Uses `think=True`.
  - Longer `num_predict` budget needed for digest output (not the 48-token title
    budget). Add a `max_tokens` optional field to the request, defaulting to
    `config.NUM_PREDICT` but overridable per-call. The digest endpoint passes
    `max_tokens=600`.

- **Structured input format** (assembled by organizer backend):

  ```
  ## Completed this week
  - [Spirals] Finish labeling
  - [Homelab] Implement monthly maintenance

  ## Active (non-followup open todos)
  ### Spirals
  - Final pass: revised figures
  - [BLOCKER] Revised study order

  ### Like Biases
  - Political capping puzzle
  ...

  ## Blocked todos
  - [Spirals] Revised study order
  - [Elite rhetoric] Metric choice for scaling
  ```

  The organizer backend queries SQLite for this data — no LLM call needed for
  assembly, just string formatting.

- **Prompt `digest_v1`:** instructs the model to produce a concise markdown
  standup digest in three sections (Done this week / In progress / Needs
  attention), prose + bullets, ≤250 words. Tone: professional, first-person.

- **`POST /api/digest` on organizer backend:**
  - Queries: todos with `completed = 1` and `updated_at >= (now - 7 days)`
    for the "done" section.
  - Queries: all open, non-followup todos ordered by project, for "active".
  - Queries: open todos with `is_blocker = 1`, for "blocked".
  - Assembles structured input string.
  - Calls `LLM_SERVICE_URL/generate-digest` with `timeout=150.0` (thinking
    takes longer).
  - Returns `{"markdown": "...", "generated_at": "<ISO timestamp>"}` or
    `{"error": "..."}` on failure.
  - Note: this is a synchronous request from the frontend's perspective (the
    frontend awaits it). The long latency is acceptable because the user
    explicitly triggered it and sees a spinner.

- **Frontend — Digest button:**
  - Small pill/button in the topbar, to the right of the title or near the
    existing `#status` element.
  - On click: show a `<dialog>` modal with a spinner and the text "Generating
    digest…". Fire `POST /api/digest`. On response: replace spinner with
    rendered markdown (use a simple `<pre>` or a lightweight markdown renderer
    — even raw markdown in a `<pre class="digest-output">` is acceptable for
    v1). On error: show a brief error message.
  - Close button dismisses the modal. No state retained after close.
  - While the request is in flight, the button is disabled to prevent double-
    sends.

- **Latency UX:** 30–90s is the expected range. The spinner + "Generating…"
  copy sets the right expectation. We do NOT use the 2s polling pattern here
  (it's a foreground request the user is waiting on).

- **Future follow-on (not in v1):** cron on `organizer-ct` calls
  `/api/digest` on a schedule and delivers the result via email or a Tailscale
  notification. Tracked separately.

---

## Plan

### Step 1 — llm-service: extend `_call_ollama` for variable `num_predict`

The title endpoint uses a fixed 48-token budget via `config.NUM_PREDICT`. For
digest output we need ~600 tokens.

Add an optional `max_tokens: Optional[int] = None` parameter to `_call_ollama`
and `SubitemRequest`/`SubitemResponse` (already extended in job 0005). When
provided, override `config.NUM_PREDICT` in the options dict.

### Step 2 — llm-service: add `digest_v1` prompt

In `llm-service/prompts.py`:

```python
"digest_v1": {
    "system": (
        "You are a research assistant writing a brief weekly standup digest. "
        "You will receive structured data about tasks completed and in progress. "
        "Write a concise markdown digest in three sections:\n"
        "## Done this week\n## In progress\n## Needs attention\n\n"
        "Rules: prose + bullets, ≤250 words total, first-person, professional tone. "
        "Do not repeat task text verbatim — synthesize. "
        "Highlight blockers and anything time-sensitive."
    ),
    "user_template": "{raw_text}",
},
```

### Step 3 — llm-service: add `/generate-digest` endpoint

In `llm-service/main.py`:

```python
class DigestRequest(BaseModel):
    structured_input: str = Field(..., min_length=1)
    prompt_version: str = Field(default="digest_v1")
    model: Optional[str] = Field(default=None)
    max_tokens: Optional[int] = Field(default=None)

class DigestResponse(BaseModel):
    markdown: str
    model: str
    prompt_version: str
    duration_ms: int
    status: str  # "ok" | "empty_output" | "timeout" | "ollama_error"

@app.post("/generate-digest", response_model=DigestResponse)
async def generate_digest(req: DigestRequest) -> DigestResponse:
    model = req.model or config.DEFAULT_MODEL
    prompt = get_prompt(req.prompt_version)
    user_message = prompt["user_template"].format(raw_text=req.structured_input)
    try:
        raw_output, elapsed = await _call_ollama(
            model=model,
            system_prompt=prompt["system"],
            user_prompt=user_message,
            think=True,
            max_tokens=req.max_tokens or 600,
        )
    except httpx.TimeoutException:
        ...  # return DigestResponse status="timeout"
    except httpx.HTTPError:
        ...  # return DigestResponse status="ollama_error"
    markdown = raw_output.strip()
    status = "ok" if markdown else "empty_output"
    return DigestResponse(markdown=markdown, model=model, prompt_version=req.prompt_version,
                          duration_ms=int(elapsed*1000), status=status)
```

Update `_call_ollama` signature:

```python
async def _call_ollama(
    model: str,
    system_prompt: str,
    user_prompt: str,
    think: bool = False,
    max_tokens: Optional[int] = None,
) -> tuple[str, float]:
    payload = {
        ...
        "think": think,
        "options": {
            "temperature": config.TEMPERATURE,
            "num_predict": max_tokens if max_tokens is not None else config.NUM_PREDICT,
        },
        ...
    }
```

Also update llm-service `config.py` to bump `TIMEOUT_S` default to `150` (or
accept a per-request timeout override) — the digest call easily runs 60–120s.

### Step 4 — organizer backend: `POST /api/digest`

In `organizer/main.py`:

```python
import datetime as dt

@app.post("/api/digest")
async def get_digest():
    conn = db.get_conn()
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=7)).isoformat()

    # Completed this week: join todos → projects
    done_rows = conn.execute("""
        SELECT p.name, t.text FROM todos t
        JOIN projects p ON p.id = t.project_id
        WHERE t.completed = 1 AND t.updated_at >= ?
        ORDER BY p.name, t.updated_at DESC
    """, (cutoff,)).fetchall()

    # Open non-followup todos grouped by project
    active_rows = conn.execute("""
        SELECT p.name, t.text, t.is_blocker FROM todos t
        JOIN projects p ON p.id = t.project_id
        WHERE t.completed = 0 AND t.is_followup = 0
        ORDER BY p.name, t.id
    """).fetchall()

    # Assemble structured input
    lines = []
    if done_rows:
        lines.append("## Completed this week")
        for proj, text in done_rows:
            lines.append(f"- [{proj}] {text}")
        lines.append("")

    if active_rows:
        lines.append("## Active todos")
        cur_proj = None
        for proj, text, is_blocker in active_rows:
            if proj != cur_proj:
                lines.append(f"### {proj}")
                cur_proj = proj
            prefix = "[BLOCKER] " if is_blocker else ""
            lines.append(f"- {prefix}{text}")
        lines.append("")

    blockers = [(p, t) for p, t, b in active_rows if b]
    if blockers:
        lines.append("## Blockers")
        for proj, text in blockers:
            lines.append(f"- [{proj}] {text}")

    structured_input = "\n".join(lines)

    try:
        async with httpx.AsyncClient(timeout=150.0) as client:
            resp = await client.post(
                f"{LLM_SERVICE_URL}/generate-digest",
                json={"structured_input": structured_input,
                      "prompt_version": "digest_v1",
                      "max_tokens": 600},
            )
            data = resp.json()
        if resp.status_code == 200 and data.get("status") == "ok":
            return {"markdown": data["markdown"],
                    "generated_at": dt.datetime.utcnow().isoformat()}
        else:
            return JSONResponse({"error": data.get("status", "llm_error")}, status_code=502)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
```

### Step 5 — Frontend

**CSS:**

```css
#digest-btn {
  font-size: 0.78rem;
  padding: 3px 10px;
  border-radius: 12px;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  cursor: pointer;
}
#digest-btn:hover { border-color: var(--accent); color: var(--accent); }
#digest-btn:disabled { opacity: 0.45; cursor: default; }
.digest-modal-body {
  max-height: 70vh;
  overflow-y: auto;
  padding: 16px 20px;
}
pre.digest-output {
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 0.9rem;
  line-height: 1.55;
}
```

**Digest button** added to `#topbar-inner` (alongside existing elements).

**Dialog markup** (appended to body):

```html
<dialog id="digest-dialog">
  <div class="modal-header">
    <span>Weekly Digest</span>
    <button class="modal-close" id="digest-close">×</button>
  </div>
  <div class="digest-modal-body" id="digest-body">
    <pre class="digest-output" id="digest-output"></pre>
  </div>
</dialog>
```

**JS handler:**

```js
document.getElementById('digest-btn').addEventListener('click', async () => {
  const btn = document.getElementById('digest-btn');
  const output = document.getElementById('digest-output');
  const dialog = document.getElementById('digest-dialog');
  btn.disabled = true;
  output.textContent = 'Generating digest… (this may take up to 90 seconds)';
  dialog.showModal();
  try {
    const data = await api('POST', '/api/digest', {});
    output.textContent = data.markdown ?? data.error ?? 'No output.';
  } catch (err) {
    output.textContent = 'Error generating digest: ' + err.message;
  } finally {
    btn.disabled = false;
  }
});
document.getElementById('digest-close').addEventListener('click', () => {
  document.getElementById('digest-dialog').close();
});
```

### Step 6 — Deploy and smoke test

```bash
# llm-service
scp llm-service/main.py llm-service/prompts.py llm-service/config.py \
    root@100.67.230.29:/tmp/
ssh root@100.67.230.29 '
  pct push 302 /tmp/main.py    /opt/llm-service/main.py
  pct push 302 /tmp/prompts.py /opt/llm-service/prompts.py
  pct push 302 /tmp/config.py  /opt/llm-service/config.py
  pct exec 302 -- systemctl restart llm-service
'
# Organizer backend + frontend
scp organizer/main.py root@100.90.75.10:/opt/organizer/
ssh root@100.90.75.10 systemctl restart organizer
scp organizer/static/index.html root@100.90.75.10:/opt/organizer/static/index.html
```

Smoke test: click Digest button → spinner appears → wait → markdown renders →
verify "Done this week", "In progress", "Needs attention" sections present →
close modal → reclick → reruns cleanly.

---

## Files touched

- `llm-service/main.py` — `_call_ollama` `max_tokens` param, `DigestRequest/Response`,
  `_generate_digest` endpoint
- `llm-service/prompts.py` — `digest_v1` entry
- `llm-service/config.py` — `TIMEOUT_S` default bump (or per-request override)
- `organizer/main.py` — `POST /api/digest` endpoint
- `organizer/static/index.html` — Digest button, dialog, CSS, JS handler

---

## Future follow-on (not in v1)

- Cron on `organizer-ct` that calls `/api/digest` on Monday morning and
  delivers via email or Tailscale notification.
- Persist recent digests in a `digests` table for historical review.
- Graduate to a stronger external model (GPT-5/Claude API) once local
  CPU latency becomes frustrating for the digest use-case.

---

## Execution log

<!-- Fill in when work begins. -->

---

## Post-completion state

<!-- Fill in when complete. -->
