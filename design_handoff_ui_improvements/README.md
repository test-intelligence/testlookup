# Handoff: TestLookup UI design improvements (audit remediation)

## Overview

This package turns the July 2026 design audit of the TestLookup frontend into an actionable work plan. The audit reviewed the shipped app (Signal theme, multi-theme system, 40+ routes) at code level; **14 findings** are organized here as three phases. Full findings with visual context: open `review/TestLookup UI Design Review.html` in a browser.

All work happens in the existing `frontend/` codebase (React + TypeScript + Tailwind + CSS custom-property themes). No new design direction — this hardens and completes what's already there.

## Ground rules for every change

- **Never introduce raw palette colors.** All color must come from theme tokens (`--color-*`, `--status-*`, `--gate-*`, `--cat-*`). This is the root cause behind most findings.
- Verify each change in **at least 2 themes, one of them light (Lab)** — light-theme legibility is where hardcoded colors break.
- Prefer editing shared primitives (`components/ui/*`) over page-local fixes; delete page-local duplicates as you migrate.

---

## PHASE 1 — Stop the bleeding (~2–3 days, mechanical)

### 1.1 Tokenize the 201 hard-coded palette colors  ⟨S1⟩

**Problem:** 201 matches of raw Tailwind palette classes (`text-emerald-400`, `bg-red-900/40`, `text-amber-300`, `text-green-400`…) plus literal hexes bypass the theme system. Every one is a light-theme defect.

**Find them:**
```bash
grep -rnE "text-(emerald|green|red|amber|yellow|purple|blue)-[0-9]|bg-(emerald|green|red|amber|orange|purple|blue)-[0-9]" frontend/src --include="*.tsx"
```

**Mapping table** (apply mechanically):

| Raw class (any shade) | Replace with |
|---|---|
| `text-emerald-*`, `text-green-*` (status/success) | `text-[var(--status-passed)]` |
| `bg-emerald-*/N`, `bg-green-*/N` (soft fills) | `bg-[var(--status-passed-bg)]` |
| `border-emerald-*`, `border-green-*` | `border-[var(--status-passed-bd)]` |
| `text-red-*` (failure/error) | `text-[var(--status-failed)]` |
| `bg-red-*/N` | `bg-[var(--status-failed-bg)]` |
| `border-red-*` | `border-[var(--status-failed-bd)]` |
| `text-amber-*`, `text-yellow-*` (warning/broken) | `text-[var(--status-broken)]` |
| `bg-amber-*/N` | `bg-[var(--status-broken-bg)]` |
| `text-purple-*` (flaky/AI) | `text-[var(--status-flaky)]` (flaky) or `text-[var(--color-purple)]` (AI/decision) |
| `text-blue-*` (info/accent) | `text-[var(--color-accent)]` |
| Trend up/down greens/reds | `--status-passed` / `--status-failed` |

Judgment calls: skipped/gold contexts → `--status-skipped`; release-gate contexts → `--gate-go/--gate-conditional/--gate-no-go`. The ProfilePage avatar-color swatches are **user data, not theme UI — leave them**.

### 1.2 Rewrite `.badge-*` classes onto status tokens  ⟨S1⟩

`src/index.css` (~line 373) defines badges on raw emerald/red/amber/orange/purple **directly below the token definitions**. Replace:

```css
.badge-passed  { @apply inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium;
                 color: var(--status-passed);  background: var(--status-passed-bg);
                 border: 1px solid var(--status-passed-bd); }
/* repeat: failed, skipped, broken (use --status-broken*), flaky (--status-flaky*) */
```

### 1.3 ESLint guard so it can't regress

Add to the ESLint config (the repo compiles an adherence config — extend it):

```js
'no-restricted-syntax': ['error', {
  selector: 'Literal[value=/\\b(text|bg|border)-(emerald|green|red|amber|yellow|orange|purple|blue)-[0-9]{2,3}\\b/]',
  message: 'Use theme tokens (--status-*, --gate-*, --color-*) instead of raw palette classes.',
}]
```

### 1.4 Fix the hover-only profile menu  ⟨S1 accessibility⟩

`src/components/layout/TopBar.tsx` → `UserProfileDropdown` (lines ~44–71) opens via `opacity-0 group-hover:opacity-100` with no click handler, no `aria-expanded`, no keyboard path — **Sign out is unreachable by keyboard/touch.**

Mirror the notification bell's pattern in the same file: `useState(open)` + `useRef` outside-click close + Esc close; trigger becomes `<button aria-haspopup="menu" aria-expanded={open}>`; menu items are real `<button>`s. ~30 lines.

### 1.5 MetricCard fixes  ⟨S2⟩

`src/components/ui/MetricCard.tsx`:
1. **Remove `role="status" aria-live="polite"`** from the card root (line ~33). Dashboards with 6–8 cards re-announce every value on each SWR poll. If update announcements are wanted, one visually-hidden `aria-live` region per page saying "Dashboard updated".
2. **Add `positiveDirection?: 'up' | 'down'` prop** (default `'up'`). Trend color = `trend_direction === positiveDirection ? passed : failed`. Update callers: Failed/Flaky/Duration metrics pass `positiveDirection="down"`.
3. Its accent + trend colors go through the 1.1 mapping (`text-emerald-400` → tokens).

---

## PHASE 2 — Consolidate (~1–2 weeks)

### 2.1 Make StatusBadge the only status renderer  ⟨S1⟩

`components/ui/StatusBadge.tsx` has **2 importers**; 30+ pages hand-roll `STATUS_COLORS` maps (AgentStatusPage, CanonicalDetailPage, FlakyCoachPage, LiveExecutionPage, IntelligenceHubPage, settings/*…). Extend StatusBadge to cover the variants pages actually need:

```tsx
interface StatusBadgeProps {
  status: 'passed'|'failed'|'skipped'|'broken'|'flaky'|'pending'|'running'
  variant?: 'pill' | 'text' | 'dot'   // pill default
  size?: 'sm' | 'md'
}
```
All colors from `--status-*`. Migrate pages one-by-one; **delete each page-local map as you go**. Acceptance: `grep -rn "STATUS_COLORS\|statusColor" frontend/src/pages` returns nothing.

### 2.2 Shared VerdictBand + WATCH token  ⟨S2⟩

- Add per-theme tokens: `--gate-watch`, `--gate-watch-bg`, `--gate-watch-bd` (Signal: `#f5d76e` family — distinct from `--gate-conditional`'s amber).
- Extract one `components/ui/VerdictBand.tsx` from the duplicate half-tokenized `VERDICT_THEME` maps in `OverviewPage.tsx` (~34–120) and `RunsPage.tsx` (~107–160). All colors tokenized.
- **Shape-code verdicts, don't rely on hue alone** (WATCH lemon vs CONDITIONAL amber is indistinguishable for deutan/protan users): GO = filled pill + check icon; WATCH = **outline** pill + eye icon; CONDITIONAL = filled + triangle; NO_GO = filled + octagon.

### 2.3 Custom project switcher  ⟨S2⟩

Replace the native `<select>` in `TopBar.tsx` (~141–160) with a ThemePicker-style popover: search input (repos with many projects), pinned "All Projects" for admins, active check, per-project health dot (pass-rate tone). Keyboard: arrows + Enter, Esc closes. Reuse ThemePicker's structure.

### 2.4 Sidebar cleanup  ⟨S2⟩

`src/components/layout/Sidebar.tsx`:
1. **Remove `/runs?upload=1` from nav** (it's an action, not a place — it can never show active). Add an "Upload report" `btn-secondary` to the Runs page header instead (keep the `manual_upload` flag gate).
2. **Re-icon:** Defects: `Gauge` → `OctagonAlert`. Break up the 4-shield cluster: Coverage keeps `ShieldCheck`; Release Gate → `Milestone`; Quarantine → `Ban`; Policies → `Scale`.
3. **Unify the group-row interaction:** clicking the row when its section is already active toggles expansion; otherwise navigates (and expands). Keep the chevron as a secondary affordance with `aria-expanded`.
4. `MyFailuresLink` badge colors → `--status-failed` tokens (currently raw rgba/hex).

### 2.5 Type floor + timestamps  ⟨S2/S3⟩

- Sweep `text-[9px]` and `text-[10px]` → `text-[11px]` minimum (`grep -rn "text-\[9px\]\|text-\[10px\]" frontend/src`). Where space is too tight at 11px, drop the text and use icon + `title`.
- Notification panel (`TopBar.tsx` ~214): `toLocaleString()` → `dayTimeAgo()` from `@/utils/formatters` (already exists), absolute time in `title`. Apply to any other list dumping raw locale strings.

### 2.6 Keyboard-accessible table rows  ⟨S3⟩

`.table-row` is a clickable div. Make the primary cell a real `<Link>` with the stretched-link pattern (row `relative`, link `::after absolute inset-0`), restoring middle-click/cmd-click. Or minimally: `tabindex={0}` + Enter/Space handlers.

### 2.7 Aurora perf  ⟨S3⟩

`index.css` body uses `background-attachment: fixed` — disables composited scrolling in Chromium (jank on the Live table). Move the aurora to a dedicated layer:

```html
<div aria-hidden="true" style="position:fixed; inset:0; z-index:-1; pointer-events:none;
     background-image:var(--app-bg-image,none); background-repeat:no-repeat;"></div>
```
(rendered once in `AppLayout`), and drop `background-image`/`background-attachment` from `body`.

---

## PHASE 3 — Differentiate (quarter bets — get UX sign-off before building)

- **⌘K command palette:** global overlay on `/` and `⌘K` — recent runs/entities, jump-to-page, scoped prefixes (`run:`, `test:`, `defect:`), verbs ("Compare last two runs"). TopBar search becomes the palette trigger; SearchPage stays as "all results".
- **Investigation thread ribbon:** persistent context strip carrying run/filter state across Runs → Failure Analysis → Deep Investigate → Defects → Release Gate, with one-click return.
- **Density toggle:** TopBar control switching a `--row-pad` / font-size var set (comfortable ↔ compact) for all tables.
- **Decompose monoliths:** RunsPage (2,211 lines), OverviewPage (1,207), IntelligenceHubPage (946) → shared organisms (`VerdictBand`, `RunListRow`, `PassRateMeter`, `TrendDelta`).

---

## Definition of done (phases 1–2)

- [ ] Palette-class grep returns **0** matches in `frontend/src` (excl. ProfilePage avatar swatches); ESLint rule active.
- [ ] `.badge-*` classes consume `--status-*` tokens.
- [ ] All five status states legible in **Lab (light)** and Signal: badges, verdict bands, trend deltas, live indicators.
- [ ] Profile menu: opens on click, `aria-expanded`, Esc + outside-click close, Sign out reachable by keyboard.
- [ ] MetricCard: no per-card live regions; `positiveDirection` respected (Failed↓ = green).
- [ ] StatusBadge is the single status renderer; page-local `STATUS_COLORS` maps deleted.
- [ ] One shared VerdictBand; WATCH vs CONDITIONAL distinguishable by shape/icon, not hue alone.
- [ ] Project switcher is a themed popover consistent with ThemePicker.
- [ ] Sidebar: no action-as-nav; distinct icons; single-target group rows.
- [ ] No functional UI text below 11px; list timestamps are relative with absolute on hover.
- [ ] Table rows keyboard-activatable and middle-clickable.
- [ ] No `background-attachment: fixed`; scrolling the Live table doesn't repaint the aurora.

## Files

```
design_handoff_ui_improvements/
├── README.md                                   ← this work plan
└── review/
    └── TestLookup UI Design Review.html        ← full audit: findings, evidence index, visuals
```
