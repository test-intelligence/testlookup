/**
 * M20: every hand-rolled modal honours the same keyboard contract.
 *
 * Each modal is opened from a real trigger button, then driven the way a
 * keyboard user drives it:
 *   - focus is inside the dialog once it opens;
 *   - Shift+Tab on the first control wraps to the last, Tab on the last
 *     wraps to the first (focus never walks out behind the backdrop);
 *   - Escape closes it and focus goes back to the trigger.
 *
 * The seven are the five the audit counted, the correct reference
 * implementation (TransitionReasonDialog, now on the shared hook), and
 * UploadReportModal; the eighth is the shared SidePanel in modal mode (VIZ-109),
 * and a NON-modal SidePanel on a phone-width screen, where it renders modal.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useState, type ReactElement } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/useJiraDefects', () => ({
  useJiraDefectMetadata: () => ({
    metadata: {
      available: true, reason: null, projects: [{ key: 'QA', name: 'Quality' }],
      issue_types: ['Bug'], default_project_key: 'QA', webhook_available: false,
    },
    isLoading: false, isError: false,
  }),
  useJiraDefectPreview: () => ({
    preview: {
      signature: 'f'.repeat(16), summary: 's', description: 'd', test_name: 't', suite_name: null,
      cluster_id: null, error_message: null,
      occurrences: { first_seen: null, last_seen: null, failing_runs: 1 },
      context: { branch: null, build_number: null, ci_run_url: null },
      ai_analysis: null, deep_link: null, latest_run_id: null, existing_defect: null,
    },
    isLoading: false, isError: false,
  }),
}))
vi.mock('@/hooks/useDefectPromotion', () => ({
  useDefectCandidate: () => ({ candidate: undefined, isLoading: false, isError: false, mutate: vi.fn() }),
  usePromoteCluster: () => ({
    promote: vi.fn(), isPromoting: false, promotionResult: null, promotionError: null, reset: vi.fn(),
  }),
}))

import CorrectClassificationModal from './ai/CorrectClassificationModal'
import DefectPromotionModal from './ai/DefectPromotionModal'
import CreateJiraIssueModal from './defects/CreateJiraIssueModal'
import DefectIntakeModal from './defects/DefectIntakeModal'
import ProposeQuarantineModal from './quarantine/ProposeQuarantineModal'
import UploadReportModal from './runs/UploadReportModal'
import TransitionReasonDialog from './testManagement/TransitionReasonDialog'
import SidePanel from './ui/SidePanel'

type Render = (onClose: () => void) => ReactElement

const MODALS: Array<[string, Render]> = [
  ['CorrectClassificationModal', (onClose) => (
    <CorrectClassificationModal testName="checkout" analysisId="a-1" currentCategory="FLAKY" onClose={onClose} />
  )],
  ['DefectPromotionModal', (onClose) => (
    <DefectPromotionModal runId="r-1" clusterId="c-1" clusterLabel="NPE cluster" onClose={onClose} />
  )],
  ['CreateJiraIssueModal', (onClose) => (
    <CreateJiraIssueModal projectId="p-1" fingerprint="fp" testName="checkout" onClose={onClose} />
  )],
  ['DefectIntakeModal', (onClose) => <DefectIntakeModal projectId="p-1" onClose={onClose} />],
  ['ProposeQuarantineModal', (onClose) => (
    <ProposeQuarantineModal
      prefill={{ project_id: 'p-1', test_fingerprint: 'fp', test_name: 'checkout' }}
      source="test"
      onClose={onClose}
    />
  )],
  ['UploadReportModal', (onClose) => (
    <UploadReportModal projectId="p-1" onClose={onClose} onSuccess={vi.fn()} />
  )],
  ['TransitionReasonDialog', (onClose) => (
    <TransitionReasonDialog
      title="Archive" description="Archive this case." confirmLabel="Archive"
      onCancel={onClose} onConfirm={vi.fn()}
    />
  )],
  // VIZ-109: the shared side panel in its modal shape.
  ['SidePanel (modal)', (onClose) => (
    <SidePanel open modal title="Run details" onClose={onClose}>
      <button type="button">Inside</button>
    </SidePanel>
  )],
]

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

function Harness({ modal, onClose }: { modal: Render; onClose: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open modal</button>
      {open && modal(() => {
        onClose()
        setOpen(false)
      })}
    </>
  )
}

function open(modal: Render) {
  const onClose = vi.fn()
  render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <Harness modal={modal} onClose={onClose} />
    </SWRConfig>,
  )
  const trigger = screen.getByRole('button', { name: 'Open modal' })
  trigger.focus()
  fireEvent.click(trigger)
  const dialog = screen.getByRole('dialog')
  const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE))
  return { trigger, dialog, items, onClose }
}

const press = (key: string, shiftKey = false) =>
  fireEvent.keyDown(document.activeElement ?? document.body, { key, shiftKey })

function keyboardContract(modal: Render) {
  it('takes focus when it opens', () => {
    const { dialog } = open(modal)
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('wraps Tab and Shift+Tab inside the dialog', () => {
    const { items } = open(modal)
    expect(items.length).toBeGreaterThan(1)
    const first = items[0]
    const last = items[items.length - 1]

    first.focus()
    press('Tab', true)
    expect(last).toHaveFocus()

    press('Tab')
    expect(first).toHaveFocus()
  })

  it('closes on Escape and returns focus to its trigger', () => {
    const { trigger, onClose } = open(modal)
    press('Escape')
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(trigger).toHaveFocus()
  })
}

describe.each(MODALS)('%s keyboard contract', (_name, modal) => {
  keyboardContract(modal)
})

// VIZ-109 review: below the sm breakpoint a NON-modal SidePanel has no room
// beside the page and is rendered modal — so it owes the same contract.
describe('SidePanel (non-modal, on a phone) keyboard contract', () => {
  const realMatchMedia = window.matchMedia
  beforeEach(() => {
    window.matchMedia = ((query: string) => ({
      matches: false, // a 375 px screen matches no min-width breakpoint
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
  })
  afterEach(() => {
    window.matchMedia = realMatchMedia
  })

  keyboardContract((onClose) => (
    <SidePanel open title="Run details" onClose={onClose}>
      <button type="button">Inside</button>
    </SidePanel>
  ))
})
