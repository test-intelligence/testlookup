/**
 * The chart specs' accessibility gate: axe at EVERY impact, under every WCAG
 * level the product claims plus axe's own best practices, with a per-rule,
 * per-node allowlist that is RATCHETED.
 *
 * One copy, used by the chart gallery spec and the VIZ-405 trend-overlay spec.
 * The trend spec needed the same gate over the same themes, and a second copy
 * of it would be free to drift — a tag dropped from one, a softer filter in the
 * other — while both stayed green.
 */
import { expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

/**
 * The rules the gate runs. Every WCAG level this product claims, plus axe's
 * own best practices.
 *
 * It used to run axe's DEFAULT rule set and then keep only `serious` and
 * `critical` findings. That is two filters at once, and both of them hide
 * exactly the kind of defect a chart has: `heading-order`, a duplicated
 * accessible name, a `tabindex` on something with no role and an unnamed
 * `role="application"` are all `moderate` or `minor`, and several of them
 * are best-practice rules the default tag set never even ran. This gate
 * reports EVERY impact under the full tag set; the allowlist is the only
 * escape, it is per rule AND per node, and it is ratcheted.
 */
export const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] as const

/** Every theme the app ships: a contrast regression can hide in any one of them. */
export const ALL_THEMES = ['signal', 'console', 'slate', 'ember', 'lab', 'midnight'] as const

/** One tolerated violation: a rule, in one theme, on the node whose HTML contains this text. */
export interface KnownViolation {
  theme: string
  rule: string
  nodeHtmlIncludes: string
}

/**
 * No axe violation, at any impact, except an allowlisted one — and every
 * allowlisted one must still fire (the ratchet), so a fix shrinks the list.
 * A green run therefore means "no NEW violation", never "none".
 *
 * `include` narrows the audit to those selectors (a spec auditing one item in
 * a state the page-wide audit never reaches); left out, the whole page is
 * audited.
 */
export async function expectNoBlockingViolations(
  page: Page,
  theme: string,
  known: readonly KnownViolation[],
  include: readonly string[] = [],
) {
  let builder = new AxeBuilder({ page }).withTags([...AXE_TAGS])
  for (const selector of include) builder = builder.include(selector)
  const result = await builder.analyze()
  const isKnown = (rule: string, html: string) =>
    known.some((entry) => entry.rule === rule && html.includes(entry.nodeHtmlIncludes))

  const blocking = result.violations
    .map((violation) => ({
      id: `${violation.id} [${violation.impact}]`,
      nodes: violation.nodes.filter((node) => !isKnown(violation.id, node.html)).map((node) => node.html),
    }))
    .filter((violation) => violation.nodes.length > 0)
  expect(blocking, `axe (${theme})`).toEqual([])

  // The ratchet: an allowlisted violation that no longer occurs is a fix,
  // and the allowlist must shrink with it.
  for (const entry of known) {
    const stillPresent = result.violations.some(
      (violation) =>
        violation.id === entry.rule && violation.nodes.some((node) => node.html.includes(entry.nodeHtmlIncludes)),
    )
    expect(
      stillPresent,
      `${entry.rule} on "${entry.nodeHtmlIncludes}" (${theme}) no longer fires — remove it from KNOWN_VIOLATIONS`,
    ).toBe(true)
  }
}
