/**
 * A control must not promise an action it cannot perform — BUG-006, then UX
 * redesign P2/P5, then W11.
 *
 * Four `/releases` controls were styled exactly like the working controls
 * beside them and answered a click with a toast: *"<action> — coming in next
 * iteration"*. Two on the release card (clone from another release, generate
 * from a PRD), two in the page header (export the schedule, a calendar).
 * BUG-006 made them disabled with a visible "(planned)" label; the redesign's
 * rule is stricter — what is not built is not rendered (P2) — and P5 item 5
 * dropped them.
 *
 * W11 built all four (REL-05, REL-07, REL-06). The rule this file keeps is
 * the same one, stated positively: each former planned control renders only
 * with a WORKING handler. Every occurrence of its label (in the code, not in a
 * comment) has an `onClick` in the element or the `OverflowItem` literal that
 * carries it, within 300 characters — and the stub patterns stay forbidden. The old labels stay retired (the new ones say what they do:
 * "Copy phases from…", "Draft from a PRD…", "Export schedule (.ics)", "Calendar").
 *
 * Strategy: pull the source via Vite's `?raw` import, the same way
 * `AgentStatusPage.partial.test.tsx` does — `node:fs` and `__dirname` are not
 * typed in this tsconfig, and reaching for them fails `tsc --noEmit`. Comments
 * are removed first, so the explanation of a control (above, and in the
 * sources) is not mistaken for the control.
 */
import { describe, expect, it } from 'vitest'

import cardSource from '@/components/releases/ReleaseCard.tsx?raw'
import pageSource from './ReleasesPage.tsx?raw'

/** Source with comments removed. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
}

const SOURCES: ReadonlyArray<readonly [string, string]> = [
  ['ReleaseCard.tsx', cardSource],
  ['ReleasesPage.tsx', pageSource],
]

/**
 * The former planned controls and where each must be built. A label is
 * matched where it is RENDERED: JSX text (`>Calendar<`) or a string literal
 * (`'Export schedule (.ics)'`), never as part of an identifier
 * (`ReleaseCalendar`).
 */
const BUILT: ReadonlyArray<readonly [file: string, label: string, src: string, pattern: RegExp]> = [
  ['ReleaseCard.tsx', 'Copy phases from…', cardSource, />\s*Copy phases from…\s*</g],
  ['ReleaseCard.tsx', 'Draft from a PRD…', cardSource, />\s*Draft from a PRD…\s*</g],
  ['ReleasesPage.tsx', 'Export schedule', pageSource, /['"`]Export schedule[^'"`]*['"`]|>\s*Export schedule[^<]*</g],
  ['ReleasesPage.tsx', 'Calendar', pageSource, /['"`]Calendar['"`]|>\s*Calendar\s*</g],
]

const WINDOW = 300

/**
 * The code that carries a label: for JSX text, the element's opening tag
 * (from its `<` to the `>` before the label); for a string literal, the
 * object literal around it (an `OverflowItem`). Never more than `WINDOW`
 * characters either side, so a handler of a NEIGHBOURING control cannot vouch
 * for this one.
 */
function carrier(code: string, at: number, length: number): string {
  if (code[at] === '>') {
    const open = code.lastIndexOf('<', at)
    return code.slice(Math.max(open, at - WINDOW), at)
  }
  const from = code.lastIndexOf('{', at)
  const to = code.indexOf('}', at + length)
  return code.slice(Math.max(from, at - WINDOW), Math.min(to === -1 ? code.length : to, at + length + WINDOW))
}

describe('the former planned /releases controls render only with a working handler', () => {
  it('no control answers a click with a stub toast, and no stub label is rendered', () => {
    for (const [name, src] of SOURCES) {
      const code = codeOnly(src)
      expect(code, `${name} promises an action it cannot perform`).not.toMatch(/coming in next iteration/i)
      expect(code, `${name}: a "(planned)" label is a stub`).not.toMatch(/\(planned\)/)
      expect(code, `${name}: a disabled control with a "Not built yet" reason is a stub`).not.toMatch(/Not built yet/)
    }
  })

  it('the retired labels stay retired', () => {
    expect(codeOnly(cardSource)).not.toMatch(/Clone from|Generate from PRD/)
    expect(codeOnly(pageSource)).not.toMatch(/Calendar view|Clone from|Generate from PRD/)
  })

  it.each(BUILT)('%s renders "%s", and every occurrence carries an onClick', (name, label, src, pattern) => {
    const code = codeOnly(src)
    const hits = [...code.matchAll(pattern)]
    expect(hits.length, `${name} no longer renders "${label}"`).toBeGreaterThan(0)
    for (const hit of hits) {
      const at = hit.index ?? 0
      expect(carrier(code, at, hit[0].length), `${name}: "${label}" at ${at} has no handler of its own`).toMatch(/onClick/)
    }
  })
})
