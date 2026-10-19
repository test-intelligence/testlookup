// @vitest-environment node
/**
 * W29a UIX-13 / UIX-10: the ESLint rules of the UI standard, run through the
 * ESLint API with the repo's own `eslint.config.js` on planted snippets.
 *
 * - The palette rule (`no-restricted-syntax`) keeps its first selector and
 *   gains every Tailwind hue and white / black, in string literals AND in the
 *   text of template literals (class strings built with `${…}` escaped it).
 * - The date rule (`no-restricted-properties`) fires in pages and components,
 *   not in `utils/` (where the formatters live) and not in the chart kit (W29b).
 *
 * ESLint is loaded through a variable specifier (as `readSourceFile` loads
 * node:fs): `npm run build` type-checks src/ without node types.
 */
/* eslint-disable no-restricted-syntax -- this file PLANTS raw palette / white / black classes to prove the rule fires on them */
import { beforeAll, describe, expect, it } from 'vitest'

interface LintMessage { ruleId: string | null; message: string }
interface LintResult { messages: LintMessage[] }
interface ESLintLike { lintText(code: string, options: { filePath: string }): Promise<LintResult[]> }
interface ESLintModule { ESLint: new (options: { cwd: string; overrideConfigFile?: string }) => ESLintLike }

let eslint: ESLintLike

beforeAll(async () => {
  const specifier = 'eslint'
  const urlSpecifier = 'node:url'
  const { ESLint } = (await import(/* @vite-ignore */ specifier)) as ESLintModule
  const { fileURLToPath } = (await import(/* @vite-ignore */ urlSpecifier)) as { fileURLToPath(url: URL): string }
  const frontendRoot = fileURLToPath(new URL('../../', import.meta.url))
  eslint = new ESLint({ cwd: frontendRoot })
}, 120_000)

async function rulesHit(code: string, filePath: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath })
  return result.messages.map((m) => m.ruleId ?? 'fatal')
}

const PAGE = 'src/pages/PlantedW29a.tsx'

describe('the palette rule (no-restricted-syntax)', () => {
  it.each([
    ['a raw sky class', "export const a = 'text-sky-500'"],
    ['a raw class on an extended prefix', "export const a = 'ring-rose-300/40'"],
    ['a variant-prefixed raw class', "export const a = 'hover:bg-slate-800'"],
    ['white in a template literal', 'export const f = (x: string) => `bg-white/10 ${x}`'],
    ['black on a border', "export const a = 'border-black'"],
    ['white text', "export const a = 'px-2 text-white'"],
    ['the original families still', "export const a = 'text-red-500'"],
  ])('flags %s', async (_, code) => {
    expect(await rulesHit(code, PAGE)).toContain('no-restricted-syntax')
  }, 60_000)

  it.each([
    ['a token class', "export const a = 'text-[var(--color-text)] bg-[var(--color-bg-hover)]'"],
    ['a token in a template literal', 'export const f = (x: string) => `border-[var(--color-border)] ${x}`'],
    ['a word that merely contains a hue', "export const a = 'whitespace-nowrap text-[var(--status-failed-ink)]'"],
    ['a regex a test uses to prove absence', 'export const re = /text-white/'],
  ])('passes %s', async (_, code) => {
    expect(await rulesHit(code, PAGE)).not.toContain('no-restricted-syntax')
  }, 60_000)
})

describe('the date rule (no-restricted-properties)', () => {
  const LOCALE_CALL = 'export const a = (d: Date, n: number) => [d.toLocaleString(), n.toLocaleString(), d.toLocaleDateString(), d.toLocaleTimeString()]'
  const INTL = "export const f = new Intl.NumberFormat('en-US'); export const g = new Intl.DateTimeFormat('en-US')"

  it('fires in pages and components', async () => {
    const page = await rulesHit(LOCALE_CALL, 'src/pages/PlantedW29a.tsx')
    expect(page.filter((r) => r === 'no-restricted-properties')).toHaveLength(4)
    const component = await rulesHit(INTL, 'src/components/PlantedW29a.ts')
    expect(component.filter((r) => r === 'no-restricted-properties')).toHaveLength(2)
  }, 60_000)

  it('is silent in utils/ (the formatters) and in the chart kit (W29b)', async () => {
    expect(await rulesHit(LOCALE_CALL, 'src/utils/PlantedW29a.ts')).not.toContain('no-restricted-properties')
    expect(await rulesHit(LOCALE_CALL, 'src/components/charts/PlantedW29a.ts')).not.toContain('no-restricted-properties')
    expect(await rulesHit(INTL, 'src/utils/PlantedW29a.ts')).not.toContain('no-restricted-properties')
  }, 60_000)

  it('the formatters are the answer: their calls pass', async () => {
    const code = "import { formatCompactDateTime, formatNumber } from '@/utils/formatters'\nexport const a = (d: Date, n: number) => [formatCompactDateTime(d), formatNumber(n)]"
    expect(await rulesHit(code, 'src/pages/PlantedW29a.tsx')).not.toContain('no-restricted-properties')
  }, 60_000)
})
