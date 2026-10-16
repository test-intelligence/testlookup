/**
 * TRND-11: the export-option registry — one entry per chart container, gone
 * when unregistered, nothing for an unknown container — and the bundle rule:
 * the registry (imported by the eager export menu) holds no engine code, and
 * the SVG export module is reached ONLY by a dynamic `import()`.
 */
import { describe, expect, it } from 'vitest'
import { exportOptionOf, registerExportOption, unregisterExportOption } from './exportRegistry'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const REGISTRY_SOURCE = import.meta.glob('./exportRegistry.ts', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

describe('exportRegistry', () => {
  it('holds one entry per container until it is unregistered', () => {
    const a = document.createElement('div')
    const b = document.createElement('div')
    const build = () => ({})
    expect(exportOptionOf(a)).toBeNull()
    registerExportOption(a, { type: 'heatmap', build })
    expect(exportOptionOf(a)).toEqual({ type: 'heatmap', build })
    expect(exportOptionOf(b)).toBeNull()
    registerExportOption(a, { type: 'treemap', build })
    expect(exportOptionOf(a)?.type).toBe('treemap')
    unregisterExportOption(a)
    expect(exportOptionOf(a)).toBeNull()
  })

  it('imports nothing from an engine package (types only), so it may sit in any chunk', () => {
    const source = Object.values(REGISTRY_SOURCE)[0]
    expect(source).toBeTypeOf('string')
    for (const line of source.split('\n').filter((l) => /^\s*import\b/.test(l))) {
      expect(line, line).toMatch(/^\s*import type\b/)
    }
  })

  it('exportSvg is reached only by a dynamic import(), never a static one (the SVG renderer stays in its chunk)', () => {
    const files = Object.keys(SOURCES)
    expect(files.length).toBeGreaterThan(100)
    const staticImporters = files.filter(
      (file) =>
        !/\.test\.tsx?$/.test(file) &&
        /^\s*(?:import|export)\b[^;]*from\s+['"][^'"]*exportSvg['"]/m.test(SOURCES[file].replace(/import\(\s*['"][^'"]*exportSvg['"]\s*\)/g, '')),
    )
    expect(staticImporters).toEqual([])
    const dynamicImporters = files.filter(
      (file) => !/\.test\.tsx?$/.test(file) && /import\(\s*['"][^'"]*exportSvg['"]\s*\)/.test(SOURCES[file]),
    )
    expect(dynamicImporters).toEqual(['/src/components/charts/ChartExportMenu.tsx'])
  })
})
