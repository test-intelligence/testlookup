import { describe, expect, it } from 'vitest'
import { COMPARE_RUNS_PATH, compareRunsHref, latestTwoHref } from './compareHref'

/** The query a href carries, read the way `RunComparePage` reads it. */
const query = (href: string) => new URLSearchParams(href.split('?')[1] ?? '')

describe('compareRunsHref (TRND-02)', () => {
  it('one suite selected: the latest two runs of that suite', () => {
    const { href, reason } = compareRunsHref({ suite: 'Auth', allProjects: false })
    expect(href).toBe('/runs/compare?mode=latest&suite=Auth')
    expect(reason).toBeNull()
  })

  it('no suite (or a blank one): the manual form', () => {
    expect(compareRunsHref({ suite: null, allProjects: false })).toEqual({ href: COMPARE_RUNS_PATH, reason: null })
    expect(compareRunsHref({ suite: '   ', allProjects: false })).toEqual({ href: COMPARE_RUNS_PATH, reason: null })
  })

  it('All Projects: no href, and the reason (latest mode needs one project)', () => {
    expect(compareRunsHref({ suite: 'Auth', allProjects: true })).toEqual({ href: null, reason: 'pick a project' })
    expect(compareRunsHref({ suite: null, allProjects: true }).href).toBeNull()
  })

  it.each(['a&b #c', 'pay ments/v2?x=1', 'ünïcode + plus', '%41 literal'])(
    'a suite named %j round-trips through URLSearchParams as ONE suite value',
    (suite) => {
      const href = latestTwoHref(suite)
      const params = query(href)
      expect(params.get('mode')).toBe('latest')
      expect(params.getAll('suite')).toEqual([suite])
      expect([...params.keys()].sort()).toEqual(['mode', 'suite'])
      expect(href.includes('#')).toBe(false)
      expect(compareRunsHref({ suite, allProjects: false }).href).toBe(href)
    },
  )
})
