/**
 * K1 (Wave 2.6, VIZ-408): the ONE place a report page asks whether the
 * catalogue sections are on.
 *
 * The catalogue rides on `viz_chart_data_api` (owner decision OD-1: no seventh
 * flag), and the advanced sections (the Trends heatmap, every Wave-3 section)
 * additionally need `viz_advanced_charts`.
 * Both are seeded OFF, so merging the pages changes nothing a user sees, and
 * rollback is turning the flag off for a project.
 *
 * Why one seam rather than `useFeatureEnabled(...)` in each page: Wave 5 turns
 * these flags on by default and deletes this file. While every page goes
 * through here, that is one deletion and five un-branched mounts; once a page
 * reads a flag by itself, it is a hunt. `flagSeam.ratchet.test.ts` fails on any
 * other reader of these keys (and of `viz_three_d`, VIZ-508's third).
 *
 * The answer is `false` until the status request answers and `false` if it
 * fails (`useFeatureEnabled`'s rule: a gate that cannot be read stays closed),
 * so a page never mounts a section, and never downloads its chunk, on a guess.
 */
import { useFeatureEnabled, useFeatureFlagStatus } from '@/hooks/useFeatureFlags'
import { VIZ_FLAGS } from '@/config/vizFlags'

/** Whether this project's report pages show the catalogue sections. */
export function useCatalogueRollout(): boolean {
  return useFeatureEnabled(VIZ_FLAGS.chartDataApi)
}

/**
 * The same flag with "not known yet" kept apart from "off": `undefined` while
 * the lookup is in flight, then `true` / `false` (`false` on failure).
 *
 * For the one section that REPLACES a card instead of adding below it
 * (Overview's trend slot, plan section 8.5): it holds a same-height skeleton
 * while this is `undefined`, instead of drawing the old card and swapping it
 * out a moment later.
 */
export function useCatalogueRolloutStatus(): boolean | undefined {
  return useFeatureFlagStatus(VIZ_FLAGS.chartDataApi)
}

/**
 * Whether the ADVANCED sections are on (Wave 3: heatmaps, coverage map,
 * failure groups, scatter, drill-down): the catalogue flag AND
 * `viz_advanced_charts` (plan 2.4: no seventh flag). Called from inside each
 * lazy section, so a page with the catalogue off never even looks the second
 * flag up: flag-off, the only request a page adds is its one
 * `useCatalogueRollout()` lookup.
 */
export function useAdvancedRollout(): boolean {
  const catalogue = useFeatureEnabled(VIZ_FLAGS.chartDataApi)
  const advanced = useFeatureEnabled(VIZ_FLAGS.advancedCharts)
  return catalogue && advanced
}

/**
 * `useAdvancedRollout` with "not known yet" kept apart from "off" (VIZ-505):
 * `undefined` while either lookup is in flight, `false` as soon as either is
 * off (or failed), `true` when both are on. For a PAGE that exists only with
 * the advanced charts (the Explorer): it holds a placeholder while this is
 * `undefined` instead of flashing "not enabled" on every first visit.
 */
export function useAdvancedRolloutStatus(): boolean | undefined {
  const catalogue = useFeatureFlagStatus(VIZ_FLAGS.chartDataApi)
  const advanced = useFeatureFlagStatus(VIZ_FLAGS.advancedCharts)
  if (catalogue === false || advanced === false) return false
  if (catalogue === undefined || advanced === undefined) return undefined
  return true
}

/**
 * Whether the test scatter offers its opt-in 3D view (VIZ-508): the advanced
 * sections AND `viz_three_d`. Called from inside the scatter's lazy body only,
 * so the third lookup is made where a scatter is mounted and nowhere else (a
 * lookup outside a drawn section adds a request to every page's inventory).
 */
export function useThreeDRollout(): boolean {
  const advanced = useAdvancedRollout()
  const threeD = useFeatureEnabled(VIZ_FLAGS.threeD)
  return advanced && threeD
}
