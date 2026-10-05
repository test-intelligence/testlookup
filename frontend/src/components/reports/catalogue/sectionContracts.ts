/**
 * Wave 3 PR-B: the PINNED public contract of each advanced section (plan 4.2,
 * "a pinned public contract per section, so composites compile before their
 * parts exist").
 *
 * Three composites mount sections they do not own: `CoverageAdvanced` (FK2)
 * mounts FK1's `HeatmapSection`; `SuiteDetailAdvanced` (FK4) mounts FK1's
 * `HeatmapSection`; `FailuresAdvanced` (FK3) mounts FK4's `ScatterSection` and
 * FK5's `FailuresDrill`. Each section module exists from M0a on as a stub with
 * the signature below (renders nothing), so every composite compiles and
 * tests against it on day one; the owner replaces the body, never the
 * signature.
 *
 * Rules for an owner:
 *   - the module keeps BOTH a named export and the same function as `default`
 *     (a composite may `lazyWithRetry(() => import(...))` it);
 *   - props may only GROW, and only by OPTIONAL props (a composite written
 *     against this file must keep compiling);
 *   - a composite never names a flag, and no section asks one (Phase D:
 *     the heatmap and coverage map since S4, the Failures-family sections
 *     and the scatter since S5).
 *
 * Types only: nothing here reaches a bundle.
 */
import type { ScopeValue } from '@/lib/scopeParams'

/** What every section is given: the page's own scope, as the page shows it. */
export interface AdvancedSectionScope {
  /** The page's window, days (snapped to the page's options). `catalogueParams` clamps it to 90 on the wire. */
  days: number
  /** The page's suite scope (`usePageSuiteFilter().suiteFilter`); on Suite detail, the page's one suite. */
  suiteFilter: ScopeValue
}

/** The heatmap kinds the `/analytics/heatmap` endpoint serves (plan 3.1). */
export const HEATMAP_KINDS = ['suite_day', 'test_run', 'suite_environment', 'suite_release'] as const
export type HeatmapKind = (typeof HEATMAP_KINDS)[number]

/** FK1 `components/reports/catalogue/HeatmapSection.tsx` (VIZ-501). */
export interface HeatmapSectionProps extends AdvancedSectionScope {
  /** The kinds this host offers, in selector order; the first is the default. Never empty. */
  kinds: readonly HeatmapKind[]
}

/** FK2 `components/reports/catalogue/CoverageMapSection.tsx` (VIZ-502). Coverage page. */
export type CoverageMapSectionProps = AdvancedSectionScope

/** FK3 `components/reports/catalogue/FailureGroupsSection.tsx` (VIZ-504). Failure analysis page. */
export type FailureGroupsSectionProps = AdvancedSectionScope

/**
 * FK4 `components/reports/catalogue/ScatterSection.tsx` (VIZ-506).
 * `suite`: Suite detail (the page's suite pinned in `suiteFilter`);
 * `project`: Failure analysis, project-wide top tests (`order=failures`).
 */
export interface ScatterSectionProps extends AdvancedSectionScope {
  placement: 'suite' | 'project'
}

/** FK5 `components/reports/catalogue/FailuresDrill.tsx` (VIZ-602 / 603). The drill ladder, Failure analysis page. */
export type FailuresDrillProps = AdvancedSectionScope
