/**
 * JR-04 step 3: an empty cluster list must not read as a measured zero.
 *
 * `failure_clusters: []` means two different things and the reader could not
 * tell them apart:
 *
 *   a) clustering ran and grouped nothing, or
 *   b) **clustering never ran**
 *
 * Only the `deep` workflow contains the `failure_clustering` stage
 * (`workflow.py: DEEP_REQUIRED_STAGES`), so every `offline` pipeline reports an
 * empty list by construction. The card rendered that as:
 *
 *     Anomalies
 *     0
 *     no clusters
 *
 * Measured on a real run (`0613ab2f`, 2 failed tests, `deep_pipeline_status:
 * never_run`): a run with genuine failures claimed zero anomalies and "no
 * clusters", which a reader reasonably takes as "the failures were analysed and
 * found unrelated". Nothing had been analysed.
 *
 * The payload already carried the answer — `pipeline_stages` lists what actually
 * executed — and the page simply never consulted it.
 *
 * `clusteringRan` is derived from the stage list rather than from the workflow
 * name, so it stays correct if clustering is ever added to another workflow.
 *
 * Secondary, recorded but NOT fixed here: the stat was labelled "Anomalies"
 * while its value was the cluster count. The intelligence payload exposes no
 * anomaly count for the page to use instead (the `anomaly_count` seen during
 * this journey lives in `agent_stage_results`, not in this response), so
 * renaming the label to "Clusters" makes it honest about what it shows; giving
 * the card a real anomaly count is a separate change.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

/**
 * The card is not exported, so the contract is asserted over the page source.
 * That is deliberate rather than lazy: the defect was a *string* shown to a
 * user, and the fix is a branch on a prop. Both are visible in the source, and
 * mounting the whole page would need the full SWR/router/store stack for a
 * two-line assertion.
 */
const PAGE_SOURCE = Object.values(
  import.meta.glob('./RunIntelligencePage.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  }),
)[0] as string

describe('JR-04 step 3 — clustering that never ran is not reported as zero', () => {
  it('found the page source to check', () => {
    // Without this the assertions below pass vacuously if the glob breaks.
    expect(PAGE_SOURCE.length).toBeGreaterThan(1000)
    expect(PAGE_SOURCE).toContain('TestOutcomeCard')
  })

  it('no longer claims "no clusters" unconditionally', () => {
    // The exact string that shipped the false zero.
    expect(PAGE_SOURCE).not.toMatch(/tiny=\{failureClusters\.length > 0 \? .* : 'no clusters'\}/)
  })

  it('says clustering was not run when it was not', () => {
    expect(PAGE_SOURCE).toMatch(/not run .* deep investigation only/i)
  })

  it('distinguishes that from a genuine empty result', () => {
    // "no clusters found" is only correct when clustering actually ran.
    expect(PAGE_SOURCE).toContain("'no clusters found'")
  })

  it('derives the flag from the stage list, not the workflow name', () => {
    // A workflow-name check would break the moment clustering is added
    // elsewhere; the stage list reports what actually executed.
    expect(PAGE_SOURCE).toMatch(
      /clusteringRan=\{pipeline_stages\.some\(\(s\) => s\.stage_name === 'failure_clustering'\)\}/,
    )
  })

  it('uses the field name the API actually returns', () => {
    // First draft used `s.stage`, which tsc rejected: the payload and the
    // PipelineStage interface both use `stage_name`. Pinned so a rename of one
    // side without the other is caught here rather than silently making
    // `clusteringRan` always false — which would restore the defect inverted,
    // showing "not run" for pipelines that did cluster.
    expect(PAGE_SOURCE).not.toMatch(/s\.stage ===/)
  })

  it('labels the stat for what it counts', () => {
    expect(PAGE_SOURCE).toMatch(/label="Clusters"/)
    expect(PAGE_SOURCE).not.toMatch(/label="Anomalies" value=\{`\$\{failureClusters\.length\}`\}/)
  })
})

describe('the rendering primitive still works', () => {
  it('renders text passed to it', () => {
    // Guards against the suite becoming source-only: if RTL breaks, these
    // source assertions would still pass and prove nothing about rendering.
    render(<span>not run — deep investigation only</span>)
    expect(screen.getByText(/not run/i)).toBeInTheDocument()
  })
})
