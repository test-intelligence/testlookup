/**
 * The documentation renderer's plain exports (no components), apart from
 * `DocArticle.tsx` so that file exports only components (fast refresh).
 */
import { createContext } from 'react'
import { slugify } from '@/content/guide/slug'

/**
 * A host's handler for in-app links (`/...` or `#...`): return `true` when it
 * handled the link, `false` to let it navigate as usual. None: navigate.
 */
export const DocLinkFollowContext = createContext<((href: string) => boolean) | null>(null)

/** Headings for the on-page table of contents. */
export function headingsOf(markdown: string): { depth: number; text: string; id: string }[] {
  const out: { depth: number; text: string; id: string }[] = []
  let inFence = false
  for (const line of markdown.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      inFence = !inFence
      continue
    }
    if (inFence) continue
    const m = /^(#{2,3})\s+(.*)$/.exec(line)
    if (m) out.push({ depth: m[1].length, text: m[2].trim(), id: slugify(m[2].trim()) })
  }
  return out
}

/** `/docs/<id>` or `/docs/<id>#anchor` → its parts; anything else → null. */
export function parseDocHref(href: string): { topic: string; anchor: string | null } | null {
  const m = /^\/docs\/([\w-]+)(?:#([\w-]+))?$/.exec(href)
  return m ? { topic: m[1], anchor: m[2] ?? null } : null
}
