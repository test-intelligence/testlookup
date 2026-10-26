/**
 * Shared by the public one-time-link pages (W17b): `/accept-invite`, `/forgot-password`,
 * `/reset-password/confirm` — the centred card (the fragment token: `hooks/useFragmentToken`).
 */
import AppLogo from '@/components/ui/AppLogo'

export function LinkPageShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-bg)] px-4">
      <div className="w-full max-w-md card p-6 space-y-4">
        <div className="flex justify-center"><AppLogo /></div>
        {children}
      </div>
    </div>
  )
}
