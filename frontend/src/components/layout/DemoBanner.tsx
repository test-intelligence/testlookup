import { Eye } from 'lucide-react'

/** Where a demo visitor goes to get their own copy. */
export const GET_TESTLOOKUP_URL = 'https://testlookup.app'

/**
 * The public read-only demo's standing notice. It sets expectations before the
 * first refused save: nothing a visitor does is kept, and the data is sample
 * data that is rebuilt every night.
 *
 * AppLayout lazy-loads this only for a session opened by the demo's one-click
 * sign-in (`authStore.demoSession`), so a normal deployment never downloads it
 * and never spends a request asking whether it is the demo.
 */
export default function DemoBanner({ refreshing = false }: { refreshing?: boolean }) {
  // An unreachable backend on the demo is almost always the nightly rebuild
  // (or maintenance): every container is recreated and the sample data
  // re-seeded, for a minute or two. Say that, calmly; DegradedBanner stays
  // quiet for demo sessions. The rebuild also replaces the demo account, so
  // the visitor may land on the sign-in page afterwards. AppLayout passes the
  // health poll's verdict in, so this lazy chunk imports nothing the shell
  // does not already load (the eager bundle budget).
  if (refreshing) {
    return (
      <div
        role="status"
        data-testid="demo-refreshing-banner"
        className="flex items-start gap-2 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-4 py-2 text-sm text-[var(--color-text-secondary)]"
      >
        <Eye className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-accent)]" aria-hidden="true" />
        <span>
          <strong className="font-semibold text-[var(--color-text)]">The live demo is refreshing.</strong>{' '}
          Its sample data is rebuilt every night at 03:00 UTC and after maintenance, which takes a few
          minutes. Retrying automatically; if you are signed out when it is back, choose Explore the demo
          again.
        </span>
      </div>
    )
  }

  return (
    <div
      role="status"
      data-testid="demo-banner"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-4 py-1.5 text-xs text-[var(--color-text-secondary)]"
    >
      <Eye className="h-3.5 w-3.5 flex-shrink-0 text-[var(--color-accent)]" aria-hidden="true" />
      <span>
        <strong className="font-semibold text-[var(--color-text)]">Live demo</strong>
        {' · read-only · sample data, reset every night'}
      </span>
      <a
        href={GET_TESTLOOKUP_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="ml-auto font-medium text-[var(--color-accent)] hover:underline"
      >
        Get TestLookup
      </a>
    </div>
  )
}
