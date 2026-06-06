import { type ReactNode } from 'react'

/**
 * Standard full-width page root.
 *
 * The app shell (`AppLayout`) owns BOTH the centered max-width column
 * (`max-w-[1600px] mx-auto`) and the responsive side gutters
 * (`px-4 sm:px-6 lg:px-8 xl:px-10`). Individual pages must therefore stay
 * `w-full` and must NOT add `mx-auto`, `max-w-*`, or their own horizontal
 * padding — doing so fights the shell's column and re-introduces the
 * off-center / width-drift this layout exists to prevent. Pass `className`
 * only for vertical rhythm (e.g. `space-y-5`).
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
