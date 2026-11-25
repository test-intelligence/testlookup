/**
 * Batch 6 (2026-11-24): the Releases guide states what the screens offer.
 *
 * - D-137: the guide listed four rule types; **+ Add Rule** in the policy
 *   editor offers five (Performance regression, EXEC-05). Every label the
 *   editor offers must appear in the guide.
 * - D-115: the guide quoted one "Drafted by rules — …" reason as if it were
 *   the only one. Every reason the badge can print must appear in the guide.
 * - Release-level Conditional Go: the release and phase gates decide Go, No-Go
 *   or Not evaluated (backend `release_rollup_service.decide`), so the release
 *   list's verdicts must not include Conditional Go.
 */
import { describe, expect, it } from 'vitest'

import { FALLBACK_REASON_TEXT } from '@/components/releases/draftPhasesModel'

import { DOC_SOURCES } from './sources'

const EDITOR = Object.values(
  import.meta.glob('/src/pages/PolicyEditorPage.tsx', { query: '?raw', import: 'default', eager: true }),
)[0] as string

/** The labels in `const RULE_TYPES = [...]`, the list **+ Add Rule** renders. */
function ruleTypeLabels(source: string): string[] {
  const block = /const RULE_TYPES = \[([\s\S]*?)\n\];/.exec(source)?.[1] ?? ''
  return [...block.matchAll(/label: '([^']+)'/g)].map((m) => m[1])
}

const releases = DOC_SOURCES['releases'] ?? ''

describe('Releases guide matches the policy editor and the draft badge', () => {
  it('reads the editor list (the scan finds the rule types)', () => {
    const labels = ruleTypeLabels(EDITOR)
    expect(labels).toContain('Flaky Recurrence Limit')
    expect(labels.length).toBeGreaterThanOrEqual(5)
  })

  it('names every rule type + Add Rule offers (D-137)', () => {
    const missing = ruleTypeLabels(EDITOR).filter((label) => !releases.includes(`**${label}**`))
    expect(missing).toEqual([])
  })

  it('does not say Override Restrictions takes an action (D-137)', () => {
    expect(releases).not.toMatch(/Override Restrictions\*\*, each with an action/)
    expect(releases).toContain('**Override Restrictions** has no action')
  })

  it('lists every reason the "Drafted by rules" badge can give (D-115)', () => {
    const missing = Object.values(FALLBACK_REASON_TEXT).filter((reason) => !releases.includes(`\`${reason}\``))
    expect(missing).toEqual([])
  })

  it('does not list Conditional Go among a release gate verdicts', () => {
    const listLine = releases.split('\n').find((line) => line.includes("each release's **recorded** gate verdict")) ?? ''
    expect(listLine).not.toBe('')
    expect(listLine.split('.')[0]).not.toMatch(/Conditional/)
    expect(DOC_SOURCES['concepts']).toContain('A release gate has no CONDITIONAL_GO')
  })
})
