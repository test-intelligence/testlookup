# Charts and visual analysis

How to read, explore and export the charts on every report page.

> **Important.** A chart never fills a gap with zero. A day or a test with nothing to measure shows **—** and "Not measured", with the reason. Read the footer's scope before reading the shape.

## Reading any chart

Every chart sits in the same frame, so these parts mean the same thing everywhere:

| Part | What it tells you |
|---|---|
| **Title** | What is measured, for example "Pass rate trend" |
| **Takeaway** | One sentence under the title, such as "Down 3.1 pts in 30 days", when the data supports one |
| **Badges** | A filter the server could not apply to this chart, for example "Not filtered by release" |
| **Footer** | "Showing top N of M" when lines were folded into **Other**, and the totals: "N of M runs · N of M executions" |
| **—** | Not measured: there was nothing to count. It is never drawn as 0 |

### Pass rate and skipped tests

A pass rate counts **passed, failed and broken** results. Skipped and unknown results are left out of both sides of the fraction. A day where every test was skipped has no pass rate: the line has a gap there, not a drop to 0%.

### When a chart has nothing to show

| Message | Meaning | What to do |
|---|---|---|
| "No runs have been ingested for this project yet" | The project has never received a run | **Ingest test results** |
| "No data matches the current filters" | Runs exist, but not in this scope | **Clear filters**, or widen the window |
| "No executions in this window" | Runs exist, but none in these days | Widen the window |

### When a chart cannot load

- **"Could not load this chart"** shows the error and a **Request ID**. Press **Retry**. If it keeps failing, include the request ID when you report it.
- **"Waiting to retry"** means you made too many requests in a short time. The chart says how long it will wait ("This chart will try again in N s") and retries by itself, up to three times. After that, press **Retry**.
- **"You do not have access to this project"**: ask a project admin for membership.
- **"Your session has expired"**: **Sign in** again.
- **"A new version is available"**: TestLookup was upgraded while the page was open. Press **Reload**.

While new data loads after a filter change, the previous chart stays on screen, dimmed, so the page does not jump.

## Interacting with a chart

- **Tooltip.** Hover a point or bar. The tooltip opens beside the mark, not over it, and stays long enough to move the pointer onto it.
- **Legend.** Select a series to hide or show it. **Shift**+select shows that series alone; **Show all series** brings the rest back.
- **Zoom.** Time charts have a range brush underneath. Drag across it, or click a first and a last day. The page filters do not change: the footer says "Zoomed to …", and the summary, the table and any export cover the zoomed days only. **Reset zoom** returns to the page window; **Apply as time filter** makes the zoom the page's window.
- **Table.** **View as table** shows the numbers behind the chart; **Hide table** closes it. When lines were folded into Other, **View the top N as a table** lists them.
- **Full screen.** The **Full screen** button enlarges the whole chart, with its title, legend and footer, in larger type. **Escape** or **Exit full screen** returns.
- **Presentation mode.** Turn it on from the sidebar footer. It uses larger type and stronger contrast across the whole app, for screen sharing and wall displays. It is remembered in this browser only.

## Filtering the page from a chart

Hold **Shift** (or **Ctrl**/**Cmd**) and click a suite or release in a chart, or use **Filter page by this** in the point's readout. A plain click drills down or opens rows instead.

- A **suite** sets the page's **Test suite** filter. This works on Trends, Coverage and Failures.
- A **release** sets the release in the top bar. This needs one project selected, not All Projects.
- The project is never changed from a chart: choose it in the top bar.

A message confirms the change, for example 'Page filtered by CheckoutSuite (clear: "All suites")'. Clicking another mark replaces the filter; it does not add to it.

## Exporting a chart

Every chart has an **Export** menu:

| Format | Contains |
|---|---|
| **PNG image** | The chart as drawn |
| **SVG image** | A scalable drawing, for documents and slides. **Light background** draws it on white whatever your theme (charts drawn as SVG only) |
| **CSV data** | The values behind the chart. Unmeasured points are empty cells, not zeros |

Each file records where it came from: the project, release, suite, window (in UTC), totals, when it was generated and the app version, plus the zoom when one is applied. File names follow `testlookup_<chart>_<project>_<date-time>Z`.

A 3D view exports its 2D chart: the image and the data are the same points.

For a whole report as a PDF or Excel file, see [Reports and exports](/docs/reports).

## What each page adds

| Page | Charts |
|---|---|
| **Overview** | Pass rate trend, status breakdown, top failing tests, failure categories |
| **Trends** | Pass rate by suite, test duration (p50 / p95), Compare, and the suite-by-day heatmap |
| **Coverage** | The test coverage map, and heatmaps of suite pass rate by environment or by release |
| **Failures** | Failures grouped by error message, systemic flake clusters, the drill-down, and the duration-versus-failure-rate scatter |
| **Suite detail** | Test results by run (heatmap) and the test scatter |
| **Summary report** | Status breakdown, results by suite, failures by test, pass rate trend |
| **Release gate** | Pass rate by release and failure cluster share, under **Context** |
| **Run compare** | Status changes between two runs |

### Trends

- **Pass rate by suite** draws the 7 busiest suites and folds the rest into **Other**. **Customise** changes the metric, the line dimension (suite, environment, branch or release), day or week buckets, the number of lines, the chart type and the scale. Options that do not fit the data are shown with the reason. Customisations are kept in this browser; **Reset to default** restores the chart.
- **Overlays.** **7-day moving average** and **Trend line** draw over the pass rate. Days that are unusual for the series are marked "Flagged as unusual". The trend line needs at least 7 days with runs.
- **Test duration (p50 / p95)** shows the typical and the slow end of test durations. A day without timings is a gap.
- **Compare** draws suites or releases you choose on one chart. It starts with the 3 busiest suites; pick others with **Suites** and **Releases** in the card (releases need one project selected). **Compare by** draws one line per suite, per release, or per suite and release (colour is the suite, the dash is the release), up to 8 lines; **Metric** switches between pass rate, failures and executions. Your choice is in the page link, so sharing the link shares the comparison.
- **Suite pass rate by day** is a heatmap of the 40 suites with the most failures.

### Failures

- **Failures grouped by error message** groups failures with the same message. The **Systemic flake clusters** tab shows tests that fail together.
- **Drill-down.** **Results by suite** opens into statuses, then failing tests, then individual executions. Each level is in the URL, so **Back** returns one level.
- **Test duration vs failure rate** plots one point per test; slow and flaky tests sit at the top right. **Select slow and flaky** selects that corner, or use **Drag to select**. The scatter needs one project selected.
- **View in 3D** adds a third axis. Drag to rotate; **Reset view** straightens it; **Back to 2D** returns. 3D needs a browser with WebGL 2; without it the 2D chart stays, with a note.

### Run compare

**Status changes** shows how each test's result moved between the two runs, for example passed to failed. Click a flow to filter the per-test table below to those tests.

## Explorer and saved views

**Testing → Explorer** draws one metric as small multiples: one panel per suite or per release, up to 12. See [Dashboards and metrics](/docs/dashboards) for its options.

**Views** saves the current filters on Overview, Trends, Coverage, Failures, Defects, the Summary report and the Explorer:

- A view keeps the top-bar release, the window, the page's suite filter, and page settings such as the Summary mode.
- Views belong to a project. The **Views** button appears once one project is selected.
- **Share with the project** makes a view visible to every member; shared views are marked "(shared)". Opening someone else's view never shows you data you could not already see.
- **My default for this page** opens that view when you first come to the page, unless the link you followed already names a release or suite.

## Limits

| Limit | Value |
|---|---|
| Lines on one chart | 8 (7 plus Other) |
| Analytics window | 1 to 365 days; per-execution charts up to 90 days |
| Points on a scatter | 2,000 by default, up to 5,000 |
| Rows per drill-down page | 50 |
| Explorer panels | 12 |
| Chart requests per user | 120 a minute per chart route; 60 a minute for heatmaps, the coverage map, failure groups and the scatter |

## Related

- [Dashboards and metrics](/docs/dashboards)
- [Reports and exports](/docs/reports)
- [Integrations](/docs/integrations): the CLI `analytics` command and the MCP `get_chart_data` tool
- [Troubleshooting](/docs/troubleshooting)
