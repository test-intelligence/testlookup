# Handoff: Releases (redesign)

> **Status:** Design ready for implementation
> **Fidelity:** Hi-fi
> **Owner:** Design — TestLookup
> **Target codebase:** `frontend/` (React + TypeScript + Tailwind, Vite)
> **Last updated:** 2026-05-11

---

## 1. Overview

The current `/releases` page is a thin shell around an incomplete release record. A single placeholder release ("goog · 1.55") expands to **five zero‑value stat tiles** (Runs 0 · Tests 0 · Passed 0 · Failed 0 · Pass Rate —%), a giant "No workflow stages found" empty state, an unsigned Compliance Pack form that has no business being shown on a Planning release, an empty "Release Phases" section, and an empty "Linked Test Runs" section. Status tabs sit in the upper right with no counts. There is no portfolio view, no gate decision, no aging signal, no owner attribution, and nothing on the screen tells the QA lead which release ships next or what is blocking it.

The redesign turns the page into a release‑portfolio dashboard that puts the **release gate decision** front‑and‑center and reserves the long form (phases, compliance pack, audit trail) for releases that have actually entered the pipeline.

1. A **verdict band** that names the most consequential release this week and the items left to clear before it ships ("1.55-rc.2 is on track — 2 items to clear before Friday").
2. A **KPI strip** with four pills — In progress · Ready to ship · Blocked · Released (30d) — each with a delta and (where it pays its way) a sparkline laid out next to the metric, not on top of it.
3. A **filter bar** with counted tabs (All 14 · Planning 5 · In progress 6 · Released 11 · Cancelled 2), a `/`-shortcut search, and three filter chips (Gate / Owner / Due).
4. A **release list** where each card carries: identity (name + semver + stage badge), owner avatar, due date with overdue colouring, **gate decision badge** (Go / Conditional / No-Go / Not evaluated), three composite gate metrics (Composite, Coverage, Flake) with their thresholds inline, a four-segment pass/fail/flake/skip bar, a six-chip **phase pipeline**, and a row of **actionable blockers** with owner + ticket + suggested action.
5. A **right rail** of derived views — Shipping This Week (date-tiled list), Aging Signals (releases stuck in a phase), Compliance Packs (signed audit ZIPs with SHA-256 prefix), and a Recent Activity feed.

The Planning release that was previously an empty shell is recast: phase chips show the pipeline ahead, and a single dashed bar offers two real next actions — "Clone from 1.54.1" or "Generate from PRD" — instead of dumping a compliance form on it.

Removed: the "No workflow stages found" empty state, the always-on Compliance Pack form, the all-zero stat tiles, the disembodied Status tab pills.

Ship-note copy: *"Releases is now a portfolio view: gate decisions, blockers, owners, and aging signals at a glance — with the phase pipeline and compliance pack shown only when the release is in flight."*

**Goals**
- A QA lead can answer "what's shipping this week and what's at risk?" in one scan.
- Every release carries a **gate decision** (Go / Conditional / No-Go / Not evaluated) and the three numbers that explain it.
- Empty states are rare and useful — they offer the one or two real next actions, not a generic CTA.
- Aging is visible at the portfolio level (right-rail panel) and at the release level (overdue date colouring).

**Non-goals**
- Per-release detail page (clicking a release navigates to `/releases/:id` — that view is out of scope).
- Gate-policy editor (the screen shows the cap; editing it lives in `/settings/gates`).
- Calendar view (the toggle is wired but the calendar layout itself is a separate handoff).

---

## 2. About the design files

The files in this bundle are **design references created in HTML** — a prototype showing intended look and behaviour. They are **not production code to copy directly**.

The target codebase is React + TypeScript + Tailwind (existing `frontend/` app, see `frontend/CLAUDE.md`). Recreate the design using the existing component library (`Card`, `Button`, `Badge`, `Segmented`, `Avatar`, `ProgressSegments`, `Tooltip`, `Sparkline`) and the CSS-variable token system in `frontend/src/index.css`.

Treat the HTML as the source of truth for **visual design and intended behaviour**, not for **markup, class names, or DOM structure**.

---

## 3. Fidelity — Hi-fi

Pixel-perfect to the values in §6. The reference HTML uses the same CSS variables (`--color-bg`, `--color-bg-card`, `--gate-go`, `--gate-conditional`, `--gate-no-go`, `--color-accent`, `--brand-500`, `--status-passed`, etc.) that already exist in `frontend/src/index.css` (see `tokens.css` in this bundle).

---

## 4. Screens / views

### 4.1 Releases (portfolio)
- **File:** `screens/releases.html`
- **Route:** `/releases`
- **Purpose:** Give release/QA leads a one-screen view of every active release, its gate decision, and what's blocking it.
- **Entered from:** Sidebar nav → Releases, dashboard "Shipping this week" widget, deep links from Slack notifications.
- **Exits to:** `/releases/:id`, `/releases/new`, `/runs?releaseId=…`, `/settings/integrations/jira`, ticket links in blocker rows.

**Layout (1440px target, ≥1280px supported)**
- Outer: `max-width: 1320px`, padding `24px 28px 80px`.
- Header row: title + sub (left), action cluster (Export schedule · Calendar view · New release) right.
- **Top strip** (`grid 1.35fr 2fr`, gap `14px`):
  - Verdict band (gate-conditional left accent, gradient bg)
  - KPI strip — `grid-cols 4`, each card uses an internal `flex` row (label+value on the left, sparkline as a flex sibling on the right, **never `position:absolute`**). One sub-line under the row.
- **Filter bar** — segmented tab control + search + 3 filter chips + list/calendar view toggle.
- **Body grid** (`grid 1fr 340px`, gap `18px`):
  - Left: stacked release cards (gap `10px`).
  - Right: four panels stacked (Shipping this week · Aging signals · Compliance packs · Recent activity).

**Release card states** — every card is one of:

| State          | Icon tile           | Gate badge       | Body sections rendered                                 |
|---|---|---|---|
| In progress    | gate-colour tile    | Go / Cond / No-Go| identity • gate stats • pass/fail bar • phase pipeline • blocker rows |
| Planning       | blue clock tile     | Not evaluated    | identity • phase pipeline (all idle) • single dashed empty bar with 2 CTAs |
| Released       | green-tick tile     | Healthy          | identity • gate stats (snapshot at release) — no progress bar, no blockers |
| Cancelled      | muted tile          | Cancelled        | identity only, muted |

**Gate badge thresholds** (drive both badge colour and the per-metric colour):

| Metric     | Healthy   | Warn         | Fail         | Token (badge)            |
|---|---|---|---|---|
| Composite  | ≥ 95%     | 90 – 94.9%   | < 90%        | `--gate-go` / `--gate-conditional` / `--gate-no-go` |
| Coverage   | ≥ 80%     | 70 – 79.9%   | < 70%        | same                                                  |
| Flake rate | ≤ 1.5%    | 1.5 – 3.0%   | > 3.0%       | same (inverted)                                       |

Final badge = worst of the three. Conditional renders when at least one metric is in Warn and none in Fail. Show the threshold inline in each `.gs` cell ("need ≥ 95%" / "cap 1.5%") so the number always defends itself.

**Components on this screen**

| Component                       | Position                          | See |
|---|---|---|
| Page header                     | top, full-width                    | §5.1 |
| Verdict band                    | top strip, left                    | §5.2 |
| KPI card                        | top strip, right (×4)              | §5.3 |
| Filter bar (segmented + chips)  | between strip and body             | §5.4 |
| Release card                    | left column, repeating             | §5.5 |
| Phase pipeline chips            | inside release card                | §5.6 |
| Blocker row                     | inside release card                | §5.7 |
| Shipping-this-week panel        | right rail                         | §5.8 |
| Aging signals panel             | right rail                         | §5.9 |
| Compliance packs panel          | right rail                         | §5.10 |
| Recent activity feed            | right rail                         | §5.11 |

---

## 5. Components (selected)

### 5.2 Verdict band
- `1px` border `var(--color-border)`, gradient bg `linear-gradient(180deg, rgba(34,197,94,0.05), transparent 70%)` over `var(--color-bg-card)`.
- `3px` left accent — colour matches the gate decision of the highlighted release (Go = `--gate-go`, Conditional = `--gate-conditional`, No-Go = `--gate-no-go`).
- Top row: small uppercase label + headline (20px / 700 / `-0.015em`) + a 11px pill (Conditional Go, etc.).
- Lede paragraph: `13px`, `var(--color-text-secondary)`, max 50ch, with one `<em>` per material number.
- Up to 3 bulleted items with a small leading icon (12×12). Each item names the **what · where · who · when**.

### 5.3 KPI card
- Border + card bg + `var(--radius-lg)`. Padding `12px 14px 10px`.
- **Layout:** outer `flex column`. First child is a `flex row` with `align-items: flex-start` and `justify-content: space-between`: left holds label + value, right holds the sparkline as a flex sibling (`flex: none`, `align-self: flex-end`). The sub-line ("from last week" / "avg gate 97.1%") is the second child of the column.
- Value: 26px / 700 / tabular-nums / `-0.02em`. Colour tinted to status (green/amber/red) when the card is signalling.
- **Never place the sparkline `position:absolute` over the value** — that's the bug the previous version had. Always flex.

### 5.5 Release card
- Outer: `var(--color-bg-card)` + `1px` border, radius `var(--radius-lg)`. Hover lifts the border to `--color-border-light`.
- Header: `grid minmax(0,1fr) auto`, padding `14px 18px 12px`. Left = identity (32px gate-tinted icon tile + name + version chip + stage badge + meta row). Right = gate cluster.
- Gate cluster: a row of 3 vertical `.gs` cells (Composite · Coverage · Flake), each `9.5px` label + `16px` value + `10.5px` sub-line ("need ≥ 95%" / "cap 1.5%" / "+3% wk"), followed by the gate badge pill.
- Progress bar: 4-segment `pbar` (passed → failed → flaky → skipped). Heights `6px`, radius `999px`. Totals legend sits to the right with coloured 7px dots.
- Phase chips row: six equal-width chips, single shared border, `7px 11px` padding, dot+name. States: idle / done (green tint) / active (teal + halo on dot) / fail (red tint) / skip (faint).
- Blocker rows: warn or red coloured rounded boxes. Icon tile · body text with `<code>` for IDs · trailing `Open ticket →` link in `--color-accent`. Stack with `6px` gap when multiple.

### 5.6 Phase pipeline chips
- Six predefined phases per project (configurable globally in `/settings/release-phases`): `Smoke · Regression · E2E suites · Perf · UAT sign-off · Sign & ship`.
- Active chip uses a teal halo (`box-shadow: 0 0 0 3px rgba(20,184,166,0.18)` on the dot).
- Failed chip shows the metric inline ("Perf · –6%") rather than a generic "Failed".
- Skip chip (only after the last completed step) is fully muted.

### 5.7 Blocker row
- `padding: 8px 12px`, radius `var(--radius-md)`.
- Warn variant: amber border `rgba(234,179,8,0.22)` + gradient bg.
- Red variant: red border `rgba(239,68,68,0.28)` + gradient bg.
- Resolved (used in Ready-to-ship release): green border + gradient bg with check icon.
- Pattern: `<icon><body><action>` — body names the failure mode, the system or component, the linked ticket, the owner, and the ETA. The action is one verb ("Open ticket →" / "Auto-quarantine →" / "Ping reviewer →" / "Escalate →").

### 5.8 Shipping-this-week panel
- Each row: a 42px date tile (day + month-or-relative-label like "Today"/"Fri") + body + status dot.
- Body must use `min-width: 0; overflow: hidden` so long titles ellipsis-truncate. The previous version overflowed the panel here.

### 5.9 Aging signals panel
- One row per stale release. `body` is `min-width:0; overflow:hidden`, title `white-space:nowrap; text-overflow:ellipsis`, description wraps. Age pill on the right (`flex:none`).
- Coloured by severity (`.ag.warn` / `.ag.red`). Surface aging starting at: blocked > 3 days, Planning with no test plan > 7 days, UAT pending > 2 days, phase stalled > 3 days.

### 5.10 Compliance packs panel
- Each row shows `version · MB · sha256 prefix · signed date` and a single trailing action (Download / Generate). Body `min-width:0; overflow:hidden`. Long sha256 lines ellipsis-truncate.
- Pending (no pack yet) renders an amber clock tile and a "Generate" link; "Generate" disabled until the gate is at least Conditional.

---

## 6. Design tokens

All values are CSS custom properties declared in `tokens.css`. Key tokens used on this screen:

- Surfaces: `--color-bg`, `--color-bg-card`, `--color-bg-secondary`, `--color-bg-hover`, `--color-bg-input`
- Borders: `--color-border`, `--color-border-light`
- Text: `--color-text`, `--color-text-secondary`, `--color-text-muted`, `--color-text-faint`
- Brand: `--brand-400` `#2dd4bf`, `--brand-500` `#14b8a6`, `--brand-600` `#0d9488`
- Accent: `--color-accent` `#4493f8`
- **Gate** (used everywhere on this screen): `--gate-go` `#22c55e`, `--gate-conditional` `#eab308`, `--gate-no-go` `#ef4444`
- Status: `--status-passed` `#34d399`, `--status-failed` `#fca5a5`, `--status-broken` `#fdba74`, `--status-skipped` `#fcd34d`, `--status-flaky` `#d8b4fe`
- Radius: `--radius-sm` `6px` (segmented buttons), `--radius-md` `8px` (chips/inputs), `--radius-lg` `12px` (cards, panels), `--radius-pill` `9999px`

---

## 7. Data model

```ts
type GateDecision = 'go' | 'conditional' | 'no_go' | 'not_evaluated' | 'cancelled';
type ReleaseStage = 'planning' | 'in_progress' | 'released' | 'cancelled';
type PhaseState  = 'idle' | 'done' | 'active' | 'failed' | 'skipped';

interface ReleasesPage {
  project: { id: string; name: string };
  verdict: VerdictBanner;
  kpis: KpiCard[];
  filters: { stage: ReleaseStage | 'all'; gate?: GateDecision; ownerId?: string; due?: 'overdue'|'week'|'month' };
  releases: Release[];
  shipping: ShippingItem[];
  aging: AgingSignal[];
  compliancePacks: CompliancePack[];
  activity: ActivityEntry[];
}

interface Release {
  id: string;
  name: string;
  version: string;                            // semver / tag
  stage: ReleaseStage;
  gate: { decision: GateDecision; composite?: number; coverage?: number; flakePct?: number; updatedAt: string };
  owner: { id: string; initials: string; name: string };
  coOwnerCount?: number;
  dueAt?: string;                             // ISO
  totals?: { passed: number; failed: number; flaky: number; skipped: number };
  phases: Array<{ key: 'smoke'|'regression'|'e2e'|'perf'|'uat'|'sign'; label: string; state: PhaseState; note?: string }>;
  blockers: Blocker[];
  fieldDefects24h?: number;                   // released only
  sloPostDeploy?: number;                     // released only
}

interface Blocker {
  id: string;
  severity: 'warn' | 'red' | 'resolved';
  title: string;                              // "Perf regression –6%"
  context: string;                            // "search-suggest p95"
  linkedTicketId?: string;                    // "SS-2841"
  ownerInitials?: string;
  etaAt?: string;                             // ISO
  action: { label: string; href: string };
}

interface VerdictBanner {
  decision: GateDecision;
  headline: string;
  pill: string;                               // e.g. "Conditional Go"
  lede: string;                               // 50ch max, HTML allowed for <em>
  items: Array<{ tone: 'warn'|'go'|'red'; html: string }>;
}

interface KpiCard {
  label: string;
  value: number | string;
  tone?: 'good' | 'warn' | 'bad' | 'neutral';
  delta?: { dir: 'up'|'down'; value: number; period: string };
  sub?: string;                               // HTML allowed
  sparkline?: number[];                       // 8–10 points; render flex-sibling, not absolute
}
```

---

## 8. Interaction notes

1. **Filter tabs are router-driven.** `?stage=planning` selects Planning. Counts are read from `kpis` + `releases` (no client-side filtering — server returns the filtered list).
2. **Search** uses `/` shortcut globally; pressing `/` focuses the input. Matches name, version, tag, and owner display name.
3. **Hover on a gate metric value** opens a tooltip with the cap, current value, and the run id contributing the worst result.
4. **Phase chip click** scrolls the body to the matching phase in the per-release detail page (`/releases/:id#phase-perf`).
5. **Blocker action** is a single button — it's the action the user is most likely to take; everything else lives in the ticket/release detail.
6. **Calendar view toggle** swaps the body list for a month grid (out of scope for this handoff — gate behind a `ff_releases_calendar` flag).
7. **New release** opens a modal: name, semver, target date, owner, optional "Clone from … " pre-fill. Posting creates a Planning release and lands you on `/releases/:id`.
8. **Generate from PRD** (Planning empty state) opens an upload modal that runs an AI pass to draft phases + test plan and lands you on a diff view to accept/reject.

---

## 9. Empty / loading / error states

- **No releases at all** (workspace just created): replace the list with a single full-width card: "No releases yet — create your first or clone from an org template" + the two CTAs.
- **No releases matching filter** (filter chips applied): replace the list with a compact bar — "No releases match these filters" + clear-filters action. Keep the right rail visible.
- **Loading**: skeletonise each release card row by row (icon tile, name, gate cluster, phase chips). Right-rail panels each get 4 skeleton rows.
- **Failed to load a release** (one card errored, others succeeded): card renders with a red border, the body replaced by a one-line error and a Retry button. Do **not** swallow it server-side — show the user.

---

## 10. Accessibility

- Status tabs are a real ARIA tablist; arrow keys move between tabs, Enter activates.
- Gate badges have an `aria-label` that includes the explanation ("Conditional Go — flake rate above cap").
- Sparklines carry an `aria-label` ("trend: 8-week in-progress count, ending 6").
- Don't rely on colour alone for the phase pipeline — the dot + colour + textual state (`Active`/`Done`/`Failed`) all carry the signal.
- All interactive cards are buttons or links; focus rings use `--color-ring` and are never suppressed.

---

## 11. Open questions

1. **Cancelled releases** — currently shown in the All count but no card variant is specified beyond "muted tile". Confirm we want them surfaced in the portfolio or filtered out by default.
2. **Multi-project workspaces** — the current header reads "Manage releases … for GoogleSearch" assuming one project. For multi-project workspaces, do we want a project switcher in the header, or per-project sub-tabs?
3. **Aging thresholds** — confirm the per-stage aging caps in §5.9 with QA Ops; they're our defaults but the values are policy.
4. **Gate policy editor** — currently the cap is shown but not editable on this screen. Is there a per-release override (e.g. relax composite to 92% with VP approval), and if so does it live here or in `/releases/:id/gate`?

---

## 12. Files in this bundle

- `README.md` — this file
- `screens/releases.html` — the redesigned page
- `tokens.css` — copy of `colors_and_type.css` (foundation tokens)
