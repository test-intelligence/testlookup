import { Presentation } from 'lucide-react'
import { clsx } from 'clsx'
import { usePresentationStore } from '@/store/presentationStore'

/**
 * The presentation-mode switch (VIZ-106): one press puts the whole app into
 * room sizes — metric values and headlines grow, quiet text reaches 7:1,
 * every chart's drawing is scaled to 16 px axis text — and the choice stays
 * in this browser (`presentationStore`).
 *
 * A toggle button: its name stays "Presentation mode" and `aria-pressed`
 * says whether it is on, so a screen reader hears one control with a state
 * rather than a label that changes under the user. Sighted users read the
 * state as a word ("On" / "Off") and as the row's active fill, never from
 * colour alone.
 *
 * It is a row of the sidebar's footer, beside My Profile and Settings, at
 * every width: one click away wherever a presenter is, worded, and (below
 * 1024 px) inside the navigation drawer.
 */
export default function PresentationToggle() {
  const enabled = usePresentationStore((s) => s.enabled)
  const setEnabled = usePresentationStore((s) => s.setEnabled)

  return (
    <button
      type="button"
      data-presentation-toggle=""
      aria-pressed={enabled}
      onClick={() => setEnabled(!enabled)}
      title="Larger text and stronger contrast, for a wall monitor or a projector"
      className={clsx('sidebar-link w-full text-left', enabled && 'active')}
    >
      <Presentation className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
      Presentation mode
      <span className="ml-auto text-xs" aria-hidden="true">
        {enabled ? 'On' : 'Off'}
      </span>
    </button>
  )
}
