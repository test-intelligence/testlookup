/**
 * Regression: settings forms wrote label and control as unconnected siblings —
 *
 *     <label className="...">SMTP Host</label>
 *     <input type="text" value={host} … />
 *
 * — no `htmlFor`, no `id`, no `aria-label`. The field looked labelled and had
 * no accessible name: `getByRole('textbox', { name: 'SMTP Host' })` matched
 * nothing, and a screen reader announced an unnamed edit box.
 *
 * Measured live before the fix: **46 of 61** controls across the settings pages,
 * including every password and API-token input.
 *
 * A label is acceptable in two shapes, and this guard allows both:
 *   1. `<label htmlFor={id}>` paired with a control carrying that id, or
 *   2. a label that WRAPS its control (`<label><input …/> Text</label>`), which
 *      is how the Toggle helpers do it.
 *
 * Parsed over the raw text rather than line by line: a first version scanned
 * fixed line windows and produced two false positives — an opening tag with
 * `htmlFor` on its own line, and a wrapping label whose input sat just past the
 * window. A guard that reports correct code is not much better than a blind one,
 * since it pressures you into "fixing" what was already right.
 */
import { describe, expect, it } from 'vitest'

const sources = import.meta.glob('./*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

describe('settings form labels are wired to their controls', () => {
  it('reads the settings pages it is meant to guard', () => {
    // A glob matching nothing would make this pass forever.
    expect(Object.keys(sources).length).toBeGreaterThanOrEqual(5)
  })

  it('has no <label> that neither carries htmlFor nor wraps its control', () => {
    const offenders: string[] = []

    for (const [file, src] of Object.entries(sources)) {
      if (file.endsWith('.test.tsx')) continue

      let from = 0
      for (;;) {
        const at = src.indexOf('<label', from)
        if (at === -1) break
        from = at + 6

        const openEnd = src.indexOf('>', at)
        if (openEnd === -1) break
        const openTag = src.slice(at, openEnd + 1)
        if (openTag.includes('htmlFor')) continue

        const closeAt = src.indexOf('</label>', openEnd)
        const inner = closeAt === -1 ? '' : src.slice(openEnd, closeAt)
        // A label that wraps its own control is correctly associated.
        if (/<(input|select|textarea)\b/.test(inner)) continue
        // A wrapped custom control that names itself (a role="switch" button
        // carrying aria-label) is already accessible; the label is decorative.
        if (/aria-label=/.test(inner)) continue

        // A label sitting before a COMPONENT (<EventCheckboxes …/>) cannot be
        // wired by id from here. Naming that group wants role="group" +
        // aria-labelledby — a real but different fix. This guard is about raw
        // controls with no accessible name, so it does not claim that one.
        const after = src.slice(closeAt, closeAt + 200)
        if (/<\s*[A-Z]/.test(after) && !/<\s*(input|select|textarea)\b/.test(after)) continue

        const line = src.slice(0, at).split(/\r?\n/).length
        offenders.push(`${file}:${line}: ${openTag.replace(/\s+/g, ' ').slice(0, 100)}`)
      }
    }

    expect(
      offenders,
      'wire the label to its control — <label htmlFor={id}> + <input id={id}>, or the shared components/ui/Field',
    ).toEqual([])
  })
})
