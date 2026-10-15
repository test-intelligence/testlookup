/**
 * W04 AIC-07: how Settings › Feature flags groups the flags it lists.
 *
 * The page renders each group's PRESENT flags first, in the order below,
 * under a header row, then every other flag under no header. A key missing
 * from the server's list simply does not render; a group with no present
 * flag renders nothing. Adding a group is adding an entry here.
 */
export interface FeatureFlagGroup {
  id: string
  title: string
  blurb: string
  keys: readonly string[]
}

export const FEATURE_FLAG_GROUPS: readonly FeatureFlagGroup[] = [
  {
    id: 'agent-stages',
    title: 'Agent stages (deep pipeline)',
    blurb:
      'Stages of the deep investigation. A stage that is off is skipped and the run says why. ' +
      'Turn a stage on for one project by switching it on globally and listing the project under Edit.',
    keys: [
      'contract_validation',
      'log_intelligence',
      'regression_watchman',
      'change_ownership',
      'defect_commander',
      'aiq_gap_refinement',
      'async_decision_report_supersession',
    ],
  },
]

/**
 * Stages that WRITE (create defects) rather than read: the page marks them so
 * nobody switches one on thinking it is another read-only specialist.
 */
export const MUTATING_FLAG_KEYS: ReadonlySet<string> = new Set(['defect_commander'])

/** The flags in display order: each group's present flags (group order), then the rest in server order. */
export function groupFeatureFlags<T extends { key: string }>(
  flags: readonly T[],
  groups: readonly FeatureFlagGroup[] = FEATURE_FLAG_GROUPS,
): { groups: { group: FeatureFlagGroup; flags: T[] }[]; ungrouped: T[] } {
  const byKey = new Map(flags.map((flag) => [flag.key, flag]))
  const claimed = new Set<string>()
  const grouped = groups
    .map((group) => {
      const present = group.keys
        .filter((key) => !claimed.has(key))
        .map((key) => byKey.get(key))
        .filter((flag): flag is T => flag !== undefined)
      for (const flag of present) claimed.add(flag.key)
      return { group, flags: present }
    })
    .filter((entry) => entry.flags.length > 0)
  return { groups: grouped, ungrouped: flags.filter((flag) => !claimed.has(flag.key)) }
}
