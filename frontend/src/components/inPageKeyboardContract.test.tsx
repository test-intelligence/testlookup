/**
 * W27 PAN-01: every in-page container honours the same keyboard contract —
 * what the modals gave their users, minus the trap (owner: desktop only, no
 * new keyboard work, no regression):
 *   - focus moves inside the container once it opens, onto its first field
 *     (else its first control);
 *   - Tab from the last control is NOT prevented (the page goes on around
 *     the container: no trap);
 *   - Escape (focus inside) closes it, and focus goes back to the trigger;
 *   - the innermost container owns Escape: one opened inside another in-page
 *     container closes alone, the outer one stays;
 *   - no dialog role, no aria-modal, no portal: it renders in its parent.
 *
 * The three primitives, then the six forms modalKeyboardContract drove through
 * the TRAP contract until W27a, rendered exactly as that file rendered them
 * (same props, same hook mocks) now that they open in the page (W27a phase 2).
 * Where focus lands changed for one of them: Correct classification focuses
 * its Comment field (the primitive picks the first FIELD; the category choices
 * are buttons), where the modal focused the first category.
 *
 * Then the W27b containers (PAN-05..07): TransitionReasonDialog (the last
 * entry of the trap contract, its seven hosts), the test library's form frame
 * (`ModalWrap`, behind New test case / New test plan / AI Generate / Generate
 * strategy), AI Generate itself, the Insights section and a generated case's
 * citations. The read-only sections have no field: focus lands on their X.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useState, type ReactElement } from 'react'
import { SWRConfig } from 'swr'
import { describe, expect, it, vi } from 'vitest'

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
// W06 DEF-03: New defect's "Affects release" select reads the top bar's cached release list.
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({
    data: {
      items: [{ id: 'rel-1', project_id: 'p-1', name: 'Checkout', version: '2.4', released_at: null }],
      total: 1,
    },
    isLoading: false,
    error: undefined,
  }),
}))
vi.mock('@/hooks/useDefectPromotion', () => ({
  useDefectCandidate: () => ({ candidate: undefined, isLoading: false, isError: false, mutate: vi.fn() }),
  usePromoteCluster: () => ({
    promote: vi.fn(), isPromoting: false, promotionResult: null, promotionError: null, reset: vi.fn(),
  }),
}))
// W27b: the reads the other groups' containers make when they open, answered in-process
// (src/test/networkGuard.ts fails any real request). None of the W27a entries uses these modules.
const ACTIVITY_EVENT = vi.hoisted(() => ({
  id: 'ev-1',
  summary: 'Release 2026.11 was activated',
  event_type: 'release.activated',
  occurred_at: '2026-10-10T10:00:00Z',
  actor: { name: 'Priya', type: 'user', ref: null },
  entity: { href: null, label: '2026.11', id: 'rel-1' },
  diff: { changed_fields: ['status'], before: { status: 'planned' }, after: { status: 'active' } },
  has_diff: true,
  context: {},
  source: null,
}))
vi.mock('@/hooks/useActivityFeed', () => ({
  useActivityEvent: () => ({ event: ACTIVITY_EVENT, error: undefined, isLoading: false }),
}))
vi.mock('@/hooks/useEmailAvailability', () => ({
  useEmailAvailability: () => ({ availability: { available: true, code: null, reason: null }, isLoading: false, error: null }),
}))
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: () => ({ data: [], isLoading: false, error: undefined }),
}))
vi.mock('@/hooks/useSuites', () => ({ useSuites: () => ({ data: { items: [], total: 0 } }) }))
vi.mock('@/services/coverageHistoryService', () => ({
  UNTAGGED_PAGE_SIZE: 50,
  // The untagged list stays loading: the section's own controls are what this contract drives.
  coverageHistoryService: { getUntaggedTests: () => new Promise(() => {}), getCoverageHistory: () => new Promise(() => {}) },
}))

import CorrectClassificationModal from './ai/CorrectClassificationModal'
import DefectPromotionModal from './ai/DefectPromotionModal'
import CreateJiraIssueModal from './defects/CreateJiraIssueModal'
import DefectIntakeModal from './defects/DefectIntakeModal'
import ProposeQuarantineModal from './quarantine/ProposeQuarantineModal'
import CitationDrawer from './rag/CitationDrawer'
import UploadReportModal from './runs/UploadReportModal'
import TransitionReasonDialog from './testManagement/TransitionReasonDialog'
import { DetailPane } from './ui/inPage/DetailPane'
import { InlineConfirm } from './ui/inPage/InlineConfirm'
import { InlineFormSection } from './ui/inPage/InlineFormSection'
import AIGenerateModal from '@/pages/test-management/AIGenerateModal'
import InsightsPanel from '@/pages/test-management/InsightsPanel'
import { ModalWrap } from '@/pages/test-management/tmUi'
import ActivityDrawer from './activity/ActivityDrawer'
import ChartCustomisePanel from './charts/ChartCustomisePanel'
import FailureGroupPanel from './charts/failureGroups/FailureGroupPanel'
import FixTaggingPanel from './coverage/FixTaggingPanel'
import EmailReportDialog from './trends/EmailReportDialog'
import PinNoteDialog from './trends/PinNoteDialog'
import { CreatedKeyModal } from '@/pages/settings/ApiKeysPage'
import CaseEditForm from '@/pages/test-management/CaseEditForm'
import { MemoryRouter } from 'react-router-dom'

type Render = (onClose: () => void) => ReactElement

interface Container {
  render: Render
  /** The control that must hold focus once it opens, found inside its root. */
  focus: (root: HTMLElement) => HTMLElement
}

const PRIMITIVES: Array<[string, Container]> = [
  ['InlineFormSection', {
    render: (onClose) => (
      <InlineFormSection
        title="Edit Release"
        closeLabel="Close release dialog"
        onClose={onClose}
        footer={(
          <>
            <button type="button" onClick={onClose}>Cancel</button>
            <button type="button">Save Changes</button>
          </>
        )}
      >
        <label>Name <input aria-label="Name" /></label>
        <label>Notes <textarea aria-label="Notes" /></label>
      </InlineFormSection>
    ),
    focus: (root) => within(root).getByRole('textbox', { name: 'Name' }),
  }],
  ['DetailPane', {
    render: (onClose) => (
      <DetailPane title="2026.11" closeLabel="Close release" onClose={onClose} onToggleExpand={vi.fn()}
        footer={<a href="/releases/r-1">Open full page</a>}
      >
        <button type="button">Evaluate gate</button>
      </DetailPane>
    ),
    // No field: the first control, the header's Expand.
    focus: (root) => within(root).getByRole('button', { name: 'Expand to full width' }),
  }],
  ['InlineConfirm', {
    render: (onClose) => (
      <InlineConfirm
        message={<>Delete this release and all its phases? This cannot be undone.</>}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={vi.fn()}
        onCancel={onClose}
      />
    ),
    focus: (root) => within(root).getByRole('button', { name: 'Cancel' }),
  }],
]

// The six W27a forms, rendered exactly as modalKeyboardContract rendered them.
const CONVERTED: Array<[string, Container]> = [
  ['CorrectClassificationModal', {
    render: (onClose) => (
      <CorrectClassificationModal testName="checkout" analysisId="a-1" currentCategory="FLAKY" onClose={onClose} />
    ),
    focus: (root) => within(root).getByLabelText(/^Comment/),
  }],
  ['DefectPromotionModal', {
    render: (onClose) => (
      <DefectPromotionModal runId="r-1" clusterId="c-1" clusterLabel="NPE cluster" onClose={onClose} />
    ),
    focus: (root) => within(root).getByLabelText(/^Title/),
  }],
  ['CreateJiraIssueModal', {
    render: (onClose) => (
      <CreateJiraIssueModal projectId="p-1" fingerprint="fp" testName="checkout" onClose={onClose} />
    ),
    focus: (root) => within(root).getByLabelText(/^Jira project/),
  }],
  ['DefectIntakeModal', {
    render: (onClose) => <DefectIntakeModal projectId="p-1" onClose={onClose} />,
    focus: (root) => within(root).getByLabelText(/^Title/),
  }],
  ['ProposeQuarantineModal', {
    render: (onClose) => (
      <ProposeQuarantineModal
        prefill={{ project_id: 'p-1', test_fingerprint: 'fp', test_name: 'checkout' }}
        source="test"
        onClose={onClose}
      />
    ),
    focus: (root) => within(root).getByLabelText(/^Reason/),
  }],
  ['UploadReportModal', {
    render: (onClose) => (
      <UploadReportModal projectId="p-1" onClose={onClose} onSuccess={vi.fn()} />
    ),
    // The never-shown file input is aria-hidden; the first field is Format.
    focus: (root) => within(root).getByLabelText('Format'),
  }],
]

// W27b: the test library's containers (G-A), then the other groups' as they land.
const W27B: Array<[string, Container]> = [
  ['TransitionReasonDialog', {
    render: (onClose) => (
      <TransitionReasonDialog
        title="Archive" description="Archive this case." confirmLabel="Archive"
        onCancel={onClose} onConfirm={vi.fn()}
      />
    ),
    focus: (root) => within(root).getByRole('textbox', { name: /^Reason/ }),
  }],
  ['ModalWrap (the test library form frame)', {
    render: (onClose) => (
      <ModalWrap title="New Test Plan" onClose={onClose}>
        <label htmlFor="kc-plan-name">Plan Name *</label>
        <input id="kc-plan-name" />
        <button type="button" onClick={onClose}>Cancel</button>
      </ModalWrap>
    ),
    focus: (root) => within(root).getByLabelText('Plan Name *'),
  }],
  ['AIGenerateModal', {
    render: (onClose) => <AIGenerateModal projectId="p-1" onClose={onClose} />,
    focus: (root) => within(root).getByRole('textbox', { name: 'What to generate test cases for' }),
  }],
  ['InsightsPanel', {
    render: (onClose) => (
      <InsightsPanel open onClose={onClose}>
        <button type="button">Sign in with SSO</button>
      </InsightsPanel>
    ),
    // A read-only section: no field, so its first control, the X.
    focus: (root) => within(root).getByRole('button', { name: 'Close panel' }),
  }],
  ['CitationDrawer', {
    render: (onClose) => (
      <CitationDrawer
        caseIndex={0}
        onClose={onClose}
        citations={[{
          case_index: 0, vector_id: 'v-1', source_id: 's-1', source_title: 'Checkout requirements',
          section_heading: 'Cart', chunk_text_preview: 'The cart survives a reload.', relevance_score: 0.9,
          canonical_url: 'https://wiki.example.test/checkout',
        }]}
      />
    ),
    focus: (root) => within(root).getByRole('button', { name: 'Close' }),
  }],
  // G-B (PAN-05b)
  ['PinNoteDialog', {
    render: (onClose) => (
      <PinNoteDialog range={{ from: '2026-10-10', to: '2026-10-12' }} suite={null} onClose={onClose} />
    ),
    // Its note field keeps autoFocus (the first field would be the From date).
    focus: (root) => within(root).getByRole('textbox'),
  }],
  ['EmailReportDialog', {
    render: (onClose) => (
      <MemoryRouter>
        <EmailReportDialog
          projectId="p-1" days={14} suiteFilter={null} release={null} suiteLabel={null}
          onClose={onClose}
        />
      </MemoryRouter>
    ),
    focus: (root) => root.querySelector<HTMLElement>('[data-email-address]') as HTMLElement,
  }],
  ['ActivityDrawer', {
    render: (onClose) => <ActivityDrawer projectId="p-1" eventId="ev-1" onClose={onClose} />,
    // A read-only detail: no field, so its first control, the X.
    focus: (root) => within(root).getByRole('button', { name: 'Close activity details' }),
  }],
  ['FailureGroupPanel', {
    render: (onClose) => (
      <FailureGroupPanel
        trendGrain={null}
        onClose={onClose}
        onViewRows={vi.fn()}
        group={{
          id: 'sig-1', rank: 1, label: 'AssertionError: expected 200', failureCount: 12, affectedTests: 3,
          affectedRuns: 4, distinctRawLines: 1, firstSeen: '2026-10-01T00:00:00Z', lastSeen: '2026-10-10T00:00:00Z',
          share: 0.4, categories: [], dominantCategory: null, trend: [], topTests: [],
        }}
      />
    ),
    focus: (root) => within(root).getByRole('button', { name: 'Close panel' }),
  }],
  ['ChartCustomisePanel', {
    render: (onClose) => (
      <ChartCustomisePanel
        chartTitle="Pass rate by suite"
        spec={{
          fields: [
            { id: 'title', label: 'Title', kind: 'text', value: '' },
            { id: 'top', label: 'Lines per panel', kind: 'select', value: '5', options: [{ value: '3', label: '3' }, { value: '5', label: '5' }] },
          ],
          unavailable: [],
          isDefault: false,
        }}
        onField={vi.fn()}
        onReset={vi.fn()}
        onClose={onClose}
      />
    ),
    focus: (root) => within(root).getByLabelText('Title'),
  }],
  ['FixTaggingPanel', {
    render: (onClose) => (
      <MemoryRouter>
        <FixTaggingPanel projectId="p-1" days={30} onClose={onClose} />
      </MemoryRouter>
    ),
    // No role in the auth store here, so no move controls: the first control is the X.
    focus: (root) => within(root).getByRole('button', { name: 'Close the fix tagging panel' }),
  }],
  // G-C (PAN-06a): the one-time key, shown once in the page.
  ['CreatedKeyModal', {
    render: (onClose) => (
      <CreatedKeyModal
        created={{
          id: 'k-1', name: 'CI key', key_hint: 'tlk_…9f', scopes: ['stream:write'], project_id: 'p-1', is_active: true,
          expires_at: null, last_used_at: null, created_at: '2026-10-10T00:00:00Z', raw_key: 'tlk_fixture_not_a_secret',
        }}
        baseUrl="https://example.test"
        projectLabel="Project A"
        onClose={onClose}
      />
    ),
    focus: (root) => within(root).getByRole('button', { name: 'Close' }),
  }],
  // W28 G-C (TCM-01): the test-case edit form, in the case pane's Details tab.
  ['CaseEditForm', {
    render: (onClose) => (
      <CaseEditForm
        caseItem={{
          id: 'case-1', project_id: 'p-1', title: 'Sign in with SSO', test_type: 'functional', priority: 'high',
          severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
          automation_status: 'not_automated', ai_generated: false, created_at: '2026-10-01T00:00:00Z',
          updated_at: '2026-10-01T00:00:00Z', steps: [{ step_number: 1, action: 'Open the login page' }],
        }}
        suiteOptions={['Login']}
        onCancel={onClose}
        onSaved={vi.fn()}
      />
    ),
    focus: (root) => within(root).getByRole('textbox', { name: 'Title' }),
  }],
]

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
const IN_PAGE_ROOT = '[data-in-page], [data-inline-confirm]'

function Harness({ container, onClose }: { container: Render; onClose: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div data-testid="page">
      <button type="button" onClick={() => setOpen(true)}>Open it</button>
      {open && container(() => {
        onClose()
        setOpen(false)
      })}
      <button type="button">After the container</button>
    </div>
  )
}

function open(container: Render) {
  const onClose = vi.fn()
  render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <Harness container={container} onClose={onClose} />
    </SWRConfig>,
  )
  const trigger = screen.getByRole('button', { name: 'Open it' })
  trigger.focus()
  fireEvent.click(trigger)
  const root = document.querySelector<HTMLElement>(IN_PAGE_ROOT)
  if (!root) throw new Error('no in-page container rendered')
  const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.closest('[aria-hidden="true"]'),
  )
  return { trigger, root, items, onClose }
}

/** The container opened from a trigger INSIDE an outer in-page container (a detail pane). */
function NestedHarness({ container, onInnerClose, onPaneClose }: {
  container: Render
  onInnerClose: () => void
  onPaneClose: () => void
}) {
  const [paneOpen, setPaneOpen] = useState(true)
  const [open, setOpen] = useState(false)
  if (!paneOpen) return <p>Pane closed</p>
  return (
    <DetailPane
      title="Outer pane"
      closeLabel="Close outer pane"
      onClose={() => {
        onPaneClose()
        setPaneOpen(false)
      }}
    >
      <button type="button" onClick={() => setOpen(true)}>Open it</button>
      {open && container(() => {
        onInnerClose()
        setOpen(false)
      })}
    </DetailPane>
  )
}

function inPageContract({ render: container, focus }: Container) {
  it('renders in the page: no dialog role, no aria-modal, inside its parent (no portal)', () => {
    const { root } = open(container)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.getByTestId('page')).toContainElement(root)
  })

  it('takes focus when it opens, on its first field (else its first control)', () => {
    const { root } = open(container)
    expect(root.contains(document.activeElement)).toBe(true)
    expect(focus(root)).toHaveFocus()
  })

  it('does not trap Tab: the key on the last control (and Shift+Tab on the first) is not prevented', () => {
    const { items } = open(container)
    expect(items.length).toBeGreaterThan(1)
    const first = items[0]
    const last = items[items.length - 1]

    last.focus()
    expect(fireEvent.keyDown(last, { key: 'Tab' })).toBe(true)
    expect(last).toHaveFocus()

    first.focus()
    expect(fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })).toBe(true)
    expect(first).toHaveFocus()
  })

  it('closes on Escape (focus inside) and returns focus to its trigger', () => {
    const { trigger, onClose } = open(container)
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(document.querySelector(IN_PAGE_ROOT)).toBeNull()
    expect(trigger).toHaveFocus()
  })

  it('owns Escape inside an outer in-page container: it closes alone, the outer pane stays', () => {
    const onInnerClose = vi.fn()
    const onPaneClose = vi.fn()
    render(
      <SWRConfig value={{ provider: () => new Map() }}>
        <NestedHarness container={container} onInnerClose={onInnerClose} onPaneClose={onPaneClose} />
      </SWRConfig>,
    )
    const pane = screen.getByRole('complementary', { name: 'Outer pane' })
    const trigger = within(pane).getByRole('button', { name: 'Open it' })
    trigger.focus()
    fireEvent.click(trigger)
    const inner = Array.from(pane.querySelectorAll<HTMLElement>(IN_PAGE_ROOT))[0]
    expect(inner).toBeDefined()
    expect(inner.contains(document.activeElement)).toBe(true)

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onInnerClose).toHaveBeenCalledTimes(1)
    expect(onPaneClose).not.toHaveBeenCalled()
    expect(screen.getByRole('complementary', { name: 'Outer pane' })).toBeInTheDocument()
    expect(trigger).toHaveFocus()

    // The next Escape, now from the pane itself, closes the pane.
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(onPaneClose).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Pane closed')).toBeInTheDocument()
  })
}

describe.each(PRIMITIVES)('%s in-page keyboard contract', (_name, container) => {
  inPageContract(container)
})

describe.each(CONVERTED)('%s in-page keyboard contract', (_name, container) => {
  inPageContract(container)
})

describe.each(W27B)('%s in-page keyboard contract', (_name, container) => {
  inPageContract(container)
})
