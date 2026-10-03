/**
 * The pinned public contract of each Wave-3 section (plan 4.2): every module
 * a composite mounts exists, exports its component by name AND as `default`
 * (the same function, so `lazyWithRetry(() => import(...))` and a named import
 * mount the same thing), and accepts the props `sectionContracts.ts` pins.
 *
 * This test outlives the stubs: when an owner fills a section, it must still
 * pass. The `satisfies` lines are the compile-time half: a section whose props
 * stop accepting the pinned ones fails `tsc`.
 */
import type { ComponentType } from 'react'
import { describe, expect, it } from 'vitest'
import * as heatmap from './HeatmapSection'
import * as coverageMap from './CoverageMapSection'
import * as failureGroups from './FailureGroupsSection'
import * as scatter from './ScatterSection'
import * as drill from './FailuresDrill'
import * as rows from './RowsPanel'
import {
  HEATMAP_KINDS,
  type CoverageMapSectionProps,
  type FailureGroupsSectionProps,
  type FailuresDrillProps,
  type HeatmapSectionProps,
  type ScatterSectionProps,
} from './sectionContracts'
import type { RowsPanelProps } from './RowsPanel.model'

heatmap.HeatmapSection satisfies ComponentType<HeatmapSectionProps>
coverageMap.CoverageMapSection satisfies ComponentType<CoverageMapSectionProps>
failureGroups.FailureGroupsSection satisfies ComponentType<FailureGroupsSectionProps>
scatter.ScatterSection satisfies ComponentType<ScatterSectionProps>
drill.FailuresDrill satisfies ComponentType<FailuresDrillProps>
rows.RowsPanel satisfies ComponentType<RowsPanelProps>

describe('Wave 3 section contracts (pinned)', () => {
  it.each([
    ['HeatmapSection', heatmap, heatmap.HeatmapSection],
    ['CoverageMapSection', coverageMap, coverageMap.CoverageMapSection],
    ['FailureGroupsSection', failureGroups, failureGroups.FailureGroupsSection],
    ['ScatterSection', scatter, scatter.ScatterSection],
    ['FailuresDrill', drill, drill.FailuresDrill],
    ['RowsPanel', rows, rows.RowsPanel],
  ])('%s is exported by name and as default, the same function', (_name, module, named) => {
    expect(typeof named).toBe('function')
    expect(module.default).toBe(named)
  })

  it('pins the heatmap kinds the endpoint serves', () => {
    expect(HEATMAP_KINDS).toEqual(['suite_day', 'test_run', 'suite_environment', 'suite_release'])
  })
})
