# Handoff: Run Intelligence — Selector view (redesign)

> **Status:** Design ready for implementation
> **Fidelity:** Hi-fi
> **Owner:** Design — TestLookup
> **Target codebase:** `frontend/` (React + TypeScript + Tailwind, Vite)
> **Last updated:** 2026-05-10

---

## 1. Overview

The current `/intelligence` page asks the user to navigate a four-stage "Intelligence selection flow" (Run Selection → Failed Run Focus → Intelligence Launch → Passing Context) before any value is visible. Two of those four cards are marked "Skipped" because there are no failing runs, so the page ends up showing a near-empty Run Selection stub with `total_runs: 1`, a one-row "Recent Passing Runs" list, and nothing actionable.

The redesign replaces the stage-card flow with a verdict-first cockpit:

1. A **health verdict** at the top — "all clear" today, with composite health, deltas vs. last week, and a stacked bar of passed/flaky/failed runs.
2. A **search + filter bar** (date · branch · environment · outcome, plus saved views) for picking a run without the four-step flow.
3. A **runs table** showing the last 12 analyzed runs with status, duration, AI confidence meter, and authorship — the user clicks a row to drill in.
4. A right rail with **cross-run AI insights** (perf drift, flake-rising, coverage drop), **monthly spend tracking** (the original "Cost: —" placeholder, made real), and a **recent activity feed**.

Ship-note copy: *"Run Intelligence opens to the answer, not the picker. Health is on top, every run analyzed in the last day is one click away, and the AI's cross-run findings show up even when nothing is failing."*

**Goals**
- One scan of the top 600px tells you whether anything needs attention.
- "Nothing failing today" is still a useful page — AI insights surface drift, flake, and coverage even on green builds.
- Run picker is search + table, not a stage flow.
- The original empty fields (`Confidence: —`, `Cost: —`) become real, populated data.

**Non-goals**
- Per-run failure detail — that lives at `/runs/:id/intelligence` (the existing Run Intelligence detail page, see `design_handoff_run_intelligence/`).
- Release-gate config (lives at `/release-gate/:id`).

---

## 2. About the design files

The files in this bundle are **design references created in HTML** — a prototype showing intended look and behavior. They are **not production code to copy directly**.

The target codebase is React + TypeScript + Tailwind (existing `frontend/` app, see `frontend/CLAUDE.md`). Recreate the design using the existing component library (`MetricCard`, `StatusBadge`, `PageHeader`, `DataTable`, `EmptyState`) and CSS-variable token system (`frontend/src/index.css`).

Treat the HTML as the source of truth for **visual design and intended behavior**, not for **markup, class names, or DOM structure**.

---

## 3. Fidelity — Hi-fi

Pixel-perfect to the values in §8. The reference HTML uses the same CSS variables (`--color-bg`, `--gate-go`, `--color-accent`, `--status-passed-bg`, etc.) that already exist in `frontend/src/index.css`, so most colors will match by token name without translation.

---

## 4. Screens / views

### 4.1 Run Intelligence — Selector
- **File:** `screens/run-intelligence.html`
- **Route:** `/intelligence` (was `/intelligence`, same path)
- **Purpose:** Daily entry point for the Run Intelligence area. Answer "is anything wrong?" in one scan; if yes, drill into the specific run; if no, surface cross-run AI insights and let the user pick any recent run.
- **Entered from:** Top-nav "Intelligence" link, dashboard "Pipeline health" widget, Slack deep-links.
- **Exits to:** `/runs/:id/intelligence` (single run detail), `/runs/compare?left=:a&right=:b`, `/insights/perf-drift/:id`, `/release-gate/:id`.

**Layout (1440px target, ≥1280px supported)**
- Outer: max-width `1360px`, padding `24px 28px 80px`.
- Header row: title + sub (left), three actions (right). Wraps on narrow screens.
- **Verdict block:** 2-column (`1.5fr 1fr`), gap `28px`. Left: status tag + headline + lede + 3 CTAs. Right: composite health number + delta + stacked health bar + legend.
- **Filter bar:** flex row, single line on desktop. Search input grows, chips are fixed-width.
- **Body grid:** 2-column (`1.7fr 1fr`), gap `14px`.
  - Left: Recent runs analyzed (table)
  - Right column (top→bottom): Cross-run insights → Intelligence spend → Recent activity

**Sticky regions** — none. Page is a single scrollable column on narrow viewports (`grid-template-columns: 1fr` under 1100px).

**Components on this screen**

| Component | Position | See |
|---|---|---|
| Page header (title + sub + actions) | top, full-width | §5.1 |
| Verdict / health card | below header | §5.2 |
| Composite health summary | inside verdict (right) | §5.3 |
| Filter bar (search + chips) | below verdict | §5.4 |
| Runs table | left body | §5.5 |
| Status badges | inside table | §5.6 |
| AI-confidence meter | inside table | §5.7 |
| Cross-run insight card | right rail | §5.8 |
| Spend / budget card | right rail | §5.9 |
| Activity feed card | right rail | §5.10 |

---

## 5. Components

### 5.1 Page header
- Title `<h1>` — `24px / 700 / -0.01em`, `var(--color-text)`.
- Sub-line — `13px / 400`, `var(--color-text-muted)`, `4px` top margin.
- Right cluster: secondary buttons (`Decision trail`, `Compare runs`) and one primary button (`Analyze new run`).
- Buttons follow existing `<Button variant="secondary|primary">` from `frontend/src/components/ui/Button.tsx`.

### 5.2 Verdict / health card
- `1px` border using `rgba(34,197,94,0.32)` (green when healthy).
- Background: `radial-gradient(120% 100% at 0% 0%, rgba(34,197,94,0.10), transparent 55%)` over `var(--color-bg-card)`.
- `3px` left accent bar in `var(--gate-go)`.
- Padding `20px 22px`, radius `var(--radius-lg)`.
- 2-column grid `1.5fr 1fr`, gap `28px`. Collapses to 1 column under 1100px.
- Title `26px / 700 / -0.02em`, headline word in `#86efac`.
- Pulse dot (top eyebrow) animates `box-shadow` outward — see `@keyframes p` in the prototype.

**Verdict state machine** — the card recolors based on `health.worstStatus`:

| State | Border / accent / pulse | Headline color | Trigger |
|---|---|---|---|
| `all-clear` | `var(--gate-go)` / `#22c55e` | `#86efac` | 0 failed, ≤1 flaky retry |
| `attention` | `var(--gate-conditional)` / `#eab308` | `#fcd34d` | ≥2 flaky OR perf drift insight |
| `at-risk` | `var(--gate-no-go)` / `#ef4444` | `#fca5a5` | ≥1 failed run in window |

### 5.3 Composite health summary (right of verdict)
- Top row: large number `38px / 700 / tabular-nums`, color matches state.
- `/100` denominator in `18px`, muted.
- Delta `▲ 3.2 pts` in `12px`, green if positive, red if negative.
- Stacked bar: `grid-template-columns: <pass>fr <flake>fr <fail>fr`, height `8px`, `2px` gaps, `2px` padding, radius `999px`. Each segment is a separate `<i>` so widths animate cleanly when data updates.
- Legend: 4 inline items (passed · flaky · failed · total) with `8px` colored squares. Total floats right with `color: var(--color-text-faint)`.

### 5.4 Filter bar
- Border `1px solid var(--color-border)`, background `var(--color-bg-card)`, radius `var(--radius-lg)`, padding `10px 12px`.
- Search input grows (`flex: 1 1 280px`), with leading magnifier icon and trailing `/` keyboard hint (`.kbd`).
- Filter chips are buttons styled as pills:
  - Inactive: `1px solid var(--color-border)`, background `var(--color-bg-input)`, text `var(--color-text-secondary)`.
  - Active: `1px solid var(--color-accent)`, background `var(--color-accent-muted)`, text `var(--color-text)`.
- Saved-view chips sit right-aligned (`flex:1` spacer in between).
- Persist filter state in URL search params: `?range=24h&branch=main&env=prod&status=all`.

### 5.5 Runs table
- 6 columns: Run ID (96px fixed) · Trigger (flex) · Status · Duration · AI confidence · When (right-aligned, hides under 960px).
- Header: `11px / 600 / uppercase / tracking-wider`, muted, on `var(--color-bg-secondary)` with top + bottom border.
- Rows: `11px 14px` padding, `1px` bottom border, vertical-align middle.
- Hover: `background: var(--color-bg-hover)`, cursor pointer.
- Selected row: `background: rgba(68,147,248,0.06)` + `inset 3px 0 0 var(--color-accent)` box-shadow on the left.
- Trigger cell: commit title (`14px / 500`), then meta line (`11.5px / 400`, muted) with `<branch>` chip, author avatar (16px), author name, optional PR number.
- Branch chip: `<code>`-style, `1px` border, `var(--color-bg-secondary)` background, `11.5px` mono.

### 5.6 Status badges
- Pill: `3px 8px` padding, `12px / 500`, `1px` border, `var(--radius-pill)`, leading `6px` dot.
- Variants reuse existing semantic tokens:
  - **Passed** → `--status-passed`/`-bg`/`-bd`
  - **Failed** → `--status-failed`/`-bg`/`-bd`
  - **Flaky** → `--status-flaky`/`-bg`/`-bd` (label "Flaky · N retries")
  - **Broken** → `--status-broken`/`-bg`/`-bd`
  - **Running** → `#93c5fd` on `rgba(59,130,246,0.16)` w/ border `rgba(37,99,235,0.5)`

### 5.7 AI-confidence meter
- Inline `<span>` containing a `56×5` bar + percent label.
- Bar: `var(--color-bg-secondary)` track, gradient fill:
  - ≥80% → green `linear-gradient(90deg,#22c55e,#34d399)`
  - 60–79% → amber `linear-gradient(90deg,#eab308,#fcd34d)`
  - <60% → red `linear-gradient(90deg,#ef4444,#fca5a5)`
- Percent label `12px`, muted, after the bar.

### 5.8 Cross-run insight card
- Each insight: `14px 16px` padding, `1px` bottom border (none on last), flex row, `12px` gap.
- Icon tile: `32×32`, radius `8px`, `1px` border, tinted background + text per insight category:
  - **Perf drift** → violet (`168,85,247`)
  - **Flake rising** → amber (`245,158,11`)
  - **Coverage** → blue (`68,147,248`)
  - **Cost / efficiency** → teal (`20,184,166`)
- Title row: small uppercase category badge + headline. Body: 1–2 lines of muted detail with inline `<code>` for file paths and commit SHAs.

### 5.9 Spend / budget card
- 2×2 grid for "Used today" and "Avg per run" — each cell shows `11px uppercase` label + `22px / 700` value + small inline unit (`· 12 runs`, `· ~12k tokens`).
- Bottom row spans both columns: budget meter with label-pair on top (left + right), `6px` bar with teal gradient fill.
- Refreshes hourly from the analytics service; live-update on websocket `intel.spend.tick` if available.

### 5.10 Activity feed
- Rows: `9px 16px`, fixed-width left timestamp column (`52px`, `11px / tabular-nums`, muted), body text right.
- Body text `13px`, secondary color; user names bold + primary text color; SHAs / file paths / IDs in `<code>` with `var(--color-bg-secondary)` background.
- Trailing "View full activity log →" link, centered, accent color.

---

## 6. Interaction notes

### Default landing state
- Range filter defaults to **Last 24h**.
- All other filters default to **All …**.
- Sort defaults to recency (newest first). Sort indicator lives in the table header `<a>` link ("change sort").

### Row interactions
- **Click a run row** → navigates to `/runs/:id/intelligence` (the detail page).
- **Selected row** (when the user has soft-focused via ↑/↓) → highlighted as described in §5.5.
- **↵ (Enter)** with a selected row → drill in. The "Jump to most recent run" CTA in the verdict simulates pressing Enter on row 1.
- **Right-click** on a row → context menu: Open · Open in new tab · Compare with … · Export decision trail · Copy run URL.

### Verdict CTAs
- *Jump to most recent run* → `/runs/:topRowId/intelligence`.
- *View flaky recoveries (N)* → opens table filtered to `status=flaky`, drawer with retry breakdown.
- *Configure notifications* → opens the notification settings modal (existing).

### Insight CTAs
- Each insight row is clickable → opens the corresponding deep-dive:
  - Perf drift → `/insights/perf-drift/:testId`
  - Flake rising → `/tests/flaky?range=7d`
  - Coverage drop → `/runs/:runId/coverage`

### Empty / edge states
- **No runs in range** → swap the table body for an `<EmptyState>` with copy "No runs in this window — broaden the date filter or trigger a build."
- **No AI insights** → the cross-run card shows a single muted row: "No anomalies in the last 24h."
- **Spend over budget** → budget bar turns amber at 80%, red at 100%; verdict picks up a secondary chip "Budget at 92%".

### Keyboard
- `/` focuses search.
- `↑` / `↓` navigates rows.
- `↵` opens selected row.
- `c` opens compare picker.
- `?` opens the keyboard-shortcut help (existing global handler).

---

## 7. Data model

```ts
interface IntelligenceSelectorPage {
  health: {
    rangeLabel: string;                // "Last 24h"
    composite: number;                 // 0..100
    deltaVsPrev: number;               // signed
    state: 'all-clear' | 'attention' | 'at-risk';
    counts: { passed: number; flaky: number; failed: number; total: number };
  };
  runs: RunRow[];
  insights: Insight[];
  spend: {
    usedToday: number;                 // dollars
    runsToday: number;
    avgPerRun: number;
    avgTokens: number;
    monthlyUsed: number;
    monthlyBudget: number;
    daysLeftInCycle: number;
  };
  activity: ActivityRow[];
}

interface RunRow {
  id: string;                          // "ci-build-1247"
  title: string;                       // commit subject
  branch: string;
  prNumber?: number;
  author: { id: string; initials: string; name: string; avatarColorIdx: 1|2|3|4|5|6 };
  status: 'passed' | 'failed' | 'flaky' | 'running' | 'broken';
  retries?: number;                    // for flaky
  durationMs: number;
  aiConfidence: number;                // 0..1
  startedAt: string;                   // ISO
}

interface Insight {
  id: string;
  category: 'perf-drift' | 'flake-rising' | 'coverage' | 'cost';
  badge: string;                       // "Perf drift"
  title: string;                       // markdown-light: code-spans allowed
  body: string;                        // 1-2 sentences
  href: string;                        // CTA target
  affectedRunIds?: string[];
}

interface ActivityRow {
  id: string;
  ts: string;                          // ISO
  body: string;                        // simple markdown: **bold**, `code`
}
```

API endpoints (proposed):

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/intelligence/selector?range=24h&branch=&env=&status=` | `IntelligenceSelectorPage` |
| `GET` | `/api/runs?…` | paginated `RunRow[]` — existing endpoint, just used here |
| `GET` | `/api/intelligence/insights?range=24h` | `Insight[]` |
| `GET` | `/api/intelligence/spend?cycle=current` | spend sub-object |

---

## 8. Design tokens

All values are CSS custom properties already declared in `colors_and_type.css` (mirrored at `frontend/src/index.css`). **Do not invent new tokens** — if a value isn't here, ask in #design before adding one.

### Surfaces
- `--color-bg` — `#0d1117` (page)
- `--color-bg-card` — `#151b23` (panels, verdict)
- `--color-bg-secondary` — `#161b22` (chips, table header)
- `--color-bg-input` — `#0d1117` (search, filter chip)
- `--color-bg-hover` — `#1c2128`

### Borders
- `--color-border` — `#3d444d`
- `--color-border-light` — `#4a525c`

### Text
- `--color-text` — `#f0f6fc`
- `--color-text-secondary` — `#c9d1d9`
- `--color-text-muted` — `#9198a1`
- `--color-text-faint` — `#656d76`

### Semantic
- `--gate-go` — `#22c55e` (verdict accent on healthy)
- `--gate-conditional` — `#eab308` (verdict accent on attention)
- `--gate-no-go` — `#ef4444` (verdict accent on at-risk)
- `--status-passed` / `-bg` / `-bd`
- `--status-failed` / `-bg` / `-bd`
- `--status-flaky` / `-bg` / `-bd`
- `--status-broken` / `-bg` / `-bd`

### Type
- Page title `24px / 700 / -0.01em`
- Section H4 `14px / 600`
- Body `14px / 400 / line-height 1.5`
- Meta `11.5–12px / 400`, muted
- Eyebrow `11px / 600 / uppercase / tracking 0.08em`
- Metric `22–38px / 700 / tabular-nums`

### Radius & spacing
- `--radius-md` `8px` (buttons, inputs, chips)
- `--radius-lg` `12px` (cards, panels, filter bar)
- `--radius-pill` `9999px` (status badges)
- `--shadow-sm` `0 1px 2px rgba(0,0,0,0.25)`

---

## 9. Accessibility

- All filter chips and table rows are `<button>` / `<tr role="button" tabindex="0">` — keyboard reachable.
- Status badges include both a dot and a textual label; never color-only.
- AI confidence meter has an adjacent percent label; the bar is decorative (`aria-hidden="true"`).
- Verdict pulse animation honors `prefers-reduced-motion` — wrap the keyframe in `@media (prefers-reduced-motion: no-preference)`.
- Focus rings use `var(--color-ring)` `#4493f8` at `2px` outline, `2px` offset.
- Color contrast: all text on `--color-bg-card` passes WCAG AA at the documented sizes. Re-check if you introduce new tints.

---

## 10. Open questions for engineering

1. **Spend source of truth** — does `intelligence_spend` already exist in the analytics warehouse, or do we compute from `runs.intelligence_cost`? If the latter, add an aggregation worker.
2. **Insight ranking** — when there are >3 cross-run insights, what's the cap? Suggest top 3 by severity score, with a "See all (N)" link in the card header.
3. **Saved views** — does the `SavedView` table already cover this surface, or do we need a new scope `intelligence_selector`?
4. **Confidence threshold** — at what confidence value do we surface a "Low confidence" badge on the row itself (currently the meter is always shown but never labeled "low")?

---

## 11. Files in this bundle

- `README.md` — this file
- `screens/run-intelligence.html` — the redesigned page
- `tokens.css` — copy of `colors_and_type.css` (foundation tokens)
