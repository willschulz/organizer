"""
Organizer -- one-shot seed from the contents of `Projects List.docx`.

Run after first deploy:
    python3 seed.py

Idempotent: if the projects table is non-empty, this script does nothing.
The structure here mirrors the docx exactly. The user explicitly said not to
guess too much per project; this is just a starting point that they can
edit/enrich in the running app.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import db


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Each project is: (name, category, deadline, blocker, todos[], followups[])
# - blocker becomes a single is_blocker=1 todo at order 0
# - todos become non-blocker active todos in listed order
# - followups become is_followup=1 todos in listed order
PROJECTS: list[dict] = [
    # ---- Near-Publication ----
    {
        "name": "Spirals",
        "category": "near_publication",
        "deadline": "2026-05-20",
        "blocker": "Finish labeling",
        "todos": [
            "Final pass: revised figures",
            "Revised study order",
            "New methods sections",
        ],
        "followups": [
            "IC2S2 prep",
            "More labelling",
            "Simulation stuff (talk to Lisa again)",
            "Collab w/ Claire? Lisa?",
            "Mastodon experiments",
            "Browser extension interventions / Mastodon UI context?",
            "Other spirals data analyses: slant of sponsored accounts?",
        ],
    },
    {
        "name": "Like Biases",
        "category": "near_publication",
        "deadline": "2026-07-15",
        "blocker": "Political capping puzzle",
        "todos": [
            "Run proposed sweeps for solving the political capping puzzle",
            "Inspect emotions classifier for anything interesting",
            "Revise report into draft paper",
        ],
        "followups": [
            "Get AG, CSMAP advice",
            "Get GreenEarth colleagues' input",
            "Inquire about other like data sources (esp. Twitter)",
            "Scrape Reddit",
        ],
    },
    # ---- In-Development ----
    {
        "name": "Mastodon experiments",
        "category": "in_development",
        "deadline": None,
        "blocker": "NYU IRB + PI approval",
        "todos": [
            "Refresh memory of rollout scheme previously planned (with 1-person sims)",
            "Integrate GreenEarth algorithm system into Mastodon",
            "Revisit LLM bots, get CSMAP advice",
            "Talk to PIs and submit IRB",
        ],
        "followups": [],
    },
    {
        "name": "WWYS Germany",
        "category": "in_development",
        "deadline": None,
        "blocker": "Need to check notes and check in with Lisa",
        "todos": [
            "Check notes and emails",
            "Talk to Lisa and get back on track",
        ],
        "followups": [],
    },
    {
        "name": "WWYS US Followup study with NORC AmeriSpeak",
        "category": "in_development",
        "deadline": None,
        "blocker": "Funding",
        "todos": [
            "Find more grants to apply for",
            "Mention to CSMAP when arriving in fall and ask about funding",
        ],
        "followups": [],
    },
    {
        "name": "Elite rhetoric project w/ Danny & Perry",
        "category": "in_development",
        "deadline": None,
        "blocker": "Metric choice for scaling elite rhetoric",
        "todos": [
            "Remember what last promised to colleagues, do it",
        ],
        "followups": [],
    },
    # ---- Early-Stage Ideas ----
    {
        "name": "Clicker (open-source rollout)",
        "category": "early_stage",
        "deadline": None,
        "blocker": "Make open-sourcing roadmap",
        "todos": [
            "Make OSS roadmap with agent help",
            "Find way to simplify hosting (or make hosting optional, default-local app)",
            "Roll out to test users to identify pain points",
        ],
        "followups": [],
    },
    {
        "name": "Fringe-attitude RAG browser extension",
        "category": "early_stage",
        "deadline": "2026-09-01",
        "blocker": "First dogfood feasibility gut check",
        "todos": [
            "Smash existing RAG together with Tiziano's browser extension code to make an MVP",
            "Test it myself in Bluesky, Twitter, Reddit; see what I think",
        ],
        "followups": [
            "Pitch to CSMAP",
            "Integrate into Mastodon / do as browser extension study",
        ],
    },
    # ---- Side Projects ----
    {
        "name": "Homelab",
        "category": "side_project",
        "deadline": None,
        "blocker": None,
        "todos": [],
        "followups": [],
    },
    {
        "name": "Personal website (self-hosted)",
        "category": "side_project",
        "deadline": None,
        "blocker": None,
        "todos": [],
        "followups": [],
    },
    {
        "name": "Job tracker",
        "category": "side_project",
        "deadline": None,
        "blocker": "Want to actually start using it",
        "todos": [],
        "followups": [],
    },
    {
        "name": "3d rendering",
        "category": "side_project",
        "deadline": None,
        "blocker": None,
        "todos": [],
        "followups": [],
    },
    {
        "name": "Sci-fi scaling",
        "category": "side_project",
        "deadline": None,
        "blocker": None,
        "todos": [],
        "followups": [],
    },
]


def seed() -> None:
    db.init_schema()
    conn = db.get_conn()
    existing = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    if existing > 0:
        print(f"DB already has {existing} project(s); seed is idempotent and will not insert.")
        return

    now = _now()
    project_order_by_cat: dict[str, int] = {}

    with db.transaction() as cx:
        for proj in PROJECTS:
            cat = proj["category"]
            display_order = project_order_by_cat.get(cat, 0)
            project_order_by_cat[cat] = display_order + 1

            cur = cx.execute(
                "INSERT INTO projects "
                "(name, category, display_order, deadline, notes, paths_json, archived, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '', '[]', 0, ?, ?)",
                (proj["name"], cat, display_order, proj["deadline"], now, now),
            )
            pid = cur.lastrowid

            todo_order = 0

            if proj["blocker"]:
                cx.execute(
                    "INSERT INTO todos "
                    "(project_id, parent_id, text, is_blocker, is_followup, "
                    " display_order, completed, completed_at, created_at, updated_at) "
                    "VALUES (?, NULL, ?, 1, 0, ?, 0, NULL, ?, ?)",
                    (pid, proj["blocker"], todo_order, now, now),
                )
                todo_order += 1

            for text in proj["todos"]:
                cx.execute(
                    "INSERT INTO todos "
                    "(project_id, parent_id, text, is_blocker, is_followup, "
                    " display_order, completed, completed_at, created_at, updated_at) "
                    "VALUES (?, NULL, ?, 0, 0, ?, 0, NULL, ?, ?)",
                    (pid, text, todo_order, now, now),
                )
                todo_order += 1

            followup_order = 0
            for text in proj["followups"]:
                cx.execute(
                    "INSERT INTO todos "
                    "(project_id, parent_id, text, is_blocker, is_followup, "
                    " display_order, completed, completed_at, created_at, updated_at) "
                    "VALUES (?, NULL, ?, 0, 1, ?, 0, NULL, ?, ?)",
                    (pid, text, followup_order, now, now),
                )
                followup_order += 1

    n_proj = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    n_todo = conn.execute("SELECT COUNT(*) AS n FROM todos").fetchone()["n"]
    print(f"Seeded {n_proj} projects and {n_todo} todos into {db.DB_PATH}.")


if __name__ == "__main__":
    seed()
