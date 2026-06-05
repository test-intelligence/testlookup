import { type ReactNode } from 'react'

/**
 * Standard full-width page root.
 *
 * The app shell (`AppLayout`) owns the single 24px gutter (its `<main … p-6>`),
 * so individual pages must NOT re-center (`mx-auto`) or cap their width
 * (`max-w-*` / `maxWidth`). Doing so re-introduces the wasted side margins and
 * per-page width drift this component exists to prevent. Use `<PageShell>` as
 * the root of every page; pass `className` only for extra vertical rhythm
 * (e.g. `space-y-5`), never width/centering.
 */
export default function PageShell({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return <main className={`w-full pb-10 ${className}`}>{children}</main>
}
