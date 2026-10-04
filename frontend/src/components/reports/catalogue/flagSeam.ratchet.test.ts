/**
 * K1 ratchet: the two catalogue flags have exactly one reader.
 *
 * Wave 5 turns the flags on by default and deletes the seam; that is a small,
 * safe diff only while no page branches on a flag by itself. So a reference to
 * `VIZ_FLAGS.chartDataApi` / `VIZ_FLAGS.advancedCharts` — or to their raw
 * strings — anywhere but the definition and the seam fails here, and the fix
 * is to call `useCatalogueRollout()` / `useAdvancedRollout()` instead.
 */
import { describe, expect, it } from 'vitest'

/** Every non-test source file under src/, as text, keyed `/src/...`. */
const SOURCES = import.meta.glob(['/src/**/*.ts', '/src/**/*.tsx', '!/src/**/*.test.ts', '!/src/**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** The definition and the seam: the only files allowed to name the two flags. */
const ALLOWED = new Set(['/src/config/vizFlags.ts', '/src/components/reports/catalogue/useCatalogueRollout.ts'])

// VIZ-508: `viz_three_d` (the scatter's 3D view) goes through the seam too.
const FLAG_REFERENCE =
  /VIZ_FLAGS\s*(?:\.\s*(?:chartDataApi|advancedCharts|threeD)\b|\[\s*['"`](?:chartDataApi|advancedCharts|threeD)['"`]\s*\])|['"`]viz_(?:chart_data_api|advanced_charts|three_d)['"`]/

/**
 * The source with its comments blanked out: a comment that NAMES a flag (to
 * explain what gates a section) reads nothing. Block comments, and `//` line
 * comments not preceded by a colon or a quote (so `'https://…'` in a string
 * survives).
 */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1')
}

function flagReaders(sources: Record<string, string>): string[] {
  return Object.entries(sources)
    .filter(([path, text]) => !ALLOWED.has(path) && FLAG_REFERENCE.test(code(text)))
    .map(([path]) => path)
    .sort()
}

describe('catalogue flag seam (K1 ratchet)', () => {
  it('finds the seam itself, so the scan is not vacuous', () => {
    expect(Object.keys(SOURCES)).toContain('/src/components/reports/catalogue/useCatalogueRollout.ts')
    expect(FLAG_REFERENCE.test(SOURCES['/src/components/reports/catalogue/useCatalogueRollout.ts'])).toBe(true)
  })

  it('no other source file reads viz_chart_data_api, viz_advanced_charts or viz_three_d', () => {
    expect(flagReaders(SOURCES)).toEqual([])
  })

  it('catches each spelling a page could use', () => {
    const planted = {
      '/src/pages/A.tsx': 'useFeatureEnabled(VIZ_FLAGS.chartDataApi)',
      '/src/pages/B.tsx': "useFeatureEnabled(VIZ_FLAGS['advancedCharts'])",
      '/src/pages/C.tsx': "useFeatureEnabled('viz_chart_data_api')",
      '/src/pages/D.tsx': 'useFeatureEnabled(`viz_advanced_charts`)',
      '/src/pages/E.tsx': 'useFeatureEnabled(VIZ_FLAGS.multiFilters)',
    }
    expect(flagReaders(planted)).toEqual(['/src/pages/A.tsx', '/src/pages/B.tsx', '/src/pages/C.tsx', '/src/pages/D.tsx'])
  })

  it('catches a scatter that reads viz_three_d by itself (VIZ-508)', () => {
    const planted = {
      '/src/components/charts/F.tsx': 'useFeatureEnabled(VIZ_FLAGS.threeD)',
      '/src/components/charts/G.tsx': "useFeatureFlagStatus('viz_three_d')",
      '/src/components/charts/H.tsx': 'useFeatureEnabled(VIZ_FLAGS.threeDee)',
    }
    expect(flagReaders(planted)).toEqual(['/src/components/charts/F.tsx', '/src/components/charts/G.tsx'])
  })

  it('a comment that names a flag is not a reader; code after a comment still is', () => {
    const planted = {
      '/src/pages/Doc.tsx': "/**\n * Shown when `viz_chart_data_api` is on.\n */\nexport const x = 1\n// VIZ_FLAGS.advancedCharts gates the heatmap\n",
      '/src/pages/Url.tsx': "const u = 'https://example.test'; useFeatureEnabled('viz_chart_data_api')",
      '/src/pages/After.tsx': "/* note */ useFeatureEnabled(VIZ_FLAGS.chartDataApi)",
    }
    expect(flagReaders(planted)).toEqual(['/src/pages/After.tsx', '/src/pages/Url.tsx'])
  })
})
