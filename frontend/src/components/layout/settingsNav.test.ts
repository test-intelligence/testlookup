/**
 * The settings sub-nav's data (UX redesign P5) against the routes it links to.
 *
 *  - Every item opens a page that is routed, with the role its route asks for
 *    (an item marked `all` must be a route every role can open; `management`
 *    one of App.tsx's management routes).
 *  - Every routed settings page and admin page is listed: a new
 *    `/settings/<thing>` route that nobody adds here fails, rather than ship
 *    unreachable except by URL (the 22-card index was the only list; now the
 *    sub-nav is).
 *  - Role filtering, the dev-only item, and which item a page is.
 */
import { describe, expect, it } from 'vitest'
import { activeSettingsItem, SETTINGS_GROUPS, settingsGroupsFor, settingsItemHref } from './settingsNav'
import { isSettingsAreaPath } from './settingsRoutes'

const APP = Object.values(
  import.meta.glob('../../App.tsx', { query: '?raw', import: 'default', eager: true }),
)[0] as string

/** The `path`s of one of App.tsx's route lists (`appRoutes`, `managementRoutes`). */
function routeList(name: string): string[] {
  const start = APP.indexOf(`const ${name}: AppRoute[] = [`)
  expect(start, `App.tsx declares ${name}`).toBeGreaterThan(-1)
  const end = APP.indexOf('\n]\n', start)
  return Array.from(APP.slice(start, end).matchAll(/path:\s*'([^']+)'/g), (m) => `/${m[1]}`)
}

const ALL_ROLES = new Set(routeList('appRoutes'))
const MANAGEMENT = new Set(routeList('managementRoutes'))
const ITEMS = SETTINGS_GROUPS.flatMap((g) => g.items)

describe('settings sub-nav: the routes it links to', () => {
  it('every item opens a routed page, behind the role its route asks for', () => {
    for (const item of ITEMS) {
      if (item.requires === 'all') expect(ALL_ROLES.has(item.to), `${item.id}: ${item.to} is a route every role can open`).toBe(true)
      else expect(MANAGEMENT.has(item.to), `${item.id}: ${item.to} is a management route`).toBe(true)
    }
  })

  it('lists every routed settings and admin page (a new one is added here, or it fails)', () => {
    const listed = new Set(ITEMS.map((i) => i.to))
    const area = [...ALL_ROLES, ...MANAGEMENT].filter(
      // A page of the area; not its parameterised children, the index itself, or a redirect.
      (path) => isSettingsAreaPath(path) && !path.includes(':') && path !== '/settings',
    )
    const missing = area.filter((path) => !listed.has(path))
    expect(missing, 'routed settings/admin pages with no item in settingsNav.ts').toEqual([])
  })

  it('ids are unique, and each item names what its page is for', () => {
    const ids = ITEMS.map((i) => i.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const item of ITEMS) expect(item.description.length, item.id).toBeGreaterThan(10)
  })
})

describe('settings sub-nav: who sees what', () => {
  it('a QA lead or admin sees all seven groups (§4), in order', () => {
    const groups = settingsGroupsFor({ canAccessManagement: true, isDev: false })
    expect(groups.map((g) => g.label)).toEqual([
      'Account', 'Project', 'Release governance', 'Integrations', 'AI', 'Security & access', 'System',
    ])
  })

  it('a viewer sees their own pages and the AI pipeline pages anyone can open, nothing that would redirect them', () => {
    const groups = settingsGroupsFor({ canAccessManagement: false, isDev: false })
    expect(groups.map((g) => [g.label, g.items.map((i) => i.id)])).toEqual([
      ['Account', ['profile', 'my-notifications']],
      ['AI', ['workflows', 'pipeline-runs']],
    ])
  })

  it('seed data is listed in development builds only', () => {
    const ids = (dev: boolean) => settingsGroupsFor({ canAccessManagement: true, isDev: dev }).flatMap((g) => g.items.map((i) => i.id))
    expect(ids(true)).toContain('seed-data')
    expect(ids(false)).not.toContain('seed-data')
  })
})

describe('settings sub-nav: which item a page is', () => {
  const active = (path: string, search = '') => activeSettingsItem(path, search)?.id ?? null

  it('the page itself, the longest route first', () => {
    expect(active('/settings/profile')).toBe('profile')
    expect(active('/agents')).toBe('pipeline-runs')
    expect(active('/agents/workflows')).toBe('workflows')
    expect(active('/projects')).toBe('projects')
  })

  it('one page with tabs: the tab picks its item, else the page’s own entry', () => {
    expect(active('/users')).toBe('users')
    expect(active('/users', '?tab=project-members')).toBe('members')
    expect(active('/users', '?tab=users')).toBe('users')
    expect(settingsItemHref(ITEMS.find((i) => i.id === 'members')!)).toBe('/users?tab=project-members')
  })

  it('the index and a page outside settings are no item', () => {
    expect(active('/settings')).toBeNull()
    expect(active('/runs')).toBeNull()
  })
})

describe('isSettingsAreaPath', () => {
  it('settings and the Admin item’s pages; not /policies (its home is Releases), not a lookalike prefix', () => {
    for (const path of ['/settings', '/settings/profile', '/projects', '/users', '/ownership', '/activity', '/agents', '/agents/workflows']) {
      expect(isSettingsAreaPath(path), path).toBe(true)
    }
    for (const path of ['/policies', '/settingsfoo', '/runs', '/releases', '/agentsx']) {
      expect(isSettingsAreaPath(path), path).toBe(false)
    }
  })
})
