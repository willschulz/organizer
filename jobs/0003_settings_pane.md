# 0003 — Settings pane (gear icon, right-edge slide-out)

| Field | Value |
|-------|-------|
| Status | planning |
| Tracks | FR-010 |
| Depends on | 0002 — the daily-goal constant exists and is read via `getDailyGoal()` |
| Behavioral context | First user-facing setting is the daily effort-point goal introduced by jobfile 0002. The pane is intentionally inaugurated for this one setting and designed to absorb future ones. |

---

## Objective

Add a gear icon to the upper-right of the topbar (right side of the
progress-dot row, where the dots have faded out under the mask). Clicking
it opens a slide-out settings pane from the right edge of the screen, the
first setting being **Daily effort goal** (numeric input, default 10).

Success looks like: clicking the gear smoothly slides a panel in from the
right; the panel contains a single labeled numeric input bound to the goal
setting; changing the value persists across reloads and immediately re-renders
the heatmap with the new goal threshold; clicking outside the pane or pressing
Esc closes it.

---

## Background — what's already in place

- Topbar layout: `#topbar-inner` already contains `#title`, `#heatmap`,
  `#status` (see `organizer/static/index.html` around line 796). The mask
  fade on `#heatmap` ends at 100% transparent — the right edge of the
  header is dim, perfect host for the gear button.
- Modal infrastructure exists for projects/todos (`<dialog>` elements,
  the `openProjectModal` helper). The settings pane is *not* a modal —
  it's a slide-out drawer. New pattern.
- No client-side persistence layer for arbitrary settings yet — only the
  `state.followupsOpen` set, which uses `localStorage`. We can either
  follow the localStorage pattern (single-device) or store server-side
  via a new endpoint (cross-device).

---

## Design decisions

- **Storage:** client-side `localStorage` for v1, key `organizer.settings`,
  value a JSON object (extensible). Single-user app + same browser most
  of the time means localStorage is sufficient. If the user later wants
  cross-device settings (laptop ↔ iOS PWA), we can migrate to a server
  endpoint without changing the UI contract.

  Schema:
  ```json
  { "daily_goal": 10 }
  ```

  Helpers:
  ```js
  function loadSettings() {
    try { return { daily_goal: 10, ...JSON.parse(localStorage.getItem('organizer.settings') || '{}') }; }
    catch { return { daily_goal: 10 }; }
  }
  function saveSettings(s) {
    localStorage.setItem('organizer.settings', JSON.stringify(s));
  }
  ```

  `getDailyGoal()` (defined in jobfile 0002) becomes:
  ```js
  function getDailyGoal() { return loadSettings().daily_goal; }
  ```

- **Pane interaction model:** slide in from the right, overlaying content.
  Closes on:
  - clicking outside the pane (backdrop click)
  - pressing Esc
  - clicking a close `×` button in the pane header
  - tapping the gear icon again (toggle behavior)

- **Width:** 320 px on desktop, full width minus a small inset on mobile.

- **Pane structure (DOM):**
  ```
  <aside id="settings-pane" class="hidden">
    <header>
      <h2>Settings</h2>
      <button class="close-btn">×</button>
    </header>
    <section class="settings-group">
      <label>
        Daily effort goal
        <input type="number" min="1" max="50" step="1" id="setting-daily-goal" />
        <div class="help">Used to scale the dot-color ramp and fill the
          ring around today's dot. The dots saturate at this value.</div>
      </label>
    </section>
  </aside>
  <div id="settings-backdrop" class="hidden"></div>
  ```

- **Gear icon placement:** absolutely positioned at the right edge of
  `#topbar-inner`, on top of the faded portion of the heatmap. Same size
  and styling as existing `.btn-icon` class (36 px circle).

- **No save button:** changes are applied immediately on input change
  (debounced or on `change` event). After save, call `renderHeatmap()` so
  the dots reflect the new goal at once. This keeps the pane lightweight
  and "live."

---

## Plan

### Step 1 — Add the gear icon to the topbar

In `organizer/static/index.html`, in the `<header id="topbar">` section
(around line 796), append a button after `#status`:

```html
<button id="settings-btn" class="btn-icon" type="button"
        aria-label="Settings" title="Settings">
  <!-- cog svg -->
</button>
```

Position it at the right edge with CSS:

```css
#topbar-inner { position: relative; }
#settings-btn {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 2;
}
```

Expected outcome: a gear button visible in the upper-right, not yet wired
to anything.

### Step 2 — Build the slide-out pane DOM

Append `<aside id="settings-pane">` and `<div id="settings-backdrop">` to
the body. Build the form via the existing `el(...)` helper or write the
HTML directly. Initial state: both elements have class `hidden`.

CSS:

```css
#settings-pane {
  position: fixed;
  top: 0; right: 0; bottom: 0;
  width: 320px;
  max-width: calc(100vw - 32px);
  background: var(--bg-elevated);
  border-left: 1px solid var(--border);
  box-shadow: -8px 0 24px rgba(0,0,0,0.08);
  transform: translateX(100%);
  transition: transform .2s ease;
  z-index: 100;
  padding: 16px;
  overflow-y: auto;
}
#settings-pane:not(.hidden) { transform: translateX(0); }
#settings-backdrop {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.18);
  z-index: 99;
  opacity: 0;
  transition: opacity .2s;
  pointer-events: none;
}
#settings-backdrop:not(.hidden) {
  opacity: 1;
  pointer-events: auto;
}
```

Expected outcome: pane slides in/out cleanly via class toggle.

### Step 3 — Wire open/close behavior

```js
const pane = document.getElementById('settings-pane');
const backdrop = document.getElementById('settings-backdrop');
const settingsBtn = document.getElementById('settings-btn');

function openSettings()  {
  pane.classList.remove('hidden');
  backdrop.classList.remove('hidden');
}
function closeSettings() {
  pane.classList.add('hidden');
  backdrop.classList.add('hidden');
}
settingsBtn.addEventListener('click', () => {
  pane.classList.contains('hidden') ? openSettings() : closeSettings();
});
backdrop.addEventListener('click', closeSettings);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !pane.classList.contains('hidden')) closeSettings();
});
```

Expected outcome: pane opens/closes via gear, backdrop, or Esc.

### Step 4 — Bind the daily-goal input

```js
const goalInput = document.getElementById('setting-daily-goal');
function applyGoal() {
  const v = Math.max(1, Math.min(50, parseInt(goalInput.value, 10) || 10));
  const s = loadSettings();
  s.daily_goal = v;
  saveSettings(s);
  renderHeatmap();  // re-render with new threshold
}
goalInput.addEventListener('change', applyGoal);
// initial value:
goalInput.value = loadSettings().daily_goal;
```

Expected outcome: changing the number live-updates the heatmap dots
and the today-ring fill threshold; value persists across reloads.

### Step 5 — Visual polish

- Ensure `loadSettings()` is called *before* the first `renderHeatmap()`
  in bootstrap (already true since `renderHeatmap` reads via
  `getDailyGoal()`).
- Confirm the pane works in the iOS PWA (test in standalone mode after
  deploying). Safe-area inset on the right may need
  `padding-right: max(16px, env(safe-area-inset-right))`.

### Step 6 — Deploy

scp `static/index.html` per `organizer-deploy.mdc`. No service restart.

---

## Open questions

- Should the gear icon temporarily replace `#status` rather than overlap it?
  On narrow screens both at once may crowd. Decide during impl based on
  visual feel.
- Should we add a "Reset to defaults" button? Probably not for v1 — only
  one setting exists.

---

## Future settings this pane should be designed to absorb

- Per-week velocity goal (parallel to daily goal)
- Weekend rendering (offset on/off, hide weekend dots entirely, etc.)
- Heatmap window size (currently the mask + window are hardcoded)
- Whether to auto-collapse the Later list on card render

(Not in v1; listed so the pane's information architecture leaves room.)

---

## Files likely touched

- `organizer/static/index.html` — gear button HTML, pane HTML, CSS
  additions, JS for settings store + open/close + binding.

---

## Execution log

<!-- Fill in when work begins. -->

---

## Post-completion state

<!-- Fill in when complete. -->
