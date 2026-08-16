/**
 * Regression: the compute canvas must not draw a pipeline that never ran.
 *
 * Owner report, 2026-08-16: an *investigation* pipeline (GroundTruthSuite,
 * Run #4) showed COMPLETED in the header while the workflow canvas showed its
 * stages pending.
 *
 * Measured live. The API returned seven stages, all `completed`:
 *
 *   hypothesis_commit, hypothesis_known_flaky, hypothesis_environment,
 *   hypothesis_infra, hypothesis_regression, investigator_plan,
 *   investigator_synthesis
 *
 * The canvas rendered: Ingestion, Anomaly Detection, Root Cause Analysis,
 * Flaky Sentinel, Release Risk — and the word "Hypothesis" zero times. Not one
 * stage that ran was on screen; five that never ran were, in their default
 * `pending` state.
 *
 * Cause: `STAGE_ID_MAP` holds only the offline/deep vocabulary, and the mapper
 * did `if (!id) continue` — dropping every investigation stage silently while
 * the nine pre-seeded offline placeholders stayed. Same vocabulary-subset class
 * as the AI settings page that offered six providers when the backend accepted
 * seven; `continue` is what made it silent.
 *
 * Dropping an unknown name is still correct for THIS canvas (fixed node
 * positions, it cannot draw an arbitrary topology) — but it has to say so, so
 * the page can decline to render a graph that does not describe the pipeline.
 */
import { describe, expect, it } from 'vitest'

import type { AgentStageResult } from '@/types/agent'

import { mapPipelineToComputeGraph } from './mapping'

function stage(name: string, status = 'completed'): AgentStageResult {
  return { stage_name: name, status } as AgentStageResult
}

/** The seven stages the live investigation pipeline returned. */
const INVESTIGATION = [
  'investigator_plan',
  'hypothesis_commit',
  'hypothesis_known_flaky',
  'hypothesis_regression',
  'hypothesis_environment',
  'hypothesis_infra',
  'investigator_synthesis',
].map(n => stage(n))

/** A representative offline pipeline. */
const OFFLINE = [
  'ingestion',
  'anomaly_detection',
  'root_cause_analysis',
  'summary',
].map(n => stage(n))

describe('mapPipelineToComputeGraph — unmapped stages are reported', () => {
  it('recognises none of an investigation pipeline stages', () => {
    const g = mapPipelineToComputeGraph(INVESTIGATION)
    expect(g.recognised).toBe(0)
  })

  it('names every stage it could not place', () => {
    const g = mapPipelineToComputeGraph(INVESTIGATION)
    expect(g.unmapped).toHaveLength(INVESTIGATION.length)
    expect(g.unmapped).toContain('hypothesis_infra')
    expect(g.unmapped).toContain('investigator_synthesis')
  })

  it('counts the stages it did place on an offline pipeline', () => {
    const g = mapPipelineToComputeGraph(OFFLINE)
    expect(g.recognised).toBe(OFFLINE.length)
    expect(g.unmapped).toEqual([])
  })

  it('still renders the offline canvas for an offline pipeline', () => {
    // The reporting must not cost the thing that already worked.
    const g = mapPipelineToComputeGraph(OFFLINE)
    const done = g.stages.filter(s => s.status === 'done').map(s => s.id)
    expect(done).toContain('ingestion')
    expect(done).toContain('summary')
  })

  it('an empty stage list is not mistaken for a foreign pipeline', () => {
    // "Nothing reported yet" must keep the canvas — the page distinguishes
    // these two by `recognised > 0`, and an empty list would otherwise be
    // treated as the wrong topology on every first paint.
    const g = mapPipelineToComputeGraph([])
    expect(g.unmapped).toEqual([])
    expect(g.recognised).toBe(0)
    expect(g.stages.length).toBeGreaterThan(0)
  })

  it('reports a mixed pipeline honestly rather than all-or-nothing', () => {
    const g = mapPipelineToComputeGraph([stage('ingestion'), stage('hypothesis_infra')])
    expect(g.recognised).toBe(1)
    expect(g.unmapped).toEqual(['hypothesis_infra'])
  })
})
