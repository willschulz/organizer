# 0001 — Per-project velocity indicators in the deadline row

| Field | Value |
|-------|-------|
| Status | planning |
| Tracks | FR-008 |
| Depends on | none |
| Behavioral context | See chat 2026-05-13 — "project-level signal" recommendation, recast as a velocity stat next to each project's deadline. |

---

## Objective

Surface a per-project velocity score (rolling 7-day average of effort points
completed per day) in the deadline row of each project card. Users currently
see *which* project has a deadline and *which* has been recently active, but
not a per-project rate of progress. Putting velocity next to deadline lets the
user notice "this project has a deadline in 14 days and a velocity of 0.3
points/day — that's a problem" at a glance.

Success looks like: every project card with at least one completion in the
last 7 days shows a small numeric velocity badge in its deadline row, styled
to be visually parallel to the deadline (small, tabular-nums, muted color).
Projects with no recent completions show nothing (no noisy "0.0").

---

## Background — what's already in place

- `GET /api/stats` already returns `by_day[date][project_id] = effort_sum`
  for the last 70 days (see `organizer/main.py` `stats(...)` function).
  This is exactly the per-project, per-day completion data we need.
- The frontend already pulls this on every `reload()` and stores it in
  `state.stats.by_day` (`organizer/static/index.html`).
- The card-header grid in `renderCard` already has a `.card-deadline` slot
  at `grid-row: 2, grid-column: 1` (see `organizer/static/index.html`
  around line 1219). We can add a sibling element next to it.

---

## Design decisions

- **Velocity metric:** rolling 7-day mean of effort-points-completed-per-day,
  computed client-side from `state.stats.by_day`. Numerator = sum of effort
  points completed in the last 7 calendar days for this project (including
  today, partial). Denominator = 7 (not "days with any completion" — we
  want average per *calendar* day, so zero-completion days count).
- **Display format:** `~N.N/day` where N.N is rounded to one decimal place.
  Examples: `~0.4/day`, `~2.0/day`, `~1.3/day`. The `~` signals "rolling
  average, not exact."
- **Hidden when zero:** projects with 0 points over the last 7 days show no
  velocity badge. Avoids drowning the UI in `~0.0/day` for cold projects.
- **Hidden when no effort points set yet:** if the project has had
  completions but none of them had `effort` set, the fallback (1 per todo)
  still applies — the velocity will reflect raw todo throughput. This is
  consistent with how `/api/stats` already treats unset effort.
- **Color/styling:** match `.card-deadline` muted text style. Optional
  enhancement (not in v1): color the velocity badge based on whether
  it's enough to meet the deadline.

---

## Plan

### Step 1 — Frontend: compute velocity from existing stats

In `organizer/static/index.html`, add a helper near `sortProjects` or
`renderRow`:

```js
function projectVelocity7d(projectId) {
  const byDay = (state.stats && state.stats.by_day) || {};
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let sum = 0;
  for (let d = 0; d < 7; d++) {
    const date = new Date(today);
    date.setDate(date.getDate() - d);
    const dayData = byDay[localISODate(date)];
    if (dayData) sum += (dayData[String(projectId)] || 0);
  }
  return sum / 7;
}
```

Expected outcome: pure function returning a float, no UI yet.

### Step 2 — Frontend: render velocity badge in `renderCard`

In `renderCard` (`organizer/static/index.html`), where the deadline row is
assembled, add a sibling element:

```js
const v = projectVelocity7d(project.id);
const velocityEl = v > 0
  ? el('div', { class: 'card-velocity' }, [`~${v.toFixed(1)}/day`])
  : null;
const deadlineRow = el('div', { class: 'card-deadline-row' },
  [deadlineEl, velocityEl].filter(Boolean));
```

Place `deadlineRow` where `deadlineEl` currently goes in the header grid.

Expected outcome: card shows e.g. "in 14 days  ~0.4/day" or "no deadline
~1.3/day" or just "no deadline" if velocity is zero.

### Step 3 — CSS

Add to the deadline section in `organizer/static/index.html`:

```css
.card-deadline-row {
  grid-column: 1;
  grid-row: 2;
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.card-velocity {
  font-size: 11px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
}
```

The `.card-deadline` itself keeps its current styling; the row wrapper
just lays it out alongside the velocity.

Expected outcome: visually parallel, no layout shift on cards without
velocity.

### Step 4 — Verify and deploy

- Local-only check: open dev tools, confirm `projectVelocity7d(<some id>)`
  matches what's visible in the heatmap.
- scp `static/index.html` per `organizer-deploy.mdc` (no service restart
  needed; it's static).

---

## Open questions

- Should the velocity also re-fetch when a todo is completed? Currently
  `refreshHeatmap()` already runs on completion (see the checkbox handler
  in `renderTodoNode`), but it only re-renders the heatmap dots, not the
  cards. Two options: (a) call `render()` instead of just `renderHeatmap()`
  on completion — cheap because the data is already in `state.stats`;
  (b) replace only the affected card's velocity element. (a) is simpler.
- Should we display velocity *units* in a tooltip ("points / day, rolling
  7-day mean")? Probably yes, just on hover.

---

## Files likely touched

- `organizer/static/index.html` — `projectVelocity7d`, `renderCard`,
  CSS additions.
- (No backend changes expected; `/api/stats` already provides the data.)

---

## Execution log

<!-- Fill in when work begins. -->

---

## Post-completion state

<!-- Fill in when complete. -->
