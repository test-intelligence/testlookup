/**
 * D-67 / S-C-3: a run is named by its build label; `Run #N` (a per-suite rank
 * that repeats in every suite) is only the secondary ordinal.
 */
import { describe, expect, it } from 'vitest'
import { runBuildLabel, runIdent, runSeqLabel } from './runOption'

describe('runOption', () => {
  it('names a run by its build label, the number second', () => {
    expect(runIdent({ id: 'a', build_number: 'cli-demo-1', run_seq: 1 })).toBe('cli-demo-1 · Run #1')
    expect(runIdent({ id: 'b', build_number: 'harness-batch-1', run_seq: 1 })).toBe('harness-batch-1 · Run #1')
  })

  it('reads a bare number as a build, and falls back to the short id', () => {
    expect(runBuildLabel({ id: 'x', build_number: 240 })).toBe('Build 240')
    expect(runBuildLabel({ id: '1a2b3c4d-0000', build_number: '' })).toBe('Run 1a2b3c4d')
    expect(runIdent({ id: '1a2b3c4d-0000', build_number: 'main-9', run_seq: null })).toBe('main-9')
  })

  it('has no ordinal for a run not numbered yet', () => {
    expect(runSeqLabel({ run_seq: null })).toBeNull()
    expect(runSeqLabel({ run_seq: 54 })).toBe('Run #54')
  })
})
