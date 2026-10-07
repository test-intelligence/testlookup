/**
 * The Test Cases page's sections (UX redesign P4 item 7): eight tabs became
 * four plus a "More ▾" menu. `?tab=` holds the id.
 */

export const TM_PRIMARY_TABS = [
  { id: 'cases', label: 'Cases' },
  { id: 'suites', label: 'Suites' },
  { id: 'plans', label: 'Plans' },
  { id: 'approvals', label: 'Approvals' },
] as const

/** The "More ▾" menu, in its order. */
export const TM_MORE_TABS = [
  { id: 'strategy', label: 'Strategy' },
  { id: 'duplicates', label: 'Duplicates' },
  { id: 'audit', label: 'Audit log' },
  { id: 'knowledge', label: 'Knowledge' },
] as const

export type TmTab = (typeof TM_PRIMARY_TABS)[number]['id'] | (typeof TM_MORE_TABS)[number]['id']

export const TM_TAB_IDS: readonly TmTab[] = [...TM_PRIMARY_TABS, ...TM_MORE_TABS].map((t) => t.id)

export const isMoreTab = (tab: TmTab): boolean => TM_MORE_TABS.some((t) => t.id === tab)

/**
 * The `?tab=` values links in the wild still carry: the old tab labels
 * (`?tab=Test+Suites&suite=<name>` from Runs, Live, Deep Investigation and
 * Intelligence; `?tab=Test+Cases` in probes), and `reviews` for the tab that
 * is now Approvals. Each reads as the tab that has the content now.
 */
const ALIASES: ReadonlyMap<string, TmTab> = new Map<string, TmTab>([
  ['Test Cases', 'cases'],
  ['Test Suites', 'suites'],
  ['Test Plans', 'plans'],
  ['Strategy', 'strategy'],
  ['Knowledge Generation', 'knowledge'],
  ['Reviews', 'approvals'],
  ['reviews', 'approvals'],
  ['Duplicates', 'duplicates'],
  ['Audit Log', 'audit'],
])

/** The tab an alias names, or null when `raw` is not an alias. */
export function tabAlias(raw: string | null): TmTab | null {
  return raw === null ? null : ALIASES.get(raw) ?? null
}
