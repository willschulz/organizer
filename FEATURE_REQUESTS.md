# Organizer — Feature Requests

Backlog of UI/UX and functionality improvements that aren't yet in the
main roadmap. Items here are not yet scheduled; they graduate to the
Organizer app's todo list (in the app itself) when prioritized.

---

## FR-001 — Drag-to-reorder todos *(implemented)*

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

**Status:** Implemented alongside nested-todo drag (FR-003). SortableJS handles
drag-to-reorder, drag-to-nest, and drag-to-unnest in one pass. Touch/mobile
included via SortableJS fallback.

---

## FR-002 — Deadline-aware within-row sort order

**Summary:** Within each category row, projects with the soonest
deadline should appear leftmost; projects without a deadline should
appear rightmost.

**Motivation:** Adding a deadline to a project currently has no effect
on its position in the row — it stays wherever `display_order` puts it.
A new project with an imminent deadline ends up buried at the right end
of the row, which defeats the purpose of setting the deadline.

**Desired sort key (per row, left to right):**
1. Projects with a deadline, ascending by date (soonest first).
2. Projects without a deadline, descending by "last activity" —
   defined as the most recent of:
   - `MAX(todo.updated_at)` across all todos on the project, or
   - `MAX(todo.completed_at)` across completed todos, or
   - `project.updated_at` as a fallback when there are no todos.
   This surfaces the most actively-worked project at the left edge of
   each row without any manual bookkeeping.

**Rationale for the activity heuristic:** deadline-less projects don't
have an urgency signal, so recency-of-work is the best proxy for "what
should I look at first." A project where a todo was just completed or
edited is more salient than one that hasn't been touched in weeks.

**Scope:**
- Sort is applied at render time in the frontend (`renderRow`) — no
  backend change required; `todos[].updated_at`, `todos[].completed_at`,
  and `project.updated_at` are all already returned in
  `GET /api/projects`.
- The drag-to-reorder feature (FR-001) should only apply within each
  group (deadline and no-deadline separately), or be suspended entirely
  while automatic sorting is active, to avoid confusion about cards
  snapping back after a manual drag.

**Priority:** High — deadline sorting is the whole point of setting a
deadline on a card; activity-based ordering makes the no-deadline group
self-organizing.

---

## FR-003 — "Just-discovered blocker" user story *(handled by nesting)*

**Summary:** While working on a todo, you discover a new prerequisite (e.g. the
longer-window cap sweep is now blocked by GCP-box OOM). You need a way to record
this without losing context on the in-flight task.

**Resolution:** The nested-todo UI handles this natively. Add a sub-todo under
the in-flight parent todo to represent the newly-discovered blocker. The parent
todo stays open; the sub-todo is the concrete next action. "What is blocking
this?" is answered by "which sub-todos remain incomplete?", without requiring a
separate flag or a flat-list position change.

No separate feature request is needed — this is precisely the use case that
motivated implementing nesting. Cross-reference: Organizer todo #52 (nested-todo
UI was already tracked before this user story was surfaced).

---

## FR-004 — Drag todo from main list into "Later…" list

**Summary:** Allow dragging a todo item from the main todo list into the
"Later…" list (and vice versa), using the same SortableJS drag mechanism
already used for reordering within a list.

**Motivation:** Currently the two lists are separate drag contexts. Promoting
or demoting an item requires deleting and re-creating it. Drag-across should
feel natural given that drag-to-reorder already works within each list.

**Scope:**
- The "Later…" list must adopt the same nesting and effort-pointing schema
  as the main todo list (it currently lacks these, which is a prerequisite
  for this feature). Nesting and effort points on a "Later…" item must
  survive a drag back to the main list without data loss.
- SortableJS `group` option allows items to be shared/moved between two
  lists — this is the recommended implementation path.
- Backend: dragging between lists likely maps to toggling a
  `is_later` (or equivalent) flag on the todo row via `PATCH /api/todos/{id}`.

**Priority:** Medium.

---

## FR-005 — Remove up/down buttons from "Add todo…" field; easter-egg drag-to-place

**Summary:** Once FR-004 is in place (drag-across-lists works), the up/down
position buttons in the "Add todo…" field become unnecessary and should be
removed. Pressing Enter should simply append the new item at the bottom of the
main todo list, from where it can be dragged to the desired position.

**Easter egg:** As an alternative to Enter, the user should be able to drag the
typed text directly from the input field to the target position in the list
(i.e. the item is created at the drop location rather than appended to the
bottom). This is a discoverable-but-not-required power-user gesture.

**Priority:** Low (depends on FR-004).

---

## FR-006 — Fix resting scroll position for wide rows (e.g. "Side Projects")

**Summary:** When a category row is wide enough that its project cards overflow
the viewport, the current resting (un-scrolled) position left-aligns the
*first card's left edge* with the *browser window's left edge*. This is ugly
because it is misaligned with the narrower rows above/below it (whose first
cards sit at the row-header left margin). The resting position should align
the first card with the left margin of the row header, matching the other rows.

**Reproduction:** Open the app at desktop width. Observe the "Side Projects"
row (or any row with enough cards to overflow). Note that the leftmost visible
card hugs the very left edge of the window rather than sitting flush with the
card-area left boundary that narrower rows use.

**Desired fix:** Set the initial `scrollLeft` (or equivalent CSS transform
origin) of wide rows so the first card's left edge aligns with the left margin
of the row header / the first cards of non-overflowing rows.

**Priority:** Low (cosmetic).

---

## FR-007 — Extend progress-dot trail to full browser width

**Summary:** The "fade out to right" progress-dot trail (history dots that fade
off the right edge of a project card) looks good at narrow viewports but the
dots stop well short of the right edge at wide desktop widths — the trail
doesn't span the full window width.

**Motivation:** From a design perspective the dots should continue all the way
across the screen regardless of window width. The amount of history encoded
in the dots doesn't need to grow (we don't have much history yet anyway);
the dots just need to be distributed across the full available width rather
than stopping at a fixed pixel count.

**Suggested fix:** Size the dot-trail container to `100vw` (or the full width
of its parent) rather than a fixed width, and distribute the dots evenly
across that space.

**Priority:** Low (cosmetic; feature was just added).
