/**
 * A control must not promise an action it cannot perform — BUG-006, then UX
 * redesign P2/P5.
 *
 * Four `/releases` controls were styled exactly like the working controls
 * beside them and answered a click with a toast: *"<action> — coming in next
 * iteration"*. Two on the release card ("Clone from …", "Generate from PRD"),
 * two in the page header ("Export schedule", "Calendar view"). BUG-006 made
 * them disabled with a visible "(planned)" label. The redesign's rule is
 * stricter — what is not built is not rendered (P2) — and P5 item 5 drops
 * them: a disabled stub control is still a stub, and it took header room the
 * page's one real action ("New release") needed.
 *
 * Strategy: pull the source via Vite's `?raw` import, the same way
 * `AgentStatusPage.partial.test.tsx` does — `node:fs` and `__dirname` are not
 * typed in this tsconfig, and reaching for them fails `tsc --noEmit`. Comments
 * are removed first, so the explanation of the removal (above, and in the
 * sources) is not mistaken for the controls.
 */
import { describe, expect, it } from 'vitest'

import cardSource from '@/components/releases/ReleaseCard.tsx?raw'
import pageSource from './ReleasesPage.tsx?raw'

const SOURCES: ReadonlyArray<readonly [string, string, RegExp]> = [
  ['ReleaseCard.tsx', cardSource, /Clone from|Generate from PRD/],
  ['ReleasesPage.tsx', pageSource, /Export schedule|Calendar view/],
]

/** Source with comments removed. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
}

describe('the planned /releases controls are not rendered', () => {
  it('no control still answers a click with a stub toast', () => {
    for (const [name, src] of SOURCES) {
      expect(codeOnly(src), `${name} promises an action it cannot perform`).not.toMatch(/coming in next iteration/i)
    }
  })

  it.each(SOURCES)('%s renders none of its former planned controls', (_name, src, labels) => {
    const code = codeOnly(src)
    expect(code).not.toMatch(labels)
    expect(code, 'a "(planned)" label is a stub').not.toMatch(/\(planned\)/)
    expect(code, 'a disabled control with a "Not built yet" reason is a stub').not.toMatch(/Not built yet/)
  })
})
