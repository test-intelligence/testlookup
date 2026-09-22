/**
 * Read a source file (relative to `frontend/`) as text, from a vitest test.
 *
 * Why not `?raw`: vitest stubs CSS (`css: false`), so `import css from
 * '../index.css?raw'` — and `import.meta.glob(..., '?raw')` — yield '' for a
 * stylesheet. Why not `import { readFileSync } from 'node:fs'`: `npm run
 * build` type-checks src/ with no node types, so the import fails `tsc`.
 *
 * A dynamic import through a VARIABLE specifier is opaque to both: tsc types
 * it as `Promise<any>` without resolving it, and Vite leaves it alone
 * (`@vite-ignore`). Tests only — this runs in vitest's Node process.
 */
interface NodeFs {
  readFileSync(path: URL, encoding: 'utf8'): string
}

export async function readSourceFile(relativeToFrontend: string): Promise<string> {
  const specifier = 'node:fs'
  const fs = (await import(/* @vite-ignore */ specifier)) as NodeFs
  // Not `new URL(\`…${x}\`, import.meta.url)` inline: Vite rewrites that exact
  // shape into an asset glob, which resolves an unknown path to "undefined".
  const here = import.meta.url
  const frontendRoot = new URL('../../', here)
  return fs.readFileSync(new URL(relativeToFrontend, frontendRoot), 'utf8')
}
