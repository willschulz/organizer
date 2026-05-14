# 0007 — LLM-assisted agent prompt builder

| Field | Value |
|-------|-------|
| Status | planning |
| Tracks | Organizer todo — V2b: LLM-assisted agent prompt builder |
| Depends on | 0004 — llm-service wiring in organizer; qwen3:8b default |
| Behavioral context | The existing copy button calls `buildTodoPrompt()` to assemble a fixed-template prompt and copies it to the clipboard. This job replaces that flow with an interactive one: clicking copy opens a small popover where the user briefly states their intent ("fix this bug", "implement this feature", "conduct this analysis"), then qwen3:8b assembles a tight, well-scoped agent prompt from the intent + all available todo/project context. Empty intent falls back to the old instant-copy behavior. |

---

## Objective

Transform the "copy agent-pickup prompt" button from a dumb template dump into
a smart, intent-aware prompt assembly step. The user's one-sentence goal is
combined with structured todo and project context by qwen3:8b to produce a
prompt that opens with a clear action instruction, includes only the context
the model actually needs, and ends ready to paste into any agent chat.

Success looks like: clicking the copy icon on a Spirals todo, typing "finish
the labeling batch and update the notes", waiting ~8s, and pasting a crisp
150–250 word prompt into Cursor that an agent could act on without any further
orientation from the user.

---

## Background — what's already in place

- `buildTodoPrompt(todo, project, ancestors)` in `organizer/static/index.html`
  (around line 1799) assembles a structured markdown prompt from todo/project
  fields. This is the context the new flow will pass to the LLM.
- The copy button (`.todo-copy-btn`) is rendered inside `renderTodoNode` and
  currently calls `buildTodoPrompt` synchronously and writes to the clipboard.
  No async path exists yet.
- llm-service at `http://100.68.34.113:8553` already handles `_call_ollama`
  with `think` and `max_tokens` params (added in jobs 0005 / 0006 planning).
  This endpoint uses `think=False` for fast (~5–15s) assembly.
- The organizer backend routes LLM calls through `LLM_SERVICE_URL` env var.
- The frontend state object has all project/todo data in memory — context
  assembly can happen client-side (avoids a round-trip to the DB). The
  assembled context string is sent as the request body to the backend, which
  forwards to llm-service.

---

## Design decisions

- **No new DB columns.** Prompt generation is ephemeral — intent + context in,
  prompt out. No storage needed.

- **UX flow:**
  1. User clicks copy icon → small `<dialog>` popover appears, anchored
     centrally (reuses the existing `<dialog>` infrastructure).
  2. Popover contains a single-line text input ("What do you want to do?") and
     a **Generate & Copy** button. A faint hint: "e.g. fix this bug, implement
     this feature, run this analysis".
  3. If the user submits an **empty** intent, fall back to the existing
     `buildTodoPrompt` instant-copy behavior (no LLM call, no latency).
  4. If intent is non-empty: button shows a spinner + "Generating…", the
     frontend fires `POST /api/todos/{id}/build-prompt` with
     `{"intent": "...", "context": "<assembled context string>"}`.
  5. On success: copy the returned prompt to clipboard, close the dialog, show
     the existing `toast('Prompt copied')`.
  6. On error: show `toast('Prompt generation failed — copied fallback')` and
     fall back to the instant-copy plain template.

- **Context assembly stays in JS.** `buildTodoPrompt` already produces a
  well-structured text block. We pass that directly as `context` rather than
  re-querying the DB server-side. This keeps the backend endpoint thin.

- **`think: False`** for this endpoint — it's fast structured assembly, not
  reasoning. Target latency: 5–15s.

- **`max_tokens: 400`** — long enough for a 250-word prompt with headroom.

- **New llm-service endpoint:** `POST /build-agent-prompt`

- **New organizer endpoint:** `POST /api/todos/{id}/build-prompt`
  - Accepts `{"intent": "...", "context": "..."}`.
  - Forwards to llm-service, returns `{"prompt": "..."}` or `{"error": "..."}`.
  - No DB read needed — context arrives from the frontend.

- **Prompt `agent_prompt_v1`:** instructs the model to write a self-contained
  agent prompt that (1) opens with the user's goal, (2) selects and synthesises
  the most relevant context, (3) lists key file paths the agent should read
  first, (4) closes with a clear action instruction. Aim for 150–250 words,
  no preamble, write the prompt directly.

---

## Plan

### Step 1 — llm-service: add `agent_prompt_v1` to `prompts.py`

```python
"agent_prompt_v1": {
    "system": (
        "You are a prompt engineer writing agent-ready task prompts. "
        "You will receive (a) a brief statement of the user's intent and "
        "(b) structured context about a todo and its project. "
        "Write a single, self-contained prompt for an AI coding or research agent. "
        "Requirements:\n"
        "- Open with one sentence stating the goal clearly.\n"
        "- Include only the context the agent actually needs (omit boilerplate).\n"
        "- List 1-4 key file paths the agent should read first (from the context).\n"
        "- Close with a clear action instruction.\n"
        "- 150-250 words. No preamble or meta-commentary — write the prompt directly."
    ),
    "user_template": "Intent: {intent}\n\nContext:\n{context}",
},
```

### Step 2 — llm-service: add `/build-agent-prompt` endpoint

In `llm-service/main.py`:

```python
class AgentPromptRequest(BaseModel):
    intent: str = Field(..., min_length=1)
    context: str = Field(..., min_length=1)
    prompt_version: str = Field(default="agent_prompt_v1")
    model: Optional[str] = Field(default=None)

class AgentPromptResponse(BaseModel):
    prompt: str
    model: str
    prompt_version: str
    duration_ms: int
    status: str  # "ok" | "empty_output" | "timeout" | "ollama_error"

@app.post("/build-agent-prompt", response_model=AgentPromptResponse)
async def build_agent_prompt(req: AgentPromptRequest) -> AgentPromptResponse:
    model = req.model or config.DEFAULT_MODEL
    prompt = get_prompt(req.prompt_version)
    user_message = prompt["user_template"].format(
        intent=req.intent, context=req.context
    )
    try:
        raw_output, elapsed = await _call_ollama(
            model=model,
            system_prompt=prompt["system"],
            user_prompt=user_message,
            think=False,
            max_tokens=400,
        )
    except httpx.TimeoutException:
        ...  # return AgentPromptResponse status="timeout"
    except httpx.HTTPError:
        ...  # return AgentPromptResponse status="ollama_error"
    text = raw_output.strip()
    status = "ok" if text else "empty_output"
    return AgentPromptResponse(prompt=text, model=model, prompt_version=req.prompt_version,
                               duration_ms=int(elapsed*1000), status=status)
```

### Step 3 — organizer backend: `POST /api/todos/{id}/build-prompt`

In `organizer/main.py`:

```python
class BuildPromptRequest(BaseModel):
    intent: str
    context: str

@app.post("/api/todos/{todo_id}/build-prompt")
async def build_todo_prompt(todo_id: int, payload: BuildPromptRequest):
    if not payload.intent.strip():
        return JSONResponse({"error": "intent required"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(
                f"{LLM_SERVICE_URL}/build-agent-prompt",
                json={
                    "intent": payload.intent.strip(),
                    "context": payload.context,
                    "prompt_version": "agent_prompt_v1",
                },
            )
            data = resp.json()
        if resp.status_code == 200 and data.get("status") == "ok" and data.get("prompt"):
            return {"prompt": data["prompt"]}
        else:
            return JSONResponse({"error": data.get("status", "llm_error")}, status_code=502)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
```

### Step 4 — Frontend: replace copy button click handler with intent popover

**New dialog markup** (appended to body once, reused for each todo):

```html
<dialog id="prompt-builder-dialog">
  <div class="modal-header">
    <span>Build agent prompt</span>
    <button class="modal-close" id="prompt-builder-close">×</button>
  </div>
  <div class="prompt-builder-body">
    <label for="prompt-intent">What do you want to do?</label>
    <input id="prompt-intent" type="text" autocomplete="off"
           placeholder="e.g. fix this bug, implement this feature, run this analysis" />
    <div class="prompt-builder-hint">Leave blank to copy the plain context template.</div>
    <div class="prompt-builder-actions">
      <button id="prompt-builder-submit">Generate &amp; Copy</button>
    </div>
    <div id="prompt-builder-status"></div>
  </div>
</dialog>
```

**CSS:**

```css
#prompt-builder-dialog { width: 420px; max-width: calc(100vw - 32px); }
.prompt-builder-body { padding: 12px 20px 20px; display: flex; flex-direction: column; gap: 8px; }
.prompt-builder-body label { font-size: 0.85rem; font-weight: 600; }
#prompt-intent {
  width: 100%; padding: 7px 10px;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg-input); font-size: 0.9rem;
}
.prompt-builder-hint { font-size: 0.75rem; color: var(--text-muted); }
.prompt-builder-actions { display: flex; justify-content: flex-end; }
#prompt-builder-submit {
  padding: 6px 14px; border-radius: 6px;
  background: var(--accent); color: #fff; border: none; cursor: pointer; font-size: 0.85rem;
}
#prompt-builder-submit:disabled { opacity: 0.5; cursor: default; }
#prompt-builder-status { font-size: 0.8rem; color: var(--text-muted); min-height: 1.2em; }
```

**JS wiring** (module-level, run once):

```js
let _promptBuilderTodo = null;
let _promptBuilderProject = null;
let _promptBuilderAncestors = null;

const _promptBuilderDialog = document.getElementById('prompt-builder-dialog');
const _promptIntent        = document.getElementById('prompt-intent');
const _promptStatus        = document.getElementById('prompt-builder-status');
const _promptSubmit        = document.getElementById('prompt-builder-submit');

document.getElementById('prompt-builder-close').addEventListener('click', () => {
  _promptBuilderDialog.close();
});

_promptBuilderDialog.addEventListener('close', () => {
  _promptIntent.value = '';
  _promptStatus.textContent = '';
  _promptSubmit.disabled = false;
});

_promptSubmit.addEventListener('click', async () => {
  const intent = _promptIntent.value.trim();
  if (!intent) {
    // fallback: instant copy of plain template
    const text = buildTodoPrompt(_promptBuilderTodo, _promptBuilderProject, _promptBuilderAncestors);
    navigator.clipboard.writeText(text).then(() => toast('Prompt copied'));
    _promptBuilderDialog.close();
    return;
  }
  _promptSubmit.disabled = true;
  _promptStatus.textContent = 'Generating…';
  const context = buildTodoPrompt(_promptBuilderTodo, _promptBuilderProject, _promptBuilderAncestors);
  try {
    const data = await api('POST', `/api/todos/${_promptBuilderTodo.id}/build-prompt`,
                           { intent, context });
    navigator.clipboard.writeText(data.prompt).then(() => toast('Prompt copied'));
    _promptBuilderDialog.close();
  } catch (err) {
    toast('Generation failed — copied fallback');
    const text = buildTodoPrompt(_promptBuilderTodo, _promptBuilderProject, _promptBuilderAncestors);
    navigator.clipboard.writeText(text);
    _promptBuilderDialog.close();
  }
});
```

**Modify the copy button click handler** in `renderTodoNode` (around line 1962):

```js
// was: navigator.clipboard.writeText(text).then(() => toast('Prompt copied'));
// now:
copyBtn.addEventListener('click', () => {
  _promptBuilderTodo = t;
  _promptBuilderProject = project;
  _promptBuilderAncestors = ancestors;
  _promptIntent.value = '';
  _promptStatus.textContent = '';
  _promptSubmit.disabled = false;
  _promptBuilderDialog.showModal();
  _promptIntent.focus();
});
```

Also wire Enter key on `#prompt-intent` to submit:

```js
_promptIntent.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') _promptSubmit.click();
});
```

### Step 5 — Deploy and smoke test

```bash
# llm-service
scp llm-service/main.py llm-service/prompts.py root@100.67.230.29:/tmp/
ssh root@100.67.230.29 '
  pct push 302 /tmp/main.py    /opt/llm-service/main.py
  pct push 302 /tmp/prompts.py /opt/llm-service/prompts.py
  pct exec 302 -- systemctl restart llm-service
'
# Organizer
scp organizer/main.py root@100.90.75.10:/opt/organizer/
ssh root@100.90.75.10 systemctl restart organizer
scp organizer/static/index.html root@100.90.75.10:/opt/organizer/static/index.html
```

Smoke test:
1. Click copy on a todo with notes and paths → dialog appears.
2. Press Enter / click Generate with empty intent → instant plain-copy fallback.
3. Click copy again → type "fix this bug" → Generate & Copy → wait 5–15s →
   spinner shows → toast "Prompt copied".
4. Paste into a text editor → verify the prompt opens with the goal, references
   correct file paths, and ends with a clear action instruction.
5. Test error path: temporarily point `LLM_SERVICE_URL` at a dead port → verify
   toast shows "Generation failed — copied fallback" and plain template is on
   clipboard.

---

## Files touched

- `llm-service/prompts.py` — `agent_prompt_v1` entry
- `llm-service/main.py` — `AgentPromptRequest/Response`, `/build-agent-prompt` endpoint
- `organizer/main.py` — `BuildPromptRequest`, `POST /api/todos/{id}/build-prompt`
- `organizer/static/index.html` — dialog markup + CSS + JS (copy button handler,
  `_promptBuilderDialog` wiring, `_promptBuilderTodo/Project/Ancestors` state)

---

## Execution log

<!-- Fill in when work begins. -->

---

## Post-completion state

<!-- Fill in when complete. -->
