# Handoff: TestLookup `/agents` — Workflow Pane (Direction C: Compute Graph)

## Overview

This package describes a redesign of the **workflow pane** on the `/agents` route in TestLookup, implemented as **Direction C — the Compute Graph**.

The current pane renders the deep-pipeline stages as a flat grid of cards. The redesign treats the workflow as what it actually is: a **DAG of compute stages with explicit dependencies and runtime decisions**. Direction C is the densest of three explored directions — a node-graph laid out on a dotted canvas, with metric-rich cards (duration, confidence, tokens, cost) and explicit, labeled edges. It is intended as a **power-user / debugger view** for engineers who need to inspect what each stage is doing and how data flows between them.

> Three directions were explored on the same canvas (A — Subway map, B — Track + Gantt, C — Compute graph). **This handoff is for Direction C.** A and B are included in `design/` only as context.

## About the Design Files

The files in `design/` are **HTML/JSX prototypes built for design review** — they use React 18 via UMD + Babel-standalone in the browser, with synthetic data inlined into `shared.jsx`. They are reference material for look and behavior, **not production code to copy directly**.

Your job is to **recreate Direction C inside the existing TestLookup `frontend/` codebase** (React + TypeScript) using its established patterns: routing, data fetching (whatever it uses for the live run stream — websocket / SSE / polling), component library, and theming. Do not import the prototype files into `frontend/`.

If something in this README conflicts with conventions already in `frontend/`, follow the codebase. Specifically:

- Use the existing color tokens / theme variables. The hex values listed below are from a GitHub-dark-inspired palette used in the prototype — map them onto whatever the codebase actually defines (likely a `theme.ts` or CSS variables file).
- Use the existing icon library. The prototype draws inline SVGs in `shared.jsx`'s `<Glyph>` component; replace with the codebase's icon set (likely `lucide-react` based on the SVG shapes used).
- Use the existing typography scale. The prototype uses system sans + a monospace; the codebase should already define both.

## Fidelity

**High-fidelity.** Spacing, color, type, status semantics, and interaction states are intentional. Reproduce the layout pixel-close. The synthetic data covers the edge cases that matter — long-running stage, failed stage with retry, skipped stage with reason, runtime decision diamond, and a completed-run verdict.

---

## The page in one screen

```
┌────────────────────────────────────────────────────────────────────────────────────┐
│ TopBar:  AI Reports › Agent Pipeline   ·   Workflow · Run 7f4e9c2a                 │
│          [LLM Mode chip]   [run picker]   [Summary report]   [trigger UUID input]  │
├────────────────────────────────────────────────────────────────────────────────────┤
│ ModeTabs:  Live   |  ● Debug   |   Audit   |   Compare                             │
├────────────────────────────────────────────────────────────────────┬───────────────┤
│ Compute canvas — dotted background, pan/scroll, fixed-size graph   │               │
│                                                                    │               │
│ ┌──────────┐   ┌──────────┐                  ┌──────────┐          │               │
│ │Ingestion │──▶│Anomaly   │──▶ ◇ decide ──llm▶│ RCA      │──▶ ...  │               │
│ │  done    │   │ Detection│       │ analysis │ running  │          │               │
│ │ dur 12s  │   │ conf 87% │       │  _mode   │ tokens   │          │  RightRail    │
│ │ conf —   │   │ tokens   │       │          │ 48.1k    │          │  (selected    │
│ │ tokens 0 │   │ 12.4k    │       │          │ cost     │          │   node:       │
│ │ cost $0  │   │ cost     │       │          │ $0.62    │          │   Overview /  │
│ └──────────┘   │ $0.08    │       │          │ [▮▮▮▯▯]  │          │   Output /    │
│                └──────────┘       │          │ ETA 175s │          │   Activity /  │
│                              parallel        └──────────┘          │   Logs)       │
│                                   │                                │               │
│                                   ▼      heuristic (faint, dashed)─┼───▶[skipped]  │
│                          ┌──────────┐                              │               │
│                          │Cluster   │──▶┌──────────┐               │               │
│                          │ done     │   │Flaky     │ ─dashed─▶ Health → Summary    │
│                          │ conf 94% │   │ failed   │              ↑                │
│                          └──────────┘   │ retry 1/3│              │                │
│                                         └──────────┘              │                │
│                                                                   └▶ Release Risk  │
└────────────────────────────────────────────────────────────────────┴───────────────┘
```

Open `design/Workflows Redesign.html` in a browser to see it. The canvas focuses Direction A fullscreen by default — press ESC (or use the canvas's escape control) to see A/B/C side by side, then click into the **Direction C** artboard. Direction C also defaults its `ModeTabs` to the **Debug** tab, since it is intended to live primarily in that mode.

---

## Layout

Top-level: a vertical flex column filling the route's main area.

```
flex: column, height: 100%, background: var(--bg-page)
├── TopBar           flex: 0 0 auto, height: ~62px
├── ModeTabs         flex: 0 0 auto, height: 41px
└── Body             flex: 1, display: flex, min-height: 0
    ├── ComputeCanvas  flex: 1, overflow: auto, background: dotted on var(--bg-page)
    │                  inner: position: relative, fixed pixel width/height (1750 × 560)
    │                  padding: 24px
    │                  - SVG edge layer (position: absolute, full bounds, pointer-events: none)
    │                  - Edge labels (absolutely positioned <div>s)
    │                  - Stage nodes (absolutely positioned)
    │                  - Decision diamond (absolutely positioned)
    └── RightRail      flex: 0 0 auto, width: 360px, border-left 1px var(--border-1),
                       bg var(--bg-1)
```

The canvas is **horizontally scrollable** — the graph is wider than the viewport on most monitors. Vertical overflow scrolls too. Do not auto-fit / zoom-to-fit on initial load; show the head of the pipeline pinned to the top-left.

> **Why no RunMeta / EventStrip / VerdictPanel?** Direction C does not include the persistent run-meta strip or live event feed below the canvas (those are part of Direction A). Stage-level events are still visible inside the RightRail's **Activity** tab. If the team wants a live event feed alongside the canvas, add it as a collapsible drawer pinned to the bottom of the canvas column — out of scope for this iteration.

---

## Data Model

Same as the other directions. The shape used by the prototype is in `design/shared.jsx`. Mirror it as TypeScript:

```ts
type RunStatus  = 'running' | 'done' | 'failed';
type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

interface Run {
  id: string;             // uuid
  shortId: string;        // first 8 chars
  branch: string;
  triggeredBy: string;
  startedAt: string;      // ISO
  startedAtLabel: string;
  elapsed: string;        // human label, recomputed every 1s while running
  pipeline: 'deep' | 'fast';
  llmMode: boolean;
  totalTests: number;
  failedCount: number;
}

interface Stage {
  id: 'ingestion'|'anomaly'|'rca'|'cluster'|'triage'|'flaky'|'health'|'summary'|'release';
  name: string;
  desc: string;                                  // one-line description shown in node body and rail
  status: StageStatus;
  dur?: string;                                  // human label, e.g. '12s', '1m 22s'
  startMs?: number;                              // seconds since run start (for ETA arithmetic)
  endMs?: number;
  etaMs?: number;
  metrics: { confidence?: string; evidence?: number; tokens?: string|number; cost?: string };
  glyph: 'database'|'alert'|'bot'|'layers'|'bug'|'warn'|'stethoscope'|'file'|'shield';
  skipReason?: string;
  error?: string;
}

interface Decision {
  id: 'route_analysis_mode';
  label: string;
  chosen: string;
  alternatives: string[];
  at: string;
  rationale: string;
}

interface Event {
  kind: 'started' | 'completed' | 'failed' | 'retry' | 'decision';
  stage: string;          // stage id, or decision id when kind === 'decision'
  at: string;             // 'HH:MM:SS'
  what: string;
}
```

The canonical fixture is in `shared.jsx` (constants `RUN`, `STAGES`, `DECISION`, `EVENTS`, `RECENT_RUNS`, `VERDICT`). Use it as your test data while wiring up the UI; swap in the live stream once the layout is right.

---

## Components

### 1. TopBar (`design/shared.jsx → TopBar`)

Page header. Three regions: title block (left), spacer, controls (right).

- **Title block** — eyebrow `AI Reports › Agent Pipeline` (10.5px, `--fg-4`, uppercase 0.12em); title `Workflow · Run {shortId}` (18px, 600).
- **LLM Mode chip** — purple pill (`--purple` on `--purple-soft`), bot icon + label.
- **Run picker** — `--bg-2` rounded 10px, with a pulsing `--accent` dot (only when run is in-flight), monospace short ID, branch, "Running · started 16:08:13 · 1m 22s elapsed" sublabel, separator, chevron-down. Clicking opens a dropdown of recent runs (see `RECENT_RUNS` fixture).
- **Summary report button** — accent-soft pill button (`--accent-soft` bg, `--accent-2` text, `rgba(68,147,248,.40)` border, 8px radius, 32px tall, 12px padding), file-text icon + label `Summary report` + arrow icon. Links to `/agents/reports/{shortId}`.
- **Trigger UUID input** — `--bg-2` rounded 8px, lightning-bolt icon, placeholder `Paste run UUID to trigger…`, square accent button on the right.

Height ~62px. Pinned at top (flex: 0 0 auto).

### 2. ModeTabs (`shared.jsx → ModeTabs`)

Four tabs: **Live · Debug · Audit · Compare**. Direction C defaults to **Debug**.

- Active tab: `--fg-1` text, 2px `--accent` underline at the bottom.
- Inactive: `--fg-3` text.
- The "Live" tab gets a green pulsing dot when active and the run is in-flight; when inactive, a `Live` micro-pill.
- Each tab has a small icon (`spin` / `bug` / `doc` / `graph`).

For this handoff only **Debug** needs to be functional (it is the home of the compute graph). The other three tabs render and switch but can show "Coming in next iteration" placeholders. If your codebase already implements Direction A's Live tab, leave it as-is — Debug becomes the new tab that hosts the compute graph.

### 3. ComputeCanvas — the heart of Direction C

A dotted, scroll-capable canvas onto which absolutely-positioned **stage nodes**, the **decision diamond**, and an **SVG edge layer** are placed.

**Container:**

- `flex: 1; overflow: auto; min-height: 0;`
- Background uses **two layered backgrounds**:
  ```css
  background:
    radial-gradient(circle at 1px 1px, rgba(255,255,255,0.025) 1px, transparent 1px) 0 0 / 24px 24px,
    var(--bg-page);
  ```
  This produces a faint 24px-on-center dot grid on top of the page background.
- Inner content wrapper: `position: relative; width: 1750px; height: 560px; padding: 24px;`. The fixed pixel size is important — it is what makes the graph scrollable rather than auto-fitting, and it's the coordinate space all of the absolute positions assume.

**Node positions** (`POS`, in `DirC-Compute.jsx`):

```
ingestion   x=  32  y= 80
anomaly     x= 290  y= 80
decision    x= 564  y=110   (88×88 square, rotated diamond)
rca         x= 700  y= 30
cluster     x= 700  y=250
triage      x= 970  y=250   (skipped)
flaky       x= 970  y=380
health      x=1240  y=380
summary     x=1240  y= 80
release     x=1500  y= 80
```

Node size: **226px wide × 132px tall** (`NODE_W = 226`, `NODE_H = 132`). The decision diamond is **88×88** rotated 45° (so its visual footprint is ~124px diagonal). Center math uses `pos + NODE_W/2, pos + NODE_H/2` for stages; `pos + 44, pos + 44` for the decision.

**The 10 edges** (see `EDGES` array):

| #  | From       | To       | Style                    | Label        |
|----|------------|----------|--------------------------|--------------|
| 1  | ingestion  | anomaly  | solid grey               | —            |
| 2  | anomaly    | decision | solid grey               | —            |
| 3  | decision   | rca      | **solid blue (chosen)**  | `llm`        |
| 4  | decision   | cluster  | **solid blue (chosen)**  | `parallel`   |
| 5  | decision   | triage   | faint dashed (not-chosen)| `heuristic`  |
| 6  | rca        | summary  | grey dashed              | —            |
| 7  | cluster    | flaky    | solid grey               | —            |
| 8  | flaky      | health   | grey dashed              | —            |
| 9  | health     | summary  | grey dashed              | —            |
| 10 | summary    | release  | grey dashed              | —            |

**Edge rendering** — a single `<svg>` overlays the canvas (`position: absolute; inset: 0; pointer-events: none;`). For each edge, build a cubic Bézier whose horizontal control points lie at the midpoint between the source's right edge and the target's left edge:

```js
const fromRight = from === 'decision' ? a.x + 30 : a.x + NODE_W/2;
const toLeft    = to   === 'decision' ? b.x - 30 : b.x - NODE_W/2;
const cx = (fromRight + toLeft) / 2;
const d  = `M${fromRight},${a.y} C ${cx},${a.y} ${cx},${b.y} ${toLeft},${b.y}`;
```

Stroke width is `2`. Three marker definitions live in `<defs>`:

| marker            | fill                  | used for                |
|-------------------|-----------------------|-------------------------|
| `c-arrow`         | `var(--border-strong)`| default grey edges      |
| `c-arrow-blue`    | `var(--accent)`       | chosen edges            |
| `c-arrow-faint`   | `var(--fg-faint)`     | not-chosen edges        |

Stroke color and arrow marker follow the same triad: chosen → accent, not-chosen → faint, otherwise → `--border-strong`. Dash array is `5 5` when `dashed` OR `notchosen`, else `0`. Not-chosen edges are also rendered at **0.55 opacity**.

**Edge labels** (the `llm`, `parallel`, `heuristic` chips):

- Absolutely-positioned `<div>` (NOT inside the SVG), translated with `transform: translate(-50%, -50%)`.
- Positioned at `((a.x + b.x) / 2 + 24, (a.y + b.y) / 2)` — the +24 nudge keeps the label clear of the diamond's hit area.
- Styling: 1×6px padding, 4px radius, 9.5px monospace, 1px border, `--bg-1` background. Two variants:
  - `.c-edge-label.chosen` — `--accent-2` text, `rgba(68,147,248,.40)` border, `rgba(68,147,248,.06)` background.
  - `.c-edge-label.notchosen` — `--fg-faint` text on the default chrome.

### 4. ComputeNode — the dense stage card

Each `ComputeNode` is a **226px-wide absolutely-positioned card** with five regions stacked vertically. See `DirC-Compute.jsx → ComputeNode`.

```
┌────────────────────────────────────────┐
│ [22px glyph icon]  Stage Name  [pill]  │  ← c-node-head
│ Compare against baselines, surface…    │  ← c-node-desc  (min-height 28px)
│ ┌──────────┐ ┌──────────┐              │
│ │duration  │ │conf      │              │  ← c-node-metrics, 2-col grid, gap 6
│ │   12s    │ │   87%    │              │
│ └──────────┘ └──────────┘              │
│ ┌──────────┐ ┌──────────┐              │
│ │tokens    │ │cost      │              │
│ │  12.4k   │ │  $0.08   │              │
│ └──────────┘ └──────────┘              │
│ (failed-only)  ⚠ retry 1/3 queued      │  ← red, 10.5px
│ (running-only) ▮▮▮▯▯ 1m 22s  ETA 175s  │  ← progress + ETA strip
└────────────────────────────────────────┘
```

**Card chrome:**

- Background `--bg-1`, border `1px solid --border-1`, radius 12px, padding 12px, cursor pointer, transitions on border-color and transform (.12s).
- Hover: border → `--border-2`.
- Selected: border → `--accent`, ring `box-shadow: 0 0 0 3px var(--accent-soft)`.

**Status variants** (composed onto the base class):

| Status   | Border                          | Background                      | Other                                                |
|----------|---------------------------------|---------------------------------|------------------------------------------------------|
| done     | `--border-1`                    | `--bg-1`                        | pill `green` (label `done`)                          |
| running  | `rgba(68,147,248,.50)`          | `rgba(68,147,248,.04)`          | pill `blue` (label `running`) + progress + ETA strip |
| failed   | `rgba(248,81,73,.50)`           | `rgba(248,81,73,.04)`           | pill `red` + red "retry 1/3 queued" line             |
| skipped  | dashed `--border-1`             | transparent                     | pill `muted`, card opacity .55 at rest               |
| pending  | `--border-1`                    | `--bg-1`                        | pill `muted` (label `pending`)                       |

**Head row** (`.c-node-head`):

- 22×22px icon square. Background uses status-tinted soft color (e.g. `--green-soft` for done, `--accent-soft` for running, `--red-soft` for failed) and the matching foreground color. Inside it sits the stage's Lucide-style glyph (`database`, `alert`, `bot`, `layers`, `bug`, `warn`, `stethoscope`, `file`, `shield`).
- Stage name — `13px / 600 / --fg-1`, ellipsis when overflowing.
- Status pill on the right — 9.5px, 999px radius, color-tone per status. The `running` pill also includes a 4×4px dot in its own color.

**Description row** (`.c-node-desc`):

- `11px / --fg-3 / line-height 1.4`. Empty space reserved with `min-height: 28px` so all cards align even when desc wraps to one line.

**Metrics grid** (`.c-node-metrics`):

- 2-column CSS grid, gap 6px. Always renders exactly 4 cells in this order:

  1. `duration` — `dur` or `—`.
  2. If `metrics.confidence != null` → `conf` cell (tone `ok` when > 80%). Otherwise → `evidence` cell.
  3. `tokens` — `metrics.tokens ?? '—'`.
  4. `cost` — `metrics.cost ?? '—'` (tone `warn` when parsed > $0.50).

  Each cell is `.c-metric`: `--bg-page` background, `1px --border-1` border, 6px radius, 4×6 padding. The key is 9.5px uppercase `--fg-4` letter-spacing 0.06em; the value is `11.5px monospace --fg-1` on the next line. Toned values (`.ok`, `.warn`, `.bad`) override the value color to `--green` / `--amber` / `--red`.

**Failed-state suffix:** below the metrics, a single row with a small `warn` glyph and "retry 1/3 queued" text in `--red`, 10.5px, gap 6.

**Running-state suffix** (`.c-node-spark`):

- A 4px-tall track on `--accent-soft`, with a progress bar fill (`--accent`, currently fixed at 47% width — drive this from `(now - startMs) / (etaMs - startMs)` when wiring to live data).
- Below the track: a row of two monospace 10px labels — current elapsed (`{dur}`) on the left, `ETA {etaMs - startMs}s total` on the right, separated by `space-between`.

### 5. Decision diamond

An 88×88 square rotated 45°, positioned at `POS.decision`. The visible "X" is purple to flag its semantics as a runtime branch.

- Border `1.5px solid --purple`, background `rgba(163,113,247,.08)`, radius 8px (rounding applies inside the rotation).
- Selected: background bumps to `rgba(163,113,247,.18)` and adds a 3px `--purple-soft` ring.
- Inner content un-rotates with `transform: rotate(-45deg)`:
  - Eyebrow: `decide` (8.5px, uppercase 0.12em, opacity .8, purple).
  - Key: `DECISION.label` with the `route_` prefix stripped (10px monospace) — i.e. `analysis_mode`.
- Cursor pointer; native `title` falls back to `DECISION.rationale` on hover. (Replace with the codebase's tooltip primitive.)

Clicking the diamond sets the rail selection to the decision id.

### 6. RightRail (`shared.jsx → RailDetail`)

`width: 360px`, `--bg-1` background, left border. Three states:

#### a. Stage selected (default)

```
┌─ rail-head ─────────────────────────┐
│ [● status dot] [STATUS] [dur]       │  ← eyebrow row
│ Stage name                          │  ← 16px / 600
│ One-line description                │  ← 11.5px / --fg-3
├─────────────────────────────────────┤
│ Tabs: Overview | Output | Activity  │
│        (with event count) | Logs    │
├─────────────────────────────────────┤
│ Body (scrollable)                   │
│  • Error block (status=failed)      │
│  • Skip reason (status=skipped)     │
│  • Metrics KV table:                │
│      status / confidence / evidence │
│      / tokens / cost / duration     │
│  • Output blob (Output tab)         │
│  • Stage events (Activity tab)      │
│  • Logs blob (Logs tab)             │
└─────────────────────────────────────┘
```

The KV table is a `<dl>` with a 95px first column, monospace 11.5px values. Status colors: `.ok` → `--green`, `.bad` → `--red`.

The "blob" is a `--bg-page` / `--border-1` card, 8px radius, 10/12 padding, 11px monospace, `white-space: pre-wrap`. Use it for output JSON, error stack traces, and log tails. Apply syntax highlighting if the codebase has a primitive; otherwise plain text is fine.

#### b. Decision selected

```
[◇ Decision purple pill]
route_analysis_mode
A branch chosen by the orchestrator at runtime.

OUTCOME
  chosen      llm   (green)
  at          16:08:13.59

BRANCHES
  [taken]      llm        ← accent-soft background
  [not taken]  heuristic  ← --bg-2 background, muted

RATIONALE
  17 failures > heuristic threshold (5). Routing to LLM.
```

#### c. Nothing selected

Centered placeholder text: "Select a stage in the flow to inspect it." (30px padding, 12px, `--fg-3`).

Selection state is **shared between node clicks and diamond clicks** — one `selectedId` string (a stage id OR `DECISION.id`).

---

## Interactions

| Action                              | Result                                                                |
|-------------------------------------|-----------------------------------------------------------------------|
| Click stage node                    | Selects stage; rail updates; node gains accent ring                   |
| Click decision diamond              | Rail switches to decision detail body                                 |
| Click empty canvas                  | No-op (keep current selection — clearing is too easy by accident)    |
| Hover skipped node                  | Tooltip with `skipReason` (native `title`); opacity bumps to 1.0      |
| Hover decision diamond              | Native title tooltip with rationale                                   |
| Scroll canvas                       | Standard horizontal/vertical scroll; cards stay put                    |
| ESC                                 | Clears selection (optional but nice)                                  |

**Live updates** (stream from backend — websocket / SSE / polling, whatever the codebase exposes):

- `RUN.elapsed` recomputes once per second from `startedAt`.
- A stage `status` transition re-renders the matching node — change border / background / pill / metric values.
- A `running` stage's progress bar should fill from `(now - startMs) / (etaMs - startMs)` clamped to `[0, 1]`. The prototype hardcodes 47% — make it derived.
- A `decision` event sets the diamond's `chosen` and **re-routes** edges: the chosen branch becomes solid blue with `c-arrow-blue`; the not-taken branch becomes faint dashed with `c-arrow-faint`. If the current `selectedId` is a stage that just transitioned to `failed`, surface the error in the rail.
- A `retry` event adds the "retry N/M queued" suffix to the failed node.
- Edge data is **derived** from the run's executed plan + the chosen decision branch — don't store the chosen/notchosen flags statically once you're on real data. Compute them when the decision arrives.

---

## State Management

Top-level state for the workflow pane in Debug mode:

```ts
const [mode, setMode]              = useState<'live'|'debug'|'audit'|'compare'>('debug');
const [selectedId, setSelectedId]  = useState<string|null>('rca');           // first running stage
const { run, stages, decision, events } = useRunStream(runId);                // codebase primitive
```

`useRunStream` should be the existing data-fetching primitive (likely a `react-query` hook wrapping a websocket or SSE subscription). The prototype's `shared.jsx` is your fixture for tests and Storybook.

Selection defaults to the first **running** stage on mount (or first **failed** if any failed); otherwise `null`.

---

## Design Tokens

These are the prototype's `<style>` block in `design/Workflows Redesign.html`. Map them onto whatever the codebase already defines — names match GitHub-dark conventions and may already exist verbatim.

```css
/* Surfaces */
--bg-page:        #0d1117;
--bg-1:           #161b22;
--bg-2:           #1c222b;
--bg-3:           #232a35;
--bg-hover:       #21262d;
--bg-active:      rgba(68,147,248,.10);

/* Borders */
--border-1:       #2a323d;
--border-2:       #3a4453;
--border-strong:  #4a5564;

/* Foreground */
--fg-1:           #f0f6fc;
--fg-2:           #c9d1d9;
--fg-3:           #8b949e;
--fg-4:           #6e7681;
--fg-faint:       #4a525c;

/* Accents */
--accent:         #4493f8;   --accent-2:  #58a6ff;   --accent-soft: rgba(68,147,248,.14);
--green:          #3fb950;                            --green-soft:  rgba(63,185,80,.14);
--red:            #f85149;                            --red-soft:    rgba(248,81,73,.14);
--amber:          #d29922;                            --amber-soft:  rgba(210,153,34,.14);
--purple:         #a371f7;                            --purple-soft: rgba(163,113,247,.14);
--cyan:           #39c5cf;                            --cyan-soft:   rgba(57,197,207,.14);
```

**Type:**

```css
--font-sans:  ui-sans-serif, system-ui, -apple-system, "Inter", "Segoe UI",
              Helvetica, Arial, sans-serif;
--font-mono:  ui-monospace, "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
```

Body text 13px / line-height 1.5. Sizes used in Direction C specifically:

- Eyebrow / micro: 9.5–10.5px, weight 600, letter-spacing 0.06–0.12em, uppercase.
- Body / desc: 11–13px, weight 400–500.
- Stage node name: 13px / 600.
- Metric key: 9.5px uppercase 0.06em.
- Metric value: 11.5px monospace.
- Edge label: 9.5px monospace.
- Decision eyebrow / key: 8.5px / 10px monospace.
- Page title: 18px weight 600.
- Rail title: 16px weight 600.

**Spacing**: 4-step scale — 6 / 10 / 12 / 14 / 18 / 22 / 24 px. Card paddings are 12px. Outer paddings are 22–24px.

**Radii**: 4px (edge labels), 6px (chips, status icon squares, metric cells), 8px (blobs, decision rotated rect), 10px (run picker), 12px (compute nodes), 999px (status pills).

**Shadows**: minimal. Only the tooltip primitive uses `0 12px 30px rgba(0,0,0,.45)`. Selection rings use `box-shadow: 0 0 0 3px <soft>` rather than blurred shadows.

**Animations**:

- `pulse` — 1.6s infinite, on running status dots and the run-picker pulse. `box-shadow: 0 0 0 0 → 8px → 0 rgba(68,147,248, .55→0)`.
- `slide` — 1.6s infinite, used on Direction A's running cards; **not used in Direction C** — Direction C draws a static progress bar fill derived from start/eta times instead.

---

## Edge Cases

- **Layout has no responsive variant.** The compute canvas assumes a fixed 1750×560 logical size; the canvas container scrolls horizontally on narrow viewports. Do not attempt a stacked-mobile rendering — Direction C is desktop-only.
- **Skipped stages don't render outgoing edges.** Triage is skipped on the LLM branch; its outgoing edge to anything else simply isn't in the `EDGES` array. When a decision rerouting causes a previously-active stage to become irrelevant, drop its outgoing edges and apply the `skipped` styling to the node.
- **Running progress bar at 0%** — show a thin track but no fill rather than a flicker.
- **Decision before it has resolved** — if you're rendering a graph mid-flight before the decision event has arrived, show no `chosen` flag on edges, and render both outgoing edges as default grey solid. The diamond can show `deciding…` in place of the key, with a small pulse animation.
- **No `etaMs`** — hide the ETA line; show only the elapsed duration.

---

## Assets

No raster assets. All icons are inline SVG; replace with the codebase's existing icon library. Icons used (semantic names) in `shared.jsx → Glyph` — all standard Lucide:

`database` `triangle-alert` (alert) `bot` `layers` `bug` `circle-alert` (warn) `stethoscope` `file` `shield` `check-circle` (check) `x-circle` (x) `loader-2` (spin) `arrow-right` (arrow) `chevron-down` (chevdown) `zap` `file-text` (doc) `clock` — plus three custom outlines for `flow` / `gantt` / `graph` (the ModeTabs icons), which are easy enough to inline.

---

## Files in This Bundle

```
design_handoff_workflows_pane_compute_graph/
├── README.md                          ← this file
└── design/
    ├── Workflows Redesign.html        ← entry point. Open in a browser to see the canvas.
    ├── shared.jsx                     ← fixture data (RUN, STAGES, DECISION, EVENTS, VERDICT,
    │                                    RECENT_RUNS) + shared components (TopBar, ModeTabs,
    │                                    RailDetail, Glyph, StatusBadge). Reuse as test fixtures.
    ├── DirC-Compute.jsx               ← THE CHOSEN DIRECTION FOR THIS HANDOFF. Read this carefully —
    │                                    it's the executable spec for the compute graph.
    ├── DirA-Subway.jsx                ← Direction A (subway map) for context only.
    ├── DirB-Gantt.jsx                 ← Direction B (track + gantt) for context only.
    ├── canvas-app.jsx                 ← Wraps A/B/C as artboards on a design canvas.
    └── design-canvas.jsx              ← The pan/zoom canvas component (not part of the design).
```

The HTML loads everything via `<script type="text/babel">` tags — open the file directly in a browser; no build step. The components share globals via `window.SHARED` and `window.DirCCompute` — that pattern is a prototype-only workaround for cross-`<script>` scope isolation; **do not replicate it in production.**

---

## Definition of Done

- [ ] `/agents` route, **Debug** ModeTab renders the compute canvas described in **Components → 3**.
- [ ] Canvas background is the dotted radial-gradient on `--bg-page`; canvas scrolls horizontally/vertically when the viewport is smaller than 1750×560.
- [ ] All ten nodes render at the documented `POS` coordinates; all five status states (done / running / failed / skipped / pending) display correctly with the visual treatments listed under **ComputeNode**.
- [ ] All ten edges render with the correct bezier path, dash style, color, and arrow marker; chosen edges are blue; not-chosen edges are faint dashed at 0.55 opacity.
- [ ] Edge labels `llm`, `parallel`, `heuristic` render in the right place with the correct chosen/notchosen styling.
- [ ] Decision diamond renders between Anomaly Detection and RCA; clicking it switches the RightRail to the decision detail body.
- [ ] Selection is shared between node clicks and the diamond.
- [ ] Live updates from the run stream advance node statuses, metric values, progress bars, and edge routing without flicker.
- [ ] Decision routing is dynamic: when the `decision` event arrives, chosen branches become solid blue, not-chosen branches become faint dashed.
- [ ] All four ModeTabs are clickable; only Debug is implemented for this handoff; Live/Audit/Compare show a holding-pattern empty state (or, if Direction A's Live tab is already shipped, keep that and add Debug alongside).
- [ ] Hover tooltips: skipped node shows skip reason; decision diamond shows rationale.
- [ ] Storybook: one story per stage state, plus a "completed run" story and a "failed retry" story — driven from the fixtures in `shared.jsx`.

Out of scope for this handoff: implementation of Live (already shipped via Direction A) / Audit / Compare modes; replacing the existing run picker / trigger UI elsewhere on the page; backend changes; mobile/responsive layouts.
