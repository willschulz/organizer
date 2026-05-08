# Organizer — Feature Requests

Backlog of UI/UX and functionality improvements that aren't yet in the
main roadmap. Items here are not yet scheduled; they graduate to the
Organizer app's todo list (in the app itself) when prioritized.

---

## FR-001 — Drag-to-reorder todos

**Summary:** Drag todo items within a card to reorder them.

**Motivation:** The current workaround is editing `display_order` via
the API directly, which is not user-friendly. A smooth drag-and-drop
reorder is the natural interaction for a todo list.

**Scope:**
- Drag within a single project's todo list (same `project_id`).
- Reordering should persist via `PATCH /api/todos/{id}` with updated
  `display_order` values.
- The "blocker" item(s) could be exempt from reordering (pinned to the
  top), or draggable like any other item (simpler); TBD.
- Followup items (the "Then:" section) may want their own independent
  ordering separate from the main list.

**Implementation notes:**
- The HTML5 Drag and Drop API (`draggable`, `dragover`, `drop` events)
  is sufficient for desktop; no external library required.
- On mobile/iOS, the HTML5 DnD API has limited support — a touch-based
  fallback (e.g. long-press → drag) would be needed for full PWA parity.
  This can be deferred to a follow-up.
- After a drop, compute new `display_order` values for the affected rows
  and issue PATCH calls. A simple gap-based scheme (e.g. 0, 10, 20, ...)
  avoids renumbering all siblings on every drag.

**Priority:** Medium — the current fixed order is livable but reordering
is a frequent enough need to be worth building.

---
