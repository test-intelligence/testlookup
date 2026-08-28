/**
 * Regression: headline copy pluralised the NOUN by count but left the VERB
 * plural, so the singular path — the common one — read wrong:
 *
 *   "1 failing run need investigation."   (IntelligenceHubPage)
 *   "1 dimension need attention"          (CoveragePage)
 *
 * Both were found by exploratory testing against a project with exactly one
 * failing run. The author had clearly thought about pluralisation — the
 * `${n === 1 ? '' : 's'}` on the noun is right there — which is what makes the
 * shape easy to miss in review: it looks handled.
 *
 * This guards the class rather than the two strings. It is deliberately narrow:
 * only verbs whose singular form differs, so it cannot fire on a noun that
 * happens to precede a preposition ("2 runs with failures", "1 run in the last
 * hour"), which is what most of the ~155 uses of this idiom do.
 *
 * A compound subject legitimately takes a plural verb ("1 flaky and 1 broken
 * run warrant a glance"), so `warrant` is not in the list.
 */
import { describe, expect, it } from 'vitest'

const pageSources = import.meta.glob('./**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

// Verbs that inflect for number. A bare one of these straight after a
// count-pluralised noun means the verb was left plural while the noun was not.
const PLURAL_VERBS = ['need', 'have', 'are', 'were', 'require', 'remain', 'appear']

describe('count-driven copy agrees in number', () => {
  it('reads the page sources it is meant to guard', () => {
    // A glob matching nothing would make this pass forever.
    expect(Object.keys(pageSources).length).toBeGreaterThan(20)
  })

  it('never follows a count-pluralised noun with a bare plural verb', () => {
    const pattern = new RegExp(
      // `${n === 1 ? '' : 's'}` then a word, then one of the verbs
      "=== 1 \\? '' : 's'\\}\\s*[A-Za-z]*\\s+(" + PLURAL_VERBS.join('|') + ")\\b",
    )
    const offenders: string[] = []

    for (const [file, src] of Object.entries(pageSources)) {
      if (file.endsWith('.test.tsx')) continue
      for (const line of src.split(/\r?\n/)) {
        if (pattern.test(line)) offenders.push(`${file}: ${line.trim().slice(0, 140)}`)
      }
    }

    expect(
      offenders,
      'pluralise the verb too, e.g. `${n === 1 ? "needs" : "need"}`',
    ).toEqual([])
  })
})
