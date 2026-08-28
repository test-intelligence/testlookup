/**
 * Every form control must have an accessible name.
 *
 * Forms across this app wrote label and control as unconnected siblings —
 *
 *     <label className="...">SMTP Host</label>
 *     <input type="text" value={host} … />
 *
 * — no `htmlFor`, no `id`, no `aria-label`. The field looks labelled and has no
 * accessible name: `getByRole('textbox', { name: 'SMTP Host' })` matches
 * nothing, and a screen reader announces an unnamed edit box. 46 controls in
 * the settings pages and 78 more across pages/components were in that state,
 * including every password and API-token input.
 *
 * Four shapes are accepted, because all four are correct:
 *   1. `<label htmlFor={id}>` paired with a control carrying that id;
 *   2. a label that WRAPS its control (`<label><input …/> Text</label>`);
 *   3. a label wrapping a control that names itself (`role="switch"` button
 *      with `aria-label`) — the label is then decorative;
 *   4. a label sitting before a COMPONENT, which cannot be wired by id from the
 *      call site. Naming those groups wants `role="group"` + `aria-labelledby`;
 *      that is a real but separate fix and this guard does not claim it.
 *
 * Comments are stripped first. An earlier version scanned raw text and flagged
 * its own doc comment; another scanned fixed line windows and reported two
 * files that were already correct. A guard that reports correct code is barely
 * better than a blind one — it pressures you into "fixing" what was right.
 */
import { describe, expect, it } from 'vitest'

const sources = {
  ...(import.meta.glob('./pages/**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>),
  ...(import.meta.glob('./components/**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>),
}

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')
}

describe('form labels are wired to their controls', () => {
  it('reads the sources it is meant to guard', () => {
    // A glob matching nothing would make this pass forever.
    expect(Object.keys(sources).length).toBeGreaterThan(50)
  })

  it('has no <label> without an accessible association', () => {
    const offenders: string[] = []

    for (const [file, raw] of Object.entries(sources)) {
      if (file.endsWith('.test.tsx')) continue
      const src = stripComments(raw)

      let from = 0
      for (;;) {
        const at = src.indexOf('<label', from)
        if (at === -1) break
        from = at + 6

        const openEnd = src.indexOf('>', at)
        if (openEnd === -1) break
        if (src.slice(at, openEnd + 1).includes('htmlFor')) continue

        const closeAt = src.indexOf('</label>', openEnd)
        const inner = closeAt === -1 ? '' : src.slice(openEnd, closeAt)
        if (/<(input|select|textarea)\b/.test(inner)) continue
        if (/aria-label=/.test(inner)) continue

        const after = src.slice(closeAt, closeAt + 200)
        if (/<\s*[A-Z]/.test(after) && !/<\s*(input|select|textarea)\b/.test(after)) continue

        const line = src.slice(0, at).split(/\r?\n/).length
        offenders.push(`${file}:${line}`)
      }
    }

    expect(
      offenders,
      'wire the label to its control — <label htmlFor={id}> + <input id={id}>, or use components/ui/Field',
    ).toEqual([])
  })

  it('every htmlFor and aria-labelledby resolves to exactly one id', () => {
    // A dangling or duplicated target is worse than no label: it points
    // assistive tech at nothing, or at the wrong control.
    const problems: string[] = []

    for (const [file, raw] of Object.entries(sources)) {
      if (file.endsWith('.test.tsx')) continue
      const src = stripComments(raw)
      const ids = [...src.matchAll(/\bid="([^"]+)"/g)].map(m => m[1])
      const count = (t: string) => ids.filter(x => x === t).length

      for (const m of src.matchAll(/(?:htmlFor|aria-labelledby)="([^"]+)"/g)) {
        const n = count(m[1])
        if (n !== 1) problems.push(`${file}: "${m[1]}" resolves to ${n} ids`)
      }
    }

    expect(problems, 'each label target must match exactly one id in its file').toEqual([])
  })
})
