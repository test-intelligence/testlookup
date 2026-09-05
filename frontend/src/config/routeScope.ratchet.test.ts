/**
 * The ratchet: the registry must keep describing the code.
 *
 * A registry that drifts is worse than no registry — a link asks it whether a
 * destination works, gets "yes", and offers the dead end anyway, which is the
 * original bug wearing a safety label. So this walks the real route table in
 * `App.tsx`, resolves each route to its page source, and checks the declaration
 * against what the page actually renders, in both directions.
 *
 * Three failures it is built to catch:
 *
 *   1. A NEW project-gated page nobody declared. It renders the shared prompt,
 *      the registry does not list it, so every link to it looks like it works.
 *   2. A STALE entry. The page learned to handle All Projects and the registry
 *      still says otherwise, so links to it carry a qualifier they no longer
 *      need — and readers learn to ignore the qualifier.
 *   3. A hand-rolled guard. Someone copies the pre-2026-09-05 shape,
 *      `if (isAllProjects) return <EmptyState .../>`, which skips the picker
 *      and is invisible to check 1. That is precisely how six pages ended up
 *      telling the reader to go and find the top bar.
 *
 * Reading source text is a blunt instrument, and its limits are stated at each
 * check rather than left for a reader to discover.
 */
import { describe, expect, it } from 'vitest'
import { singleProjectRoutes } from './routeScope'

/**
 * Sources as text, through Vite rather than `node:fs`.
 *
 * `tsconfig.json` sets `types: ["vite/client"]` and does not pull in
 * `@types/node`, so a `readFileSync` here type-checks under vitest and then
 * breaks `npm run build` (which is `tsc && vite build`) — green locally, red in
 * CI. `src/formLabels.test.ts` already reads page sources this way.
 */
const SOURCES: Record<string, string> = {
  // Two narrow globs rather than `../**/*.tsx`: the wide form eagerly inlines
  // every component AND every test file as a string, which is a lot of bundle
  // for a check that only ever reads the route table and the pages it names.
  //
  // The options MUST be written out at each call. Vite parses these
  // statically and rejects a shared `const` with "Expected the second argument
  // to be an object literal" — and it fails at COLLECTION, so the file
  // contributes zero tests while the run still reports every other test as
  // passing.
  ...(import.meta.glob('../App.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>),
  ...(import.meta.glob('../pages/**/*.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>),
  // Components too. The two worst instances of the reported bug were in
  // `FirstRunGuide` — the onboarding panel shown to a brand-new user, whose
  // project selection is the ALL_PROJECTS_ID default — and neither was in a
  // page file.
  ...(import.meta.glob('../components/**/*.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>),
}

function source(relative: string): string {
  const src = SOURCES[`../${relative}.tsx`]
  expect(src, `no source globbed for ${relative}.tsx`).toBeTruthy()
  return src
}

/** Component identifier → page module path, from the lazy() declarations. */
function lazyImports(appSource: string): Map<string, string> {
  const out = new Map<string, string>()
  const re = /const (\w+) = lazy\(\(\) => import\('@\/(pages\/[\w/-]+)'\)\)/g
  for (const m of appSource.matchAll(re)) out.set(m[1], m[2])
  return out
}

/** Route path → page module path, for every route the app registers. */
function routeToModule(): Map<string, string> {
  const app = source('App')
  const imports = lazyImports(app)
  expect(
    imports.size,
    'no lazy() page imports parsed out of App.tsx — the import shape changed ' +
      'and this ratchet is now checking nothing',
  ).toBeGreaterThan(20)

  const out = new Map<string, string>()
  const re = /\{\s*path:\s*'([^']+)',\s*component:\s*(\w+)\s*\}/g
  for (const m of app.matchAll(re)) {
    const mod = imports.get(m[2])
    // Routes with params (`runs/:runId`) share a module with their base route;
    // keeping the first mapping is enough, since scope is per page.
    if (mod && !out.has(`/${m[1]}`)) out.set(`/${m[1]}`, mod)
  }
  expect(
    out.size,
    'no routes parsed out of App.tsx — the route-table shape changed',
  ).toBeGreaterThan(20)
  return out
}

const readPage = (mod: string) => source(mod)

/**
 * Files allowed to link to a project-gated route without ScopedLink.
 *
 * The Sidebar is the primary navigation: it lists every destination in the app,
 * and a reader clicking "Flaky Coach" there is browsing, not following a
 * contextual promise attached to a number. Qualifying every nav item would
 * clutter the one surface that has to stay scannable — and the destination
 * now offers a picker, so the link works. Exempted explicitly, with the reason,
 * rather than quietly not checked.
 */
const EXEMPT_FROM_SCOPED_LINK = ['components/layout/Sidebar.tsx']

/** Does this page render the shared "needs one project" prompt? */
const rendersPrompt = (src: string) => src.includes('<ProjectRequiredEmptyState')

/**
 * A hand-rolled All-Projects guard that renders a plain EmptyState.
 *
 * Scoped to the ~14 lines after the guard so an unrelated EmptyState elsewhere
 * in a long page is not blamed. Both brace styles, because the no-brace form is
 * the one someone writes when adding a quick guard.
 */
function handRolledGuard(src: string): boolean {
  const lines = src.split('\n')
  return lines.some((line, i) => {
    if (!/^\s*if \(isAllProjects\)\s*\{?\s*$/.test(line)) return false
    return lines.slice(i, i + 14).some(l => l.includes('<EmptyState'))
  })
}

describe('route scope registry — held to the code', () => {
  const routes = routeToModule()
  const declared = new Set(singleProjectRoutes())

  it('declares every route whose page renders the project-required prompt', () => {
    const undeclared: string[] = []
    for (const [route, mod] of routes) {
      if (rendersPrompt(readPage(mod)) && !declared.has(route)) {
        undeclared.push(`${route}  (${mod}.tsx)`)
      }
    }
    expect(
      undeclared,
      'these pages refuse to render in All Projects mode but are not declared ' +
        "in config/routeScope.ts, so every link to them looks like it works:\n" +
        undeclared.join('\n'),
    ).toEqual([])
  })

  it('declares nothing that has stopped being project-gated', () => {
    const stale: string[] = []
    for (const route of declared) {
      const mod = routes.get(route)
      if (!mod) {
        stale.push(`${route}  (no such route in App.tsx)`)
        continue
      }
      if (!rendersPrompt(readPage(mod))) stale.push(`${route}  (${mod}.tsx)`)
    }
    expect(
      stale,
      'these routes are declared single-project but their pages no longer ' +
        'render the prompt, so links to them carry a qualifier they do not ' +
        `need:\n${stale.join('\n')}`,
    ).toEqual([])
  })

  it('has no page hand-rolling the guard it used to hand-roll', () => {
    // The shape that produced the reported bug: a bare EmptyState behind
    // `if (isAllProjects)`, with no picker on it and nothing a link could ask.
    const handRolled: string[] = []
    for (const [route, mod] of routes) {
      if (handRolledGuard(readPage(mod))) handRolled.push(`${route}  (${mod}.tsx)`)
    }
    expect(
      handRolled,
      'use ProjectRequiredEmptyState instead — a bare EmptyState behind this ' +
        'guard leaves the reader with nothing to press and is invisible to ' +
        `the registry:\n${handRolled.join('\n')}`,
    ).toEqual([])
  })

  it('detects a hand-rolled guard when it sees one', () => {
    // The positive control. Without it, a detector broken by a formatting
    // change would report a clean sweep forever — the "test that cannot fail"
    // shape these guards exist to prevent.
    const planted = [
      'export default function Page() {',
      '  if (isAllProjects) {',
      '    return (',
      '      <EmptyState title="Select a project" />',
      '    )',
      '  }',
      '}',
    ].join('\n')
    expect(handRolledGuard(planted)).toBe(true)

    const migrated = planted.replace(
      '<EmptyState title="Select a project" />',
      '<ProjectRequiredEmptyState description="x" />',
    )
    expect(handRolledGuard(migrated)).toBe(false)
  })

  it('routes every link to a project-gated page through ScopedLink', () => {
    // The check that finds the next one. `OverviewPage` was the reported
    // instance; running this for the first time also turned up two in
    // `FirstRunGuide`, including "Generate a project key under Settings → API
    // Keys" — a step in the SETUP INSTRUCTIONS shown to a brand-new user,
    // pointing at a page that refuses to render in the state that user is in.
    const offenders: string[] = []
    for (const [file, src] of Object.entries(SOURCES)) {
      if (EXEMPT_FROM_SCOPED_LINK.some(e => file.endsWith(e))) continue
      for (const route of declared) {
        // `[^>]*` rather than a `\s` escape, for two reasons. It spans
        // NEWLINES, and the worst instance found was multi-line:
        //
        //     <Link
        //       to="/settings/api-keys"
        //
        // A single-line pattern would have reported the app clean while the
        // setup instructions still pointed into a dead end. And it needs no
        // backslash, which is what mangled this file on the first attempt.
        //
        // `<Link` cannot match inside `<ScopedLink` — the character before
        // `Link` there is `d`, not `<`.
        const plainLink = new RegExp(`<Link[^>]*to="${route}"`)

        // A route passed as a PROP (`linkTo="/flaky-coach"`) is rendered by
        // some other component, and text cannot say which. A file that renders
        // ScopedLink anywhere has the mechanism, so its prop-passed routes are
        // treated as scoped — that is exactly how `OverviewPage` passes
        // `linkTo` down to its local `KpiCard`, which now renders a ScopedLink.
        //
        // The honest limit: a route reaching a plain `<Link to={variable}>` in
        // a component that never imports ScopedLink is invisible here. The
        // reported bug was that shape, which is why the fix put the check into
        // the LINK component rather than relying on this test to find them all.
        const propLink =
          new RegExp(`linkTo="${route}"`).test(src) && !src.includes('<ScopedLink')

        if (plainLink.test(src) || propLink) {
          offenders.push(`${file}  ->  ${route}`)
        }
      }
    }
    expect(
      offenders,
      [
        'these link straight to a page that cannot render while All Projects',
        'is active, with nothing to warn the reader. Use ScopedLink:',
        ...offenders,
      ].join(' | '),
    ).toEqual([])
  })

  it('detects an unscoped link when it sees one', () => {
    // Positive control for the regex above — a detector that silently stopped
    // matching would report a clean app forever.
    const route = '/flaky-coach'
    const re = new RegExp(`<Link[^>]*to="${route}"`)

    expect(re.test(`<Link to="${route}" className="btn">Flaky coach</Link>`)).toBe(
      true,
    )
    // The multi-line form, which is how the API-keys instance was written.
    expect(re.test(`<Link\n  to="${route}"\n  className="x"\n>`)).toBe(true)
    // ScopedLink must NOT trip it, or the check reports the fix as the bug.
    expect(
      re.test(`<ScopedLink to="${route}" className="btn">Flaky coach</ScopedLink>`),
    ).toBe(false)
    // A different route must not trip it either.
    expect(re.test('<Link to="/runs" className="btn">Runs</Link>')).toBe(false)
  })

  it('resolves the reported route to the page that caused the report', () => {
    // Anchors the App.tsx parsing itself: if the route table or the lazy-import
    // shape changes, every check above quietly starts iterating an empty map,
    // and this is the assertion that notices.
    expect(routes.get('/flaky-coach')).toBe('pages/FlakyCoachPage')
    expect(routes.get('/settings/retention')).toBe('pages/settings/RetentionPage')
  })
})
