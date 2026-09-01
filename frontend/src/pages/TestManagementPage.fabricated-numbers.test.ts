/**
 * Regression: the Test Management page invented the numbers in its right rail.
 *
 * Four separate fabrications, all rendered with the same confidence as the
 * real counts beside them:
 *
 *   "Req coverage 87%"  — covered / (covered + 0.15 * covered) reduces to
 *                         N / 1.15N for every project, every day. A constant,
 *                         presented as a measurement, in a verdict ribbon.
 *   "N requirements tracked" — TestLookup has no requirements entity, no
 *                         requirements table and no req-coverage endpoint. N
 *                         was the test-case count relabelled.
 *   uncoveredReqs / untestedBranches / defectsWithoutRegression
 *                       — 15%, 8% and 50% of numbers already on the page,
 *                         captioned "in prd:current" and "in main", neither of
 *                         which the page can see. These also drove
 *                         `disabled={count === 0}`, so a small library greyed
 *                         out working buttons.
 *   Library Health rates — computed over `useTestCases({size: 200})` while the
 *                         server's true total was printed in the same sentence.
 *
 * Asserted statically, matching the convention of the page's sibling specs
 * (`TestManagementPage.refresh.test.ts`): the page is ~4k lines with a large
 * hook surface, and "no rendered number is a ratio of another number on the
 * page" is a property of the source rather than of any one rendered state.
 */
import { describe, expect, it } from 'vitest'

import source from './TestManagementPage.tsx?raw'
import evidenceSource from '@/components/testManagement/EvidenceGapLists.tsx?raw'

/** Source with `//` and block comments stripped, so the prose ABOUT the removed
 *  fabrications (which deliberately quotes them) cannot satisfy a check. */
const code = `${source}\n${evidenceSource}`
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .split('\n')
  .filter((line) => !line.trim().startsWith('//'))
  .join('\n')

describe('TestManagementPage renders no fabricated numbers', () => {
  it('no longer synthesises an "uncovered requirements" bucket', () => {
    // The literal that made the coverage percentage a constant.
    expect(code).not.toMatch(/coverageUncov/)
    expect(code).not.toMatch(/fullList\.length\s*\*\s*0\.15/)
  })

  it('does not multiply a COUNT by a magic ratio to produce another count', () => {
    // 0.15 / 0.08 / 0.5 of `fullList.length` and `staleCount` were three
    // separate invented backlogs. Scoped to identifiers that name a count, so
    // the weighted health score (`automationScore * 0.35 + …`, a declared
    // weighting rather than a claimed measurement) stays legal.
    const scaled = [...code.matchAll(/\b(\w*(?:[Ll]ength|[Cc]ount|[Tt]otal))\s*\*\s*0?\.\d+/g)]
      .map((m) => m[0])
    expect(scaled, `a count scaled by a literal ratio: ${scaled.join(', ')}`).toEqual([])
  })

  it('does not label test cases as requirements', () => {
    expect(code).not.toMatch(/requirements tracked/i)
    expect(code).not.toMatch(/Req coverage/i)
    // The source refs the page cannot observe.
    expect(code).not.toMatch(/prd:current/i)
  })

  it('does not gate a generation button on a count it does not have', () => {
    expect(code).not.toMatch(/disabled=\{count === 0\}/)
    expect(code).not.toMatch(/uncoveredReqs|untestedBranches|defectsWithoutRegression/)
  })

  it('discloses when the library-health rates cover only a sample', () => {
    // The 200-row roll and the server total still coexist -- that is fine, as
    // long as the page says which one the percentages came from.
    expect(code).toMatch(/describeStatBasis\(/)
    expect(code).toMatch(/statBasis/)
  })

  it('still reports the automation split, which IS measured', () => {
    // A fix that deleted the card wholesale would satisfy every check above
    // while removing real information.
    expect(code).toMatch(/AutomationCoverageCard/)
    expect(code).toMatch(/coverageAuto\s*=\s*automatedCount/)
  })
})
