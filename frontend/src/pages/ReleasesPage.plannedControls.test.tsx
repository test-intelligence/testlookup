/**
 * A control must not promise an action it cannot perform — BUG-006.
 *
 * Four `/releases` controls were styled exactly like the working controls
 * beside them and answered a click with a toast: *"<action> — coming in next
 * iteration"*. Two on the release card ("Clone from …", "Generate from PRD"),
 * two in the page header ("Export schedule", "Calendar view").
 *
 * The cost lands in the wrong place. The user reads the control as available,
 * decides to use it, clicks, and only then learns it does not exist — having
 * already spent the attention. Disabled-with-a-reason spends none of it, and
 * the reason stays on the control rather than in a toast that has vanished.
 *
 * The state is in the visible label, not only in `title`: a tooltip does not
 * exist on a touch device, and `title` is not reliably announced by screen
 * readers.
 *
 * The features are still wanted — `docs/BACKLOG.md` carries all four. This
 * changes how they are *advertised*, not whether they are planned, which is why
 * the label reads "(planned)" instead of the control being deleted: removing it
 * would drop the only signal that the capability is coming.
 *
 * Asserted against source rather than by mounting. `ReleaseCard` needs a
 * complete release object (`gate.decision`, `stage`, `phases`, `blockers`,
 * `metrics`) and the header lives inside the whole page; a fabricated fixture
 * would pin the fixture rather than the contract. Same call this repo makes in
 * `siblingInvalidation.test.ts`.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..')

const PLANNED = [
  ['components/releases/ReleaseCard.tsx', /Clone from|Generate from PRD/],
  ['pages/ReleasesPage.tsx', /Export schedule|Calendar view/],
] as const

function sourceOf(relative: string): string {
  return readFileSync(join(SRC, relative), 'utf-8')
}

/**
 * Each `<button …>` element, sliced at its OWN closing tag.
 *
 * Splitting on the next `<button` instead ran past `</button>` and swept in the
 * neighbouring markup, so a working control's `onClick` was attributed to the
 * disabled one beside it — the first version of this test failed for that
 * reason, not because the source was wrong.
 */
function buttonElements(src: string): string[] {
  const out: string[] = []
  let from = 0
  for (;;) {
    const start = src.indexOf('<button', from)
    if (start === -1) break
    const end = src.indexOf('</button>', start)
    if (end === -1) break
    out.push(src.slice(start, end))
    from = end + '</button>'.length
  }
  return out
}

/** Source with comments removed, so this fix's own explanation of the bug is
 *  not mistaken for the bug. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

describe('the planned /releases controls are honestly unavailable', () => {
  it('no control still answers a click with a stub toast', () => {
    // The guard for the whole class, not just the four known sites: a new stub
    // button shipped the same way fails here.
    for (const [file] of PLANNED) {
      expect(
        codeOnly(sourceOf(file)),
        `${file} still promises an action it cannot perform`,
      ).not.toMatch(/coming in next iteration/i)
    }
  })

  it.each(PLANNED)('%s marks its planned controls unavailable', (file, labels) => {
    const planned = buttonElements(sourceOf(file)).filter(b => labels.test(b))
    expect(planned, 'expected exactly the two known planned controls').toHaveLength(2)

    for (const b of planned) {
      // `opacity` and `cursor-not-allowed` only LOOK unavailable. The attribute
      // is what makes the click impossible and what a screen reader announces.
      expect(b, 'styled as unavailable but still clickable').toMatch(/\bdisabled\b/)
      expect(b, 'no reason given').toMatch(/title=/)
      expect(b, 'the state must be visible, not only on hover').toMatch(/\(planned\)/)
      expect(b, 'a disabled control must not also carry a click handler').not.toMatch(
        /onClick=/,
      )
    }
  })
})
