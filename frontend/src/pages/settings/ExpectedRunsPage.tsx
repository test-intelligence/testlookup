/**
 * Settings › Project › Expected runs (W23a TRND-03): `/settings/expected-runs`.
 *
 * TestLookup's own EXPECTATION of when a run arrives ("a run of <suite | any run> by 06:00 on Mon–Fri in
 * Europe/Berlin"), so a missing one is noticed — observe only: nothing here starts, stops or pauses CI.
 * For ONE project (All Projects renders the project prompt). Reads on load: the cadences and their last
 * 14 days (`GET …/run-cadences`, `GET …/run-cadences/status?days=14`); nothing polls.
 *
 * A QA lead (or an admin) adds, edits, pauses, resumes and removes, each in the page: Add opens an
 * `InlineFormSection` above the table, Edit / Pause an `InlineRowSection` under the row, Delete an
 * `InlineConfirm` under the row, Resume posts at once — one open form at a time (`useInlineSlot`). Anyone
 * else who opens the URL reads the table without actions. Every refusal is shown inline where it
 * happened (`suppressToast` on every write). Every name and reason is React text.
 *
 * Widths (CI's DejaVu Sans): the 730 px settings Panel at 1280 (`card !p-0`) holds Expectation (the rest, ≈ 442 px) ·
 * State 200 ("until Fri Sep 25, 23:59" ≈ 182 px at 12 px) · Actions 88 ("Resume" ≈ 67 px) under `table-fixed`; the
 * summary and a long reason wrap inside their columns by design.
 */
import { Fragment, useState, type ReactNode } from 'react'
import { CadenceForm, PauseForm } from '@/components/runCadence/CadenceForm'
import {
  ADD_LABEL,
  ANY_RUN_LABEL,
  DELETE_QUESTION,
  EMPTY_TEXT,
  LOAD_ERROR,
  PAGE_SUBTITLE,
  PAGE_TITLE,
  PANEL_TITLE,
  PROJECT_PROMPT,
  STATUS_LOAD_ERROR,
  emptyForm,
  fromCadence,
  pauseBody,
  refusalMessage,
  stateLines,
  statusLine,
  toCreateBody,
  toPatchBody,
  type CadenceFormState,
} from '@/components/runCadence/cadenceFormModel'
import { helpTopicParam } from '@/components/help/helpTopics'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import { Panel } from '@/components/ui/PanelHeader'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { SEVERITY_STYLE } from '@/components/ui/severity'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import InlineRowSection from '@/components/ui/inPage/InlineRowSection'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { refreshRunCadences, useRunCadenceStatus, useRunCadences } from '@/hooks/useRunCadences'
import { usePermissions } from '@/hooks/usePermissions'
import {
  createCadence,
  deleteCadence,
  pauseCadence,
  resumeCadence,
  updateCadence,
} from '@/services/runCadenceService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { RunCadence, RunCadenceStatusItem } from '@/types/runCadence'

const HELP_TOPIC = helpTopicParam('/settings/expected-runs')
const ACTION = 'block text-left text-xs font-medium text-[var(--color-accent)] hover:underline disabled:opacity-50'
const PAUSED_PILL = SEVERITY_STYLE.P1

type SlotKey = 'add' | `edit:${string}` | `pause:${string}`

export default function ExpectedRunsPage() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null

  if (!projectId) {
    return (
      <div className="space-y-4">
        <PageHeader compact title={PAGE_TITLE} subtitle={PAGE_SUBTITLE} helpTopic={HELP_TOPIC} />
        <ProjectRequiredEmptyState description={PROJECT_PROMPT} />
      </div>
    )
  }
  return <ExpectedRunsForProject key={projectId} projectId={projectId} />
}

function ExpectedRunsForProject({ projectId }: { projectId: string }) {
  const { isQaLead: canEdit } = usePermissions()
  const { data, error, isLoading, mutate } = useRunCadences(projectId)
  const status = useRunCadenceStatus(projectId, 14)
  const slot = useInlineSlot<SlotKey>()
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null)

  const cadences = data ?? []
  const statusById = new Map((status.data?.items ?? []).map((item) => [item.cadence.id, item]))
  const refresh = () => refreshRunCadences(projectId)

  async function add(form: CadenceFormState) {
    await createCadence(projectId, toCreateBody(form))
    slot.close()
    await refresh()
  }

  async function edit(cadence: RunCadence, form: CadenceFormState) {
    const body = toPatchBody(form, cadence)
    if (Object.keys(body).length > 0) await updateCadence(projectId, cadence.id, body)
    slot.close()
    await refresh()
  }

  async function pause(cadence: RunCadence, untilLocal: string, reason: string) {
    await pauseCadence(projectId, cadence.id, pauseBody(untilLocal, reason))
    slot.close()
    await refresh()
  }

  async function act(cadence: RunCadence, send: () => Promise<unknown>, fallback: string) {
    setBusy(cadence.id)
    setRowError(null)
    try {
      await send()
      setConfirmingDelete(null)
      await refresh()
    } catch (err) {
      setRowError({ id: cadence.id, message: refusalMessage(err, fallback) })
    } finally {
      setBusy(null)
    }
  }

  const openRow = (key: SlotKey) => {
    setConfirmingDelete(null)
    setRowError(null)
    slot.open(key)
  }

  const addButton = canEdit ? (
    <button type="button" className="btn-primary btn-sm" onClick={() => openRow('add')}>
      {ADD_LABEL}
    </button>
  ) : undefined

  return (
    <div className="space-y-4">
      <PageHeader compact title={PAGE_TITLE} subtitle={PAGE_SUBTITLE} helpTopic={HELP_TOPIC} />
      <Panel title={PANEL_TITLE} actions={addButton} data-testid="expected-runs-panel">
        {canEdit && slot.openKey === 'add' && (
          <div className="border-b border-[var(--color-border)] p-4">
            <InlineFormSection title={ADD_LABEL} headingLevel={3} onClose={slot.close} slot={slot} testId="expected-runs-add">
              <CadenceForm initial={emptyForm()} onSubmit={add} onCancel={slot.close} />
            </InlineFormSection>
          </div>
        )}
        {error && !data ? (
          <div className="space-y-2 p-4" role="alert" data-expected-runs-error="">
            <p className="m-0 text-sm font-medium text-[var(--color-text)]">{LOAD_ERROR}</p>
            <button type="button" className="btn-secondary btn-sm" onClick={() => void mutate()}>Retry</button>
          </div>
        ) : isLoading && !data ? (
          <div className="flex justify-center py-10">
            <LoadingSpinner size="lg" />
          </div>
        ) : cadences.length === 0 ? (
          <div className="space-y-3 p-4" data-expected-runs-empty="">
            <p className="m-0 text-sm text-[var(--color-text-muted)]">{EMPTY_TEXT}</p>
            {/* W23a demo: the open Add form above already is the action; a second Add button under it read twice. */}
            {slot.openKey !== 'add' && addButton}
          </div>
        ) : (
          <CadenceTable
            cadences={cadences}
            canEdit={canEdit}
            statusById={statusById}
            statusFailed={Boolean(status.error) && !status.data}
            slotKey={slot.openKey}
            confirmingDelete={confirmingDelete}
            busy={busy}
            rowError={rowError}
            renderEdit={(c) => <CadenceForm initial={fromCadence(c)} onSubmit={(form) => edit(c, form)} onCancel={slot.close} />}
            renderPause={(c) => <PauseForm onSubmit={(until, reason) => pause(c, until, reason)} onCancel={slot.close} />}
            onEdit={(c) => openRow(`edit:${c.id}`)}
            onPause={(c) => openRow(`pause:${c.id}`)}
            onResume={(c) => void act(c, () => resumeCadence(projectId, c.id), 'The expected cadence could not be resumed.')}
            onAskDelete={(c) => {
              slot.close()
              setRowError(null)
              setConfirmingDelete(c.id)
            }}
            onCancelDelete={() => setConfirmingDelete(null)}
            onDelete={(c) => void act(c, () => deleteCadence(projectId, c.id), 'The expected cadence could not be removed.')}
          />
        )}
      </Panel>
    </div>
  )
}

interface CadenceTableProps {
  cadences: RunCadence[]
  canEdit: boolean
  statusById: Map<string, RunCadenceStatusItem>
  statusFailed: boolean
  slotKey: SlotKey | null
  confirmingDelete: string | null
  busy: string | null
  rowError: { id: string; message: string } | null
  renderEdit: (c: RunCadence) => ReactNode
  renderPause: (c: RunCadence) => ReactNode
  onEdit: (c: RunCadence) => void
  onPause: (c: RunCadence) => void
  onResume: (c: RunCadence) => void
  onAskDelete: (c: RunCadence) => void
  onCancelDelete: () => void
  onDelete: (c: RunCadence) => void
}

function CadenceTable(props: CadenceTableProps) {
  const { cadences, canEdit, statusById, statusFailed, slotKey, confirmingDelete, busy, rowError } = props
  const columns = canEdit ? 3 : 2
  return (
    <Table density="dense" className="table-fixed" aria-label={PANEL_TITLE} data-expected-runs="">
      <colgroup>
        <col />
        <col className="w-[200px]" />
        {canEdit && <col className="w-[88px]" />}
      </colgroup>
      <thead>
        <tr>
          <Th>Expectation</Th>
          <Th>State</Th>
          {canEdit && <Th>Actions</Th>}
        </tr>
      </thead>
      <tbody>
        {cadences.map((c) => {
          const scope = c.suite_name ?? ANY_RUN_LABEL
          const state = stateLines(c)
          const rowBusy = busy === c.id
          return (
            <Fragment key={c.id}>
              <Tr interactive={false} data-cadence-id={c.id}>
                <Td className="break-words align-top">
                  <div className="font-medium text-[var(--color-text)]">{scope}</div>
                  <div className="text-xs text-[var(--color-text-secondary)]">{c.summary}</div>
                  <div className="text-xs text-[var(--color-text-muted)]" data-status-line="">
                    {statusFailed ? STATUS_LOAD_ERROR : statusLine(statusById.get(c.id))}
                  </div>
                </Td>
                <Td className="break-words align-top" data-cadence-state={state.paused ? 'paused' : 'active'}>
                  {state.paused ? (
                    <span
                      className="inline-flex rounded-full border px-2 py-0.5 text-xs font-medium"
                      style={{ color: PAUSED_PILL.fg, background: PAUSED_PILL.bg, borderColor: PAUSED_PILL.bd }}
                    >
                      {state.label}
                    </span>
                  ) : (
                    <span className="text-sm text-[var(--color-text)]">{state.label}</span>
                  )}
                  {state.lines.map((line, i) => (
                    <div key={i} className="mt-0.5 text-xs text-[var(--color-text-muted)]">{line}</div>
                  ))}
                </Td>
                {canEdit && (
                  <Td className="align-top">
                    <div className="flex flex-col items-start gap-1">
                      <button type="button" className={ACTION} aria-label={`Edit ${scope}`} onClick={() => props.onEdit(c)}>
                        Edit
                      </button>
                      {c.paused ? (
                        <button type="button" className={ACTION} disabled={rowBusy} aria-label={`Resume ${scope}`} onClick={() => props.onResume(c)}>
                          Resume
                        </button>
                      ) : (
                        <button type="button" className={ACTION} aria-label={`Pause ${scope}`} onClick={() => props.onPause(c)}>
                          Pause
                        </button>
                      )}
                      <button
                        type="button"
                        className="block text-left text-xs font-medium text-[var(--ui-danger-ink)] hover:underline"
                        aria-label={`Delete ${scope}`}
                        onClick={() => props.onAskDelete(c)}
                      >
                        Delete
                      </button>
                    </div>
                  </Td>
                )}
              </Tr>
              {canEdit && slotKey === `edit:${c.id}` && (
                <InlineRowSection colSpan={columns} dataAttributes={{ 'data-cadence-edit': c.id }}>{props.renderEdit(c)}</InlineRowSection>
              )}
              {canEdit && slotKey === `pause:${c.id}` && (
                <InlineRowSection colSpan={columns} dataAttributes={{ 'data-cadence-pause': c.id }}>{props.renderPause(c)}</InlineRowSection>
              )}
              {canEdit && confirmingDelete === c.id && (
                <InlineRowSection colSpan={columns}>
                  <InlineConfirm
                    message={DELETE_QUESTION}
                    confirmLabel="Delete"
                    tone="danger"
                    busy={rowBusy}
                    onConfirm={() => props.onDelete(c)}
                    onCancel={props.onCancelDelete}
                  />
                </InlineRowSection>
              )}
              {rowError?.id === c.id && (
                <InlineRowSection colSpan={columns}>
                  <p role="alert" className="m-0 text-sm text-[var(--ui-danger-ink)]">{rowError.message}</p>
                </InlineRowSection>
              )}
            </Fragment>
          )
        })}
      </tbody>
    </Table>
  )
}
