# 0002 — Daily effort goal: ring around today's dot

| Field | Value |
|-------|-------|
| Status | planning |
| Tracks | FR-009 |
| Depends on | none (settings pane FR-010 makes the goal user-adjustable; ship this first with a hardcoded 10-point default) |
| Behavioral context | See chat 2026-05-13 — "give today a forward-looking element" and "continuous color ramp with a milestone marker" recommendations. |

---

## Objective

Replace today's static circle-outline with a *circular progress ring* that
fills clockwise (from 12 o'clock) with green as effort points are completed
during the current local day. Once the daily goal threshold is reached, mark
that day's dot with a small star. Also: switch the dot color scale from the
current 4-bin step function to a continuous green-intensity ramp from 0 to
`goal`, with the star indicating "≥ goal" reached.

Success looks like:
- The ring around today's dot visibly fills throughout the day as effort
  points accumulate. At 0 points the ring is the current faint outline.
  At `goal` points (default 10) it is a complete green circle.
- Any day in the trail with effort ≥ goal has a small star (☆ → ★) overlaid
  on or adjacent to the dot.
- All dots use a continuous green ramp (gray at 0 → solid green at goal).
- The today-circle remains *centered* on the dot at all DPRs (the existing
  alignment bug — Organizer todo #80 — should be fixed in this pass).

---

## Background — what's already in place

- `renderHeatmap` in `organizer/static/index.html` currently uses 4 discrete
  CSS classes (`hm-0`, `hm-1`, `hm-2`, `hm-3`) based on count thresholds
  (`<=3`, `<=9`, `>9`). The today-dot has a separate `.hm-today` outline
  class.
- `/api/stats` returns daily effort totals; sub-day granularity is implicit
  in `completed_at` timestamps but is not currently exposed via the API.
- For the ring fill, we only need the *aggregated total for today*, which
  `by_day[today]` already provides. The ring updates whenever `renderHeatmap`
  runs, which is on every completion (via `refreshHeatmap` in the checkbox
  handler) and on resize (via the `ResizeObserver`). No backend change.

---

## Design decisions

- **Goal storage:** hardcoded constant `DEFAULT_DAILY_GOAL = 10` for v1,
  to be replaced by a user-adjustable setting in jobfile 0003.
- **Dot color ramp:** continuous gradient. Computed as
  `intensity = Math.min(1, effort_sum / GOAL)`. Apply via CSS variable on
  each dot: `style="--dot-intensity: 0.62"`. CSS uses this to interpolate
  between `var(--border)` (0) and `var(--green)` (1).
- **Star overlay:** for days where `effort_sum >= GOAL`, append a star
  element. Visually small (≈8 px), centered above the dot or offset
  diagonally. Use a CSS pseudo-element so DOM stays clean. Color: match
  `var(--green)` solid.
- **Ring (today only):** SVG element wrapping the today-dot, with a single
  `<circle>` using `stroke-dasharray` / `stroke-dashoffset` to encode
  fill fraction. Or pure CSS with `conic-gradient(from 0deg, var(--green)
  X%, transparent X%)`. Conic-gradient is simpler. Ring radius slightly
  larger than the dot. The dot itself sits inside the ring.
- **Alignment fix (todo #80):** the current ring uses `outline` which
  doesn't always center perfectly. Switching to a wrapper div with an
  absolutely-positioned ring should fix this for free.

---

## Plan

### Step 1 — Define the goal constant

In `organizer/static/index.html`, near the top of the script section:

```js
const DEFAULT_DAILY_GOAL = 10;   // adjustable via settings pane (jobfile 3)
function getDailyGoal() { return DEFAULT_DAILY_GOAL; }   // wrap for later
```

Expected outcome: one place to change the threshold; jobfile 0003 will
replace `getDailyGoal()` with one that reads from settings.

### Step 2 — Refactor the dot CSS to a continuous ramp

Remove `.hm-0`..`.hm-3` and the today-outline. Add:

```css
.heatmap-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  flex-shrink: 0;
  background: color-mix(in srgb,
    var(--green) calc(var(--dot-intensity) * 100%),
    var(--border));
}
```

Where `--dot-intensity` is a per-dot CSS variable in 0..1. Falls back
gracefully on browsers without `color-mix` (still readable).

Expected outcome: the dot is now visually parameterized by a single
intensity number per day.

### Step 3 — Update `renderHeatmap`

Replace the bin lookup with:

```js
const goal = getDailyGoal();
const intensity = Math.min(1, count / goal);
const dot = document.createElement('div');
dot.className = 'heatmap-dot';
dot.style.setProperty('--dot-intensity', intensity.toFixed(3));
if (count >= goal) dot.classList.add('hm-goal-met');
if (daysAgo === 0) dot.classList.add('hm-today');
```

Expected outcome: each dot's color reflects today's progress on a
continuous scale; star eligibility flagged.

### Step 4 — Star overlay

Wrap each dot in a small positional container (or use `::after`):

```css
.heatmap-dot.hm-goal-met::after {
  content: "★";
  position: absolute;
  font-size: 7px;
  color: var(--green);
  top: -6px;
  left: 50%;
  transform: translateX(-50%);
  pointer-events: none;
}
.heatmap-dot { position: relative; }
```

Adjust size/position iteratively for legibility. Should not visually
overpower the dot.

Expected outcome: a small star appears centered above each day that
hit the goal.

### Step 5 — Today's ring (the centerpiece)

Replace the `.hm-today` outline with a conic-gradient ring:

```css
.heatmap-dot.hm-today {
  /* The ring is drawn via a wrapper or pseudo-element */
}
.heatmap-dot.hm-today::before {
  content: "";
  position: absolute;
  top: -3px;
  left: -3px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: conic-gradient(
    from 0deg,
    var(--green) calc(var(--dot-intensity, 0) * 360deg),
    transparent calc(var(--dot-intensity, 0) * 360deg)
  );
  /* Mask out the inner circle so we only see the ring */
  mask: radial-gradient(circle, transparent 3.5px, black 4px);
  -webkit-mask: radial-gradient(circle, transparent 3.5px, black 4px);
}
```

The wrapper rules require `position: relative` and adequate spacing
(slight increase in `.heatmap-dot` margin or container padding) so the
ring doesn't clip the adjacent dot.

Expected outcome: the ring around today's dot fills clockwise from 12
o'clock as effort accumulates. The same `--dot-intensity` variable drives
both the dot color and the ring fill, so they update together.

### Step 6 — Verify alignment (organizer todo #80)

Manually inspect at multiple zoom levels and DPRs. The ring should be
perfectly centered on the dot. If misalignment persists, fall back to a
wrapper `<div>` containing the dot + ring as siblings, positioned by
flexbox rather than `top`/`left` math.

### Step 7 — Deploy

scp `static/index.html` per `organizer-deploy.mdc`. No service restart.

---

## Stretch / related (separate from the three approved features but
discussed in the same chat)

- **Weekend dot offset:** move Sat/Sun dots ~2 px lower than weekday dots
  to visually distinguish them, so a desirable pattern of "weekdays green,
  weekends empty" reads as such rather than as a noisy gap. Implementation:
  per-dot inline `margin-top: 2px` when the day-of-week is 0 or 6.
  Decide before starting whether to fold into this jobfile or split out.

---

## Open questions

- Star vs. some other glyph (e.g. a thicker dot, a sparkle): try a few in
  isolation before committing. Don't want it to feel game-y.
- Should days that *exceeded* the goal scale beyond solid green
  (e.g. add a second star at 2× goal)? Probably no — flatten above goal,
  per "no anchoring on aggregate totals" principle.
- Should the ring animate when it fills (CSS transition on
  `--dot-intensity`)? Subtle yes; jarring no. Try with `transition:
  background .3s` and adjust.

---

## Files likely touched

- `organizer/static/index.html` — heatmap CSS section (around line 119),
  `renderHeatmap` function (around line 1779), goal constant.

---

## Execution log

<!-- Fill in when work begins. -->

---

## Post-completion state

<!-- Fill in when complete. -->
