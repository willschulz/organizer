# Organizer

Personal project tracker for keeping track of research / homelab /
side-project todos in a clean, ADHD-friendly UI.

- **Live URL:** `https://organizer.manx-celsius.ts.net/` (tailnet only)
- **Stack:** FastAPI + single-file SPA + SQLite
- **Host:** LXC `organizer-ct` (VMID 301) on `alabastron`
- **Auth perimeter:** Tailscale (no app-level login)
- **Source-of-truth:** this directory on the laptop. Never edit the
  deployed copy on the LXC. See `.cursor/rules/organizer-deploy.mdc`.

## What it does

- 4-row layout (Near-Publication / In-Development / Early-Stage /
  Side-Projects) of portrait-aspect project cards.
- Each card: project name, optional deadline badge, ordered todo list
  with the "blocker" item pinned + highlighted at the top, plus a
  "Then..." disclosure for `is_followup=1` items.
- Add / edit / delete projects and todos; check todos off; completion
  timestamps are stored for future progress visualization.
- **`in_progress` status** — a purple left-bar highlight with a
  play-triangle circle marker. Toggled via a hover play button on each
  todo row. Completing a todo auto-clears `in_progress`.
- **Per-todo context fields** (`notes`, `paths_json`) — hidden from the
  card UI by default; a hover note-icon button opens them in an edit
  modal. Each todo card also has a **Copy** button that writes a
  structured context block (ids, notes, paths, and phase instructions)
  to the clipboard, ready to paste into a Cursor agent session or for
  injection by an orchestrating agent. Feed the agent
  pickup/breakdown/close-out loop (see Agent rules below).
- Per-project hidden metadata: `paths_json` and free-form `notes`.
  Both feed the agent rules; neither is rendered in the card UI.
- PWA hooks: `manifest.webmanifest`, apple-touch icons, and standalone
  display so iOS/macOS Safari "Add to Home Screen" produces a
  native-feeling app.

Out-of-scope (deferred): nested-todo UI, completion visualization,
"Resources" row, day/week planner.

## Layout

```
organizer/
  README.md
  .env.example
  .gitignore                # data/, .env, venv/, __pycache__/
  requirements.txt          # fastapi, uvicorn[standard], pydantic
  organizer.service         # systemd unit (deployed to /etc/systemd/system/)
  main.py                   # FastAPI app + all CRUD routes
  db.py                     # SQLite connection + schema migration
  seed.py                   # one-shot: populate from Projects List.docx
  static/
    index.html              # the SPA (vanilla HTML/CSS/JS, no build)
    manifest.webmanifest
    icons/                  # apple-touch-icon, icon-192, icon-512, favicon-32
  data/                     # gitignored; organizer.db lives here in prod
```

## Data model

Two tables, kept deliberately small. `parent_id` is present on `todos`
from day one so v2 nesting is a UI change, not a migration.

```sql
projects (id, name, category, display_order, deadline,
          notes, paths_json, archived, created_at, updated_at)

todos    (id, project_id, parent_id, text,
          is_blocker, is_followup, in_progress, display_order,
          completed, completed_at,
          notes, paths_json,
          created_at, updated_at)
```

Categories are constrained: `near_publication | in_development |
early_stage | side_project`. The "Then:" / "What's next?" content
from the seed docx maps to `is_followup=1` and is hidden behind a
disclosure on the card.

Full schema lives in `db.py` (`SCHEMA_SQL`).

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Serve `static/index.html` |
| `GET` | `/api/projects` | All projects + their todos, grouped by category |
| `POST` | `/api/projects` | Create project |
| `PATCH` | `/api/projects/{id}` | Edit (name, category, deadline, notes, paths, order, archived) |
| `DELETE` | `/api/projects/{id}` | Delete (cascade-deletes todos) |
| `POST` | `/api/todos` | Create todo |
| `PATCH` | `/api/todos/{id}` | Edit (text, is_blocker, is_followup, in_progress, order, completed, notes, paths) |
| `DELETE` | `/api/todos/{id}` | Delete |
| `POST` | `/api/todos/{id}/complete` | Set `completed=1`, `completed_at=now()`, `in_progress=0` |
| `POST` | `/api/todos/{id}/uncomplete` | Clear completed + completed_at |
| `GET` | `/api/stats?since=ISO` | Completed-todo counts per project / per day |

## Agent rules

Three Cursor rules in `.cursor/rules/` implement a lightweight
propose-and-confirm loop between the Organizer and the rest of the
workspace. All three are `alwaysApply: true` but begin with a guard
that exits silently if the current request is not about an Organizer
todo.

| Rule file | Trigger | Behavior |
|---|---|---|
| `organizer-todo-pickup.mdc` | "let's work on X / tackle Y" — or a Copy-button paste | Resolve project + todo (or skip resolution if Copy context already present); read `notes`/`paths` context (capped); summarize; propose a session plan or hand off to breakdown |
| `organizer-todo-breakdown.mdc` | Todo spans multiple sessions | Propose 2–5 same-abstraction-level sub-todos with starter `paths`/`notes`; `POST` only after explicit user approval |
| `organizer-todo-closeout.mdc` | Work completes or advances a todo | Summarize changes; propose new todos, completion, and project-context updates; write in order new → update → complete, only after approval |

The loop is intentionally human-gated: no rule writes to the API
without explicit user confirmation. Sub-todos are kept at a
human-manageable abstraction level (one focused session each).

**Two entry points, same rules:**
- **Copy-button / orchestrated:** structured context (ids, notes, paths,
  phase instructions) arrives pre-loaded — either pasted from the card's
  Copy button or injected by an orchestrating agent. The pickup rule
  recognises the block and skips API resolution.
- **Natural-language pickup:** user says "let's work on X". The pickup
  rule resolves the todo via `GET /api/projects`, reads context, then
  proceeds as normal.

## Local development

```bash
cd ~/Desktop/homelab/organizer
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # optional; defaults are fine for local
uvicorn main:app --reload --host 127.0.0.1 --port 8551
# http://127.0.0.1:8551/
```

The DB lives at `./data/organizer.db` by default and is created on
first request via `init_schema()`. To seed a fresh local DB from the
`Projects List.docx` content:

```bash
python seed.py
```

`seed.py` is idempotent: it bails out if the `projects` table already
has rows.

## Deploy

See [`.cursor/rules/organizer-deploy.mdc`](../.cursor/rules/organizer-deploy.mdc)
for the canonical commands.

Quick reference (run from `~/Desktop/homelab/`):

```bash
# Frontend only — no restart:
scp organizer/static/index.html root@100.90.75.10:/opt/organizer/static/index.html

# Backend — restart required:
scp organizer/main.py organizer/db.py organizer/seed.py \
    root@100.90.75.10:/opt/organizer/
ssh root@100.90.75.10 systemctl restart organizer

# Verify:
ssh root@100.90.75.10 'systemctl is-active organizer && curl -s http://127.0.0.1:8551/api/projects | head -c 200'
```

Verify end-to-end (from laptop, hitting Tailscale Serve):

```bash
curl -sI https://organizer.manx-celsius.ts.net/ | head -1
```

## Where things live on the LXC

| Thing | Path |
|---|---|
| App code | `/opt/organizer/` |
| Python venv | `/opt/organizer/venv/` |
| Static frontend | `/opt/organizer/static/` |
| SQLite DB | `/opt/organizer/data/organizer.db` |
| Env file | `/opt/organizer/.env` |
| systemd unit | `/etc/systemd/system/organizer.service` |
| Tailscale Serve config | `tailscale serve status` (state in `/var/lib/tailscale/`) |
| uvicorn bind | `127.0.0.1:8551` |

## Backups

The cluster-wide PBS backup job snapshots LXCs nightly, which covers
`organizer-ct` once it is added to the relevant include list. The
SQLite DB at `/opt/organizer/data/organizer.db` rides along inside the
container's rootfs. WAL is enabled (`PRAGMA journal_mode = WAL` in
`db.py`), so a hot snapshot is consistent.

If irreplaceable data accumulates before the PBS job is verified, take
a one-off dump:

```bash
ssh root@100.90.75.10 'sqlite3 /opt/organizer/data/organizer.db ".backup /tmp/organizer-$(date -u +%Y%m%dT%H%M%SZ).db"'
scp root@100.90.75.10:/tmp/organizer-*.db ~/backups/
```

## Why FastAPI + single-file SPA?

This mirrors the `apotheke-dash` pattern already established in the
homelab: small Python service, one HTML file with vanilla JS, SQLite
for state, `scp`-based deploy. No build step, no node_modules, no
webpack. Easy to read end-to-end on a single screen, easy to edit
from the laptop and push.

## Roadmap (deferred)

**Queued:**
- Flesh out `notes`/`paths` context for all 13 projects.
- Resources row (cloud-credits tracker).
- iOS / macOS Xcode wrapper if the PWA experience hits limits.
