# Handoff: Run Intelligence (redesign)

> **Status:** Design ready for implementation
> **Fidelity:** Hi-fi
> **Owner:** Design — TestLookup
> **Target codebase:** `frontend/` (React + TypeScript + Tailwind, Vite)
> **Last updated:** 2026-05-10

---

## 1. Overview

Run Intelligence is the per-build summary page in TestLookup. It shows a single CI run's release-readiness verdict, the AI pipeline that produced it, the test outcome, the specific failures, and the recommended actions by role.

The current page has structural problems: the verdict appears four times, two values disagree (24 vs 25), the "Workflow Progress" grid duplicates the "Pipeline Stages" sidebar list, three placeholder cards are empty, and the actual failure detail is never shown. The redesign collapses the duplication into a single verdict-led spine and surfaces the missing failure detail.

Ship release note: *"Run Intelligence is now verdict-first — the gate decision, blockers, and the failing test are all above the fold, with one clean pipeline ribbon and an honest AI-confidence readout."*

**Goals**
- One unambiguous verdict, one risk number, one set of CTAs.
- The actual failure (test name, file path, owner, error) visible without scrolling on a 1440px viewport.
- AI confidence and provenance readable at a glance, not buried.
- Pipeline progress shown once, not twice.

**Non-goals**
- Comparison view (lives at `/runs/compare`)
- Test-cases drill-in (lives at `/runs/:id`)
- Release-gate config (lives at `/release-gate/:id`)

---

## 2. About the design files

The files in this bundle are **design references created in HTML** — a prototype showing intended look and behavior. They are **not production code to copy directly**.

The target codebase is React + TypeScript + Tailwind (existing `frontend/` app, see `frontend/CLAUDE.md` and `frontend/tailwind.config.js`). Recreate the design using the existing component library (`MetricCard`, `StatusBadge`, `PageHeader`, `EmptyState`, etc. in `frontend/src/components/ui/`) and CSS-variable token system (`frontend/src/index.css`).

Treat the HTML as the source of truth for **visual design and intended behavior**, not for **markup, class names, or DOM structure**.

---

## 3. Fidelity — Hi-fi

Pixel-perfect. Match every value listed in §8 Design Tokens. The reference HTML uses the same CSS variables (`--color-bg`, `--color-text`, `--gate-conditional`, etc.) that already exist in the codebase, so most colors will match by token name without translation.

---

## 4. Screens / views

### 4.1 Run Intelligence page
- **File:** `screens/run-intelligence.html`
- **Route:** `/runs/:runId/intelligence`
- **Purpose:** Give a release manager (or developer or QA) the gate decision, the failure that drives it, and the next action — without reading the full test list.
- **Entered from:** Builds list, dashboard "What's blocking release" panel, notifications.
- **Exits to:** `/runs/:runId` (test cases), `/runs/compare?left=:id`, `/release-gate/:id`, decision-trail modal, file-defect flow.

**Layout (1440px target, ≥1280px supported)**
- Outer: max-width `1320px`, padding `24px 28px 80px`, vertically stacked sections with `14px` gap between sections.
- Header row: title + crumb (left), persona tabs + action buttons (right). Wraps on narrow screens.
- **Verdict block:** 2-column (`1.4fr 1fr`), gap `24px`. Left: gate name + lede + blocker line + CTAs. Right: composite risk meter + dimension grid (2×2).
- **Pipeline ribbon:** full-width card. 9 equal columns separated by 1px borders, no gaps.
- **Body grid:** 2-column (`1.6fr 1fr`), gap `14px`.
  - Left col (top→bottom): Test outcome → What failed → Recommended actions
  - Right col: AI confidence → Failure category → Provenance footer

**Sticky regions**
- None on this page. Header scrolls with content. (The original page had a sticky persona/action bar — drop it; the verdict needs to win the top of the viewport.)

**Components on this screen**

| Component | Position | See |
|---|---|---|
| Page header (title + crumb + actions) | top, full-width | §5.1 |
| Persona tabs | header right | §5.2 |
| Verdict card | below header | §5.3 |
| Risk meter + dimension grid | inside verdict (right) | §5.4 |
| Pipeline ribbon | below verdict | §5.5 |
| Test outcome strip | left col, top | §5.6 |
| Failure detail card | left col, middle | §5.7 |
| Recommended actions card | left col, bottom | §5.8 |
| AI confidence card | right col, top | §5.9 |
| Failure category card | right col, middle | §5.10 |
| Provenance footer | right col, bottom | §5.11 |

**Empty / loading / error states**
- **Run still analyzing:** Pipeline ribbon shows partial state (current stage with running spinner, downstream stages dimmed). Verdict shows "Analysis in progress" with neutral styling, no risk score yet.
- **Pipeline failed:** Replace verdict body with error card; show which stage failed in the ribbon (red dot, error icon). Keep test outcome (we have it) and What failed (we have it). Recommended actions become "Retry analysis" only.
- **No failures (all green):** Verdict shows GO (success-green treatment, see §5.3 GO variant). Drop the "What failed" card entirely; replace with "All checks passed" empty state. Failure category card collapses to a single passed line.
- **No baseline:** Reproducibility dimension shows `—` instead of a number, bar at 0%. AI confidence card includes "No baseline run for comparison" gap chip.
- **LLM unavailable:** Confidence ≤ 30%; lede includes "Recommendation derived deterministically — LLM reasoning was unavailable for this run."

**Responsive behavior**
- ≥1280px: layout as designed (2-col body, 9-col ribbon).
- 1024–1279px: body grid collapses to 1 column (right col stacks under left). Ribbon stays 9-col.
- 768–1023px: ribbon collapses to 3-col grid (3 rows of 3 stages); stat strip collapses to 2-col grid.
- <768px: not a target for v1. Show "View on a wider screen" message or single-column scroll fallback.

---

## 5. Components

### 5.1 Page header
- **Anatomy:** Title (`Run Intelligence`, `text-2xl/700`), crumb row (`Build` + monospace build id chip + `·` + branch chip + `·` + completion timestamp), trailing action buttons.
- **Action buttons (left to right):** Persona tabs · Refresh · PDF · Evidence · Compare · **View Test Cases** (primary).
- **All buttons except "View Test Cases":** secondary style — `padding: 6px 11px`, `border: 1px solid var(--color-border)`, `border-radius: 8px`, text color `var(--color-text-muted)`, hover `border-color: var(--color-border-light); color: var(--color-text)`.
- **Primary button:** `background: var(--color-btn-primary-bg)` (#238636), white text, weight 500.
- Code chips inside the crumb: `background: var(--color-bg-secondary)`, `border: 1px solid var(--color-border)`, `padding: 1px 6px`, `border-radius: 4px`, font `JetBrains Mono` 11.5px.

### 5.2 Persona tabs (segmented control)
- **Variants:** Executive (default selected) · Developer · Manager.
- **Container:** `background: var(--color-bg-secondary)`, `border: 1px solid var(--color-border)`, `border-radius: 8px`, `padding: 3px`. Children butted with no gap.
- **Tab:** `padding: 5px 12px`, `border-radius: 4px`, font 14px/500.
- **Selected:** `background: var(--color-bg-card)`, `color: var(--color-text)`, `box-shadow: var(--shadow-sm)`.
- **Unselected:** transparent background, `color: var(--color-text-muted)`. Hover lifts to `var(--color-text)`.
- **Behavior:** Switching persona re-orders the right rail and may swap the "Recommended actions" emphasis. Persona is persisted to localStorage as `tl.runIntel.persona`.

### 5.3 Verdict card
- **Container:** `background: radial-gradient(120% 100% at 0% 0%, rgba(245,158,11,0.10), transparent 55%), var(--color-bg-card)`. Border `1px solid rgba(245,158,11,0.4)`. `border-radius: 12px`. `padding: 18px 20px`. Left edge has a 3px-wide colored bar matching the gate.
- **Three gate variants:**

  | Gate | Border color | Bar color | Tag color | h2 color |
  |---|---|---|---|---|
  | GO | `rgba(34,197,94,0.4)` | `var(--gate-go)` (#22c55e) | `#86efac` | `#86efac` |
  | CONDITIONAL | `rgba(245,158,11,0.4)` | `var(--gate-conditional)` (#eab308) | `#fcd34d` | `#fcd34d` |
  | NO-GO | `rgba(239,68,68,0.4)` | `var(--gate-no-go)` (#ef4444) | `#fca5a5` | `#fca5a5` |

- **Header tag:** uppercase `Release readiness` with a 6px pulsing dot (`@keyframes p` — see HTML, 1.6s ease-out infinite). Dot color matches gate.
- **h2:** `font-size: 28px`, `line-height: 1.1`, `letter-spacing: -0.02em`, `font-weight: 700`. Format: `<Gate name> · <one-line action>` — e.g. "Conditional Go · proceed with mitigation".
- **Lede:** `var(--color-text-secondary)`, `text-sm`, `max-width: 60ch`. Plain prose explaining *why* this gate. Mention LLM-unavailable explicitly when relevant.
- **Blocker line:** Single horizontal row inside the card. `padding: 10px 12px`, `background: rgba(239,68,68,0.10)`, `border: 1px solid rgba(239,68,68,0.3)`, `border-radius: 8px`. Layout: red-x icon · text · spacer · suite chip on the right. Format: `<N> blocking issue(s) · <reason>`.
- **CTA row:** `gap: 8px`, wraps. Primary CTA matches gate severity (amber `Hold release` for conditional, green `Approve and ship` for go, red `Block release` for no-go). Secondary: `Override gate`, `Approve with conditions` (conditional only).

### 5.4 Risk meter + dimension grid (right side of verdict)
- **Composite meter:**
  - "Composite risk score" label (`uppercase 10px text-muted`).
  - Big number (`44px/700`, tabular-nums), color matches gate, `/ 100` suffix in muted.
  - Gate pill on the right (`Conditional`/`Go`/`No-go`).
  - Bar: 6px tall, full width, `border-radius: 999px`, gradient `linear-gradient(90deg, #22c55e, #fcd34d 50%, #ef4444)`. The fill width = score (e.g. 24% for 24/100).
  - Two divider ticks at 33% and 66% mark the conditional/no-go thresholds.
  - Scale labels under: `Safe · 0` · `Conditional · 30` · `Block · 70` · `100`.
- **Dimension grid (2×2):**
  - Each tile: `background: rgba(255,255,255,0.025)`, `border: 1px solid var(--color-border)`, `border-radius: 4px`, `padding: 8px 10px`.
  - Top row: dimension name (uppercase 10.5px) + weight (e.g. `25%`).
  - Bottom row: numeric score (14px/600, tabular-nums) + horizontal bar (4px tall).
  - Severity coloring:
    - Score ≥ 70 → `bad` (red `#fca5a5` text, `#ef4444` bar)
    - Score 30–69 → `warn` (amber `#fcd34d` / `#f59e0b`)
    - Score < 30 → `good` (emerald `#34d399` / `#22c55e`)
    - Score `—` (unknown) → `neutral` (muted text, no fill)
  - Dimensions in order: User impact (25%), Regression (20%), Blast radius (15%), Reproducibility (15%). Remaining 25% weight is implicit (other deterministic checks).

### 5.5 Pipeline ribbon
- **Card:** standard card chrome, `padding: 16px 18px 18px`.
- **Header row:** Title `AI pipeline · Deep Analysis` (14px/600) and one-line meta `● Completed · 9 stages · 8 done · 1 skipped · 10m 40s total` (12px muted, dot in `var(--status-passed)`). Right side: small confidence chip (`Confidence 22%` in red treatment when low) + `· 0 evidence items · 0 tools`.
- **Stages:** 9 columns (`grid-template-columns: repeat(9, minmax(0, 1fr))`), no gap, divided by 1px right borders.
- **Each stage:** `padding: 10px 8px`, hover `background: var(--color-bg-hover)`, cursor pointer.
  - Top row: 2-digit number (`01..09`, 10px faint, tabular-nums) + 14px circular status icon.
  - Name: 12px/600.
  - Duration: 10.5px muted, tabular-nums (`0s`, `1m 10s`, `8m 0s`).
  - Bottom 2px track: full color = done, 0% = skipped, partial color = in-progress.
- **Status icons:**
  - Done: `background: rgba(34,197,94,0.18)`, `color: #34d399`, checkmark glyph.
  - Skipped: `background: var(--color-bg-secondary)`, dashed border, em-dash glyph. Whole stage at 0.55 opacity. Substring `Skipped` in 9.5px uppercase under the name.
  - Warn: `background: rgba(245,158,11,0.18)`, `color: #fcd34d`, alert glyph (used for the final stage when the verdict is conditional and the release-risk stage flagged it).
  - Running: not in this snapshot — show pulsing accent dot when implementing.
- **Click stage:** opens a side drawer with the stage's logs, evidence, and timing breakdown. (Drawer not in this scope — stub the click handler.)

### 5.6 Test outcome strip
- **Card header:** title `Test outcome`. Right: `Affected suite` label + monospace suite pill (`payments`, `var(--color-accent)` text, `rgba(68,147,248,0.12)` bg, `rgba(68,147,248,0.25)` border). Multiple suites = comma-separated chips, max 3 then `+N more`.
- **Stat row:** 5 equal columns separated by 1px right borders. Each stat: `padding: 14px 16px`, vertical stack of label (10.5px uppercase muted), number (24px/700, tabular-nums), tiny meta (10.5px muted).
  - Stats in order: Pass rate (warn-color when <100%), Total tests, Failed (bad-color when >0), Skipped, Anomalies.
- **Distribution row:** `padding: 0 16px 12px`. Label row above (uppercase 11px) showing breakdown. Stack bar 8px tall, `border-radius: 999px`, segments use `flex` proportional to counts (e.g. 8 passed = `flex: 8`, 1 failed = `flex: 1`). Legend below with 9px color squares.

### 5.7 Failure detail card ("What failed")
- **Card body:** `border-left: 3px solid #ef4444`. Background `linear-gradient(90deg, rgba(239,68,68,0.06), transparent 30%), var(--color-bg-card)`.
- **Top row:** Test name (monospace 13px, format `<suite> › <test name>`) on the left; category pill on the right (`Product bug` red, `Flaky` purple, `Infra` orange).
- **Meta row:** `font-size: 12px`, muted. Pipe-separated: file path · `Owner <name>` · run history · baseline state.
- **Error block:** `background: var(--color-bg)`, `border: 1px solid var(--color-border)`, `border-left: 2px solid rgba(239,68,68,0.55)`, `border-radius: 4px`, `padding: 10px 12px`, `font-family: JetBrains Mono 12px`, `white-space: pre-wrap`. Show the assertion line + first 2–3 stack frames; truncate with "Show full stack →" if longer.
- **Action row:** `Open test case` · `View logs` · `Decision trail` · `File defect`. Standard secondary buttons.
- **Multiple failures:** repeat the failure block stacked, with `+ N more failures` collapsing footer that expands inline.

### 5.8 Recommended actions card (role-routed)
- **Card header:** title `Recommended actions`. Right meta: `Routed by role`.
- **Each role row:** 3-col grid (`22px 1fr auto`), `padding: 10px 12px`, `background: var(--color-bg)`, `border: 1px solid var(--color-border)`, `border-radius: 8px`, gap `10px`.
  - Icon disc: 22×22 circle, role-tinted (QA blue, Developer purple, SRE amber, RM green).
  - Body: role name (10.5px uppercase) optionally followed by an owner chip (`@R. Mehta`, monospace 11px in `var(--color-bg-secondary)` pill). Below: copy in `text-secondary`, 13px, `line-height: 1.45`.
  - Trailing: Copy button (faint, hover muted).
- **De-prioritized roles:** if a role's score doesn't apply (e.g. SRE when blast-radius < 20), render the row at `opacity: 0.65` with a parenthetical `— low priority for this run` after the role name.
- **Order:** Developer (owner) → QA → Release Manager → SRE. Persona tab can re-emphasize: Executive view shows RM first, Developer view shows Dev first, Manager view keeps as designed.

### 5.9 AI confidence card
- **Card body:**
  - Top line: big percent (22px/700, color matches confidence band — red <30, amber 30–69, green ≥70) + descriptive label.
  - 4px bar showing percent. Gradient `linear-gradient(90deg, #ef4444, #f97316)` for low confidence, single accent color for med/high.
  - Why paragraph (12.5px, line-height 1.5).
  - **Gaps list:** stacked items, each `padding: 7px 10px`, `background: var(--color-bg)`, `border: 1px solid var(--color-border)`, `border-radius: 4px`. Red x-circle icon + reason.
  - **Checks list:** small green pill chips (`background: rgba(34,197,94,0.10)`, `color: #34d399`, `border: 1px solid rgba(34,197,94,0.25)`).

### 5.10 Failure category card
- **Card header:** title `Failure category`. Right: `<N> total`.
- **Body:** stacked rows. Active categories use a colored row (red bg/border for product bug at 100%); zero categories are dashed-border, `opacity: 0.6`, with copy `No <category> detected this run`.
- **Categories shown:** Product bug, Flaky, Infra. Future: Environment, Test bug.

### 5.11 Provenance footer
- **Inline strip:** `padding: 10px 14px`, dashed border, muted text. Left: `Provenance · pipeline v2 · 0 evidence · 0 tools`. Right: `Decision trail →` link in `var(--color-accent)`.

---

## 6. Interactions & behavior

### Navigation
- `View Test Cases` → `/runs/:runId` (existing route).
- `Compare` → `/runs/compare?left=:runId` (existing).
- `Decision Trail` button (header) → opens a modal overlay with the AI decision audit log. Provenance footer link goes to the same modal.
- Persona tabs → re-render right rail and `Recommended actions` order. URL stays the same; persona persisted in localStorage.
- Stage in pipeline ribbon → opens right-side drawer (out of scope — stub).
- Failure card `Open test case` → `/runs/:runId/cases/:caseId`.
- Failure card `File defect` → POST to defect tracker integration; show toast with link.

### Animations / transitions

| Element | Trigger | Property | Duration | Easing |
|---|---|---|---|---|
| Verdict gate dot | mount | box-shadow halo | 1.6s loop | `ease-out` |
| Card hover | hover | border-color, background | 150ms | `ease-out` |
| Stage hover | hover | background | 120ms | `ease-out` |
| Persona tab switch | click | shadow + bg cross-fade | 150ms | `ease-out` |
| Drawer enter (stage detail) | click | transform translateX(100%→0), opacity | 220ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| Toast | action | opacity + translateY(8px→0) | 180ms | `ease-out` |

Respect `prefers-reduced-motion`: disable the verdict dot pulse and replace drawer/toast slides with opacity-only fades.

### Optimistic UI
- `Hold release` / `Override gate` / `Approve with conditions`: show the new state immediately, reconcile on response, rollback with toast on error.
- Persona switch: instant local state, no server roundtrip.

---

## 7. State management

**URL state**
- Route param `runId`. No query params owned by this page (compare-link writes its own).

**Local component state**
- `persona: 'executive' | 'developer' | 'manager'` (persisted localStorage `tl.runIntel.persona`)
- `expandedFailureIds: Set<string>` for the "Show full stack" toggle
- `categoryFilter: string | null` if user clicks a category row to filter the failure list

**Server data**

| Endpoint | Method | Trigger | Used by | Cache key |
|---|---|---|---|---|
| `GET /api/runs/:id/intelligence` | GET | mount | whole page | `['run', runId, 'intelligence']` |
| `GET /api/runs/:id/failures` | GET | mount | §5.7, §5.10 | `['run', runId, 'failures']` |
| `POST /api/runs/:id/release-decision` | POST | CTA click | §5.3 | invalidates `['run', runId, 'intelligence']` |
| `POST /api/runs/:id/refresh-analysis` | POST | Refresh button | whole page | invalidates `['run', runId, *]` |

**Real-time**
- If the run is still analyzing, subscribe to `SSE /api/runs/:id/events` to update the pipeline ribbon and verdict in place. Close stream when status becomes terminal (completed/failed).

---

## 8. Design tokens

The reference HTML loads `tokens.css` (bundled — same file as `frontend/src/index.css` in the codebase). Use the **existing CSS variables** wherever possible. The values listed below are what the design *uses* — only the marked-novel ones need to be added.

### Colors (existing tokens)

| Token | Value | Usage in this design |
|---|---|---|
| `--color-bg` | `#0d1117` | page bg |
| `--color-bg-secondary` | `#161b22` | code chips, segment container, stat-row bg variants |
| `--color-bg-card` | `#151b23` | all cards |
| `--color-bg-hover` | `#1c2128` | stage hover |
| `--color-border` | `#3d444d` | all card and divider borders |
| `--color-border-light` | `#4a525c` | hover border on secondary buttons |
| `--color-text` | `#f0f6fc` | h1, h2, primary copy |
| `--color-text-secondary` | `#c9d1d9` | lede, role action body |
| `--color-text-muted` | `#9198a1` | meta text, labels |
| `--color-text-faint` | `#656d76` | stage numbers, tertiary meta |
| `--color-accent` | `#4493f8` | suite chip, links |
| `--color-btn-primary-bg` | `#238636` | "View Test Cases" |
| `--color-btn-primary-hover` | `#2ea043` | hover |
| `--gate-go` | `#22c55e` | go gate bar/dot |
| `--gate-conditional` | `#eab308` | conditional gate bar/dot |
| `--gate-no-go` | `#ef4444` | no-go gate bar/dot |
| `--status-passed` | `#34d399` | pipeline done icon, distribution-bar pass segment |
| `--status-failed` | `#fca5a5` | failure pill text |

### Colors (used inline in this design — consider adding as tokens)

| Color | Usage | Suggested name |
|---|---|---|
| `rgba(245,158,11,0.10)` | conditional verdict glow | `--gate-conditional-glow` |
| `rgba(239,68,68,0.10)` | blocker line bg | `--alert-bg-soft` |
| `rgba(239,68,68,0.30)` | blocker line border | `--alert-border-soft` |
| `rgba(34,197,94,0.18)` | done-stage icon bg | `--status-passed-soft` |
| `linear-gradient(90deg, #22c55e, #fcd34d 50%, #ef4444)` | risk meter fill | `--gradient-risk` |

### Typography (existing)
Family — sans: `Inter, system-ui, ...`; mono: `JetBrains Mono`.

| Token (existing) | Size | Weight | Usage |
|---|---|---|---|
| `--text-2xl` | 24px | 700 | page h1 |
| `--text-xl` | 20px | 700 | (not used) |
| `--text-lg` | 18px | 600 | (not used) |
| `--text-base` | 16px | 400 | body default |
| `--text-sm` | 14px | 400 / 500 / 600 | most card content |
| Inline `28px/700/-0.02em` | — | — | verdict h2 — **add as `--text-display-sm`** |
| Inline `44px/700/-0.02em/tabular` | — | — | risk score — **add as `--text-display-md`** |

Tabular numerals (`font-variant-numeric: tabular-nums`) on every numeric value: stat numbers, risk score, dimension scores, durations.

### Spacing
Standard 4-step ramp: 4 · 8 · 12 · 14 · 16 · 18 · 20 · 24 · 28 · 32 px. The `14px` between sections is intentional — slightly tighter than `gap-4` Tailwind default.

### Radii

| Use | Value |
|---|---|
| code chips | 4px |
| dimension tiles | 4px |
| error block | 4px |
| buttons | 8px |
| cards | 12px |
| pulse dot, pills, bars | 999px |

### Shadows
Cards: no shadow (border-only). Selected persona tab: `var(--shadow-sm)` (existing token). Drawer: `var(--shadow-lg)` (existing).

### Motion
See §6. Use existing `--motion-base` (200ms `cubic-bezier(0.16, 1, 0.3, 1)`) where defined; the verdict pulse is custom and lives in component CSS.

---

## 9. Assets

| Asset | Path in handoff | Format | Source / license |
|---|---|---|---|
| Icons | inline SVG in component | SVG | Lucide (MIT) — already a dependency in `frontend/package.json` |
| Fonts | Google Fonts CDN | woff2 | Inter (OFL), JetBrains Mono (OFL) — match the existing app |
| Logo | not used on this page | — | — |

> Use the existing `lucide-react` imports rather than copying SVGs into JSX.

---

## 10. Copy & content

| Key | English |
|---|---|
| `runIntel.title` | Run Intelligence |
| `runIntel.crumb.build` | Build |
| `runIntel.crumb.branch` | Branch |
| `runIntel.action.refresh` | Refresh |
| `runIntel.action.pdf` | PDF |
| `runIntel.action.evidence` | Evidence |
| `runIntel.action.compare` | Compare |
| `runIntel.action.viewCases` | View Test Cases |
| `runIntel.persona.executive` | Executive |
| `runIntel.persona.developer` | Developer |
| `runIntel.persona.manager` | Manager |
| `runIntel.verdict.tag` | Release readiness |
| `runIntel.verdict.h2.go` | Go · ship it |
| `runIntel.verdict.h2.conditional` | Conditional Go · proceed with mitigation |
| `runIntel.verdict.h2.noGo` | No-Go · block release |
| `runIntel.verdict.lede.llmUnavailable` | Recommendation derived deterministically — LLM reasoning was unavailable for this run. |
| `runIntel.blocker.template` | {n, plural, one {1 blocking issue} other {{n} blocking issues}} · {reason} |
| `runIntel.cta.hold` | Hold release |
| `runIntel.cta.override` | Override gate |
| `runIntel.cta.approveConditions` | Approve with conditions |
| `runIntel.cta.shipIt` | Approve and ship |
| `runIntel.cta.block` | Block release |
| `runIntel.meter.label` | Composite risk score |
| `runIntel.meter.scale.safe` | Safe · 0 |
| `runIntel.meter.scale.cond` | Conditional · 30 |
| `runIntel.meter.scale.block` | Block · 70 |
| `runIntel.dim.userImpact` | User impact |
| `runIntel.dim.regression` | Regression |
| `runIntel.dim.blastRadius` | Blast radius |
| `runIntel.dim.reproducibility` | Reproducibility |
| `runIntel.pipeline.title` | AI pipeline · Deep Analysis |
| `runIntel.pipeline.metaTemplate` | {status} · {total} stages · {done} done · {skipped, plural, =0 {} one {1 skipped · } other {{skipped} skipped · }}{duration} total |
| `runIntel.outcome.title` | Test outcome |
| `runIntel.outcome.affectedSuite` | Affected suite |
| `runIntel.outcome.distribution` | Result distribution |
| `runIntel.outcome.stat.passRate` | Pass rate |
| `runIntel.outcome.stat.total` | Total tests |
| `runIntel.outcome.stat.failed` | Failed |
| `runIntel.outcome.stat.skipped` | Skipped |
| `runIntel.outcome.stat.anomalies` | Anomalies |
| `runIntel.failure.title` | What failed |
| `runIntel.failure.shownTemplate` | {shown} of {total} failures shown |
| `runIntel.failure.pill.bug` | Product bug |
| `runIntel.failure.pill.flaky` | Flaky |
| `runIntel.failure.pill.infra` | Infra |
| `runIntel.failure.action.openCase` | Open test case |
| `runIntel.failure.action.logs` | View logs |
| `runIntel.failure.action.decisionTrail` | Decision trail |
| `runIntel.failure.action.fileDefect` | File defect |
| `runIntel.actions.title` | Recommended actions |
| `runIntel.actions.routedBy` | Routed by role |
| `runIntel.actions.lowPriority` | low priority for this run |
| `runIntel.confidence.title` | AI confidence |
| `runIntel.confidence.lowLabel` | Low — recommendation derived deterministically, not by LLM reasoning |
| `runIntel.confidence.gap.evidence` | No evidence artifacts collected |
| `runIntel.confidence.gap.baseline` | No baseline run for comparison |
| `runIntel.confidence.gap.tools` | 0 tools invoked during analysis |
| `runIntel.category.title` | Failure category |
| `runIntel.category.empty` | No {category} detected this run |
| `runIntel.provenance.label` | Provenance |
| `runIntel.provenance.link` | Decision trail |

**Tone:** Concise, plainspoken, no jargon. Sentence case throughout (gate names are an exception — uppercase or title-case). Write actions as imperatives ("Hold release", not "You should hold the release").

---

## 11. Accessibility checklist

- [ ] Tab order: header → persona tabs → header actions → verdict CTAs → pipeline stages → left-col cards top-to-bottom → right-col cards top-to-bottom → footer link.
- [ ] Visible focus ring on every focusable element (`outline: 2px solid var(--color-ring); outline-offset: 2px`).
- [ ] Color is never the only signal — every status (gate, stage, dimension, category) has both color and an icon or text label.
- [ ] All inline SVGs have `aria-hidden="true"`; icons that *are* the only label have `role="img"` + `aria-label`.
- [ ] The verdict h2 must be programmatically associated with the gate severity — wrap the gate name in a `<span aria-label="Gate: Conditional Go">`.
- [ ] Failure error block: `<pre>` with `role="region"` and `aria-label="Error stack trace"`.
- [ ] Persona tabs: implement as `role="tablist"` with proper `role="tab"`, `aria-selected`, `aria-controls`. Right-rail content acts as the tabpanels.
- [ ] Pipeline stages: `<button>` elements with `aria-label="Stage 5: Triage, skipped"` so screen readers get the full state.
- [ ] Live announcements: when the verdict changes (e.g. after Refresh), use a polite live region to announce "Gate updated to Conditional Go, risk 25 of 100".
- [ ] Motion: respect `prefers-reduced-motion`; disable verdict dot pulse.
- [ ] Contrast: all text/bg combinations in the design are tested ≥4.5:1 body, ≥3:1 for large text and UI elements. Re-check if you change palette.

---

## 12. Analytics & events

| Event name | When fired | Properties |
|---|---|---|
| `run_intel_viewed` | page mount | `runId, gate, riskScore, confidence, persona` |
| `run_intel_persona_changed` | tab click | `from, to` |
| `run_intel_action_clicked` | header action | `action: 'refresh' \| 'pdf' \| 'evidence' \| 'compare' \| 'view_cases'` |
| `run_intel_verdict_action` | gate CTA | `decision: 'hold' \| 'override' \| 'approve_conditional' \| 'ship' \| 'block', riskScore` |
| `run_intel_stage_opened` | stage click | `stageName, stageStatus` |
| `run_intel_failure_opened` | failure CTA | `failureId, action` |
| `run_intel_decision_trail_opened` | provenance link or header button | `source: 'header' \| 'footer'` |

---

## 13. Open questions

- [ ] Verdict CTAs — is `Override gate` an inline action or does it open a confirm modal with a required reason? (Recommendation: modal + required reason, audit-logged.)
- [ ] Stage drawer scope — does v1 ship the drawer or just the click→navigate behavior? (Currently stubbed.)
- [ ] Multi-suite runs — is `Affected suite` a chip strip with overflow `+N more`, or does the suite breakdown move out into its own card? (Design assumes chip strip.)
- [ ] Persona — does Manager differ from Executive in this scope, or is it Phase 2? (Design renders all three as identical for now; tab is wired but doesn't change the layout.)
- [ ] Real-time SSE — confirm endpoint exists at `/api/runs/:id/events` or recommend polling fallback.
- [ ] Defect tracker integration — Jira / Linear / both? Affects the `File defect` flow.
- [ ] "Compare" button when there's no obvious right-hand side — disable, or default to the previous run on the same branch? (Recommendation: default to previous run, allow change in the compare page.)

---

## 14. Files in this bundle

```
design_handoff_run_intelligence/
├── README.md                         ← this file
├── tokens.css                        ← design-token reference (mirror of frontend/src/index.css)
└── screens/
    └── run-intelligence.html         ← the design reference, opens standalone in a browser
```

Open `screens/run-intelligence.html` directly in a browser to see the live design at the intended viewport (1440px). It uses CSS variables defined in `tokens.css` (loaded via `<link rel="stylesheet" href="../tokens.css">`).

---

## 15. How to implement (suggested order)

1. **Audit** — open `screens/run-intelligence.html` next to the existing `/runs/:runId/intelligence` page. Note which sections collapse to existing components (`StatusBadge`, `MetricCard`) and which are new.
2. **Add novel tokens** (§8 — risk gradient, soft alert bg/border, display sizes) to `frontend/src/index.css`.
3. **Build atoms** — gate badge variants, dimension tile, stage cell, role-action row, suite chip. These don't exist yet; add them under `frontend/src/components/ui/runIntel/`.
4. **Compose page** — `RunIntelligencePage.tsx` assembles header + verdict + ribbon + body grid. Use placeholder data first.
5. **Wire data** — `useRunIntelligence(runId)` hook around the GET endpoints; React Query for caching and SSE wiring.
6. **States and motion** — implement the skipped/conditional/no-go variants and the pulse animation. Verify reduced-motion fallback.
7. **Accessibility pass** — keyboard-only walk-through, axe audit, contrast spot-check.
8. **Visual diff** — open the HTML reference at 1440×900 next to the React build at the same viewport. Resolve any deltas. Commit screenshots in the PR.

---

## 16. Out of scope / known gaps

- **Mobile (<768px):** not a v1 target. Show a "view on a wider screen" notice or a single-column scroll fallback.
- **Stage drawer contents:** the click handler ships, the drawer body is Phase 2.
- **Decision-trail modal:** out of scope here; the link/button hooks ship but the modal lives in another doc.
- **Compare picker:** assumes the existing `/runs/compare` page handles the right-hand side selection.
- **Print styles:** the `PDF` button calls a server-side renderer; no client-side print stylesheet is required.
- **Internationalization beyond the keys in §10:** copy is keyed but the locale system is external to this scope.
