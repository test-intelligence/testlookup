/**
 * `/agents` › Try an agent (W23c AGT-11, D-14): invoke ONE agent on the run picked on the page, in the page.
 *
 * Rendered by `AgentStatusPage` only while its header button is pressed (or the URL carries `?invocation=`), so
 * the page's default load and height do not change; it reads NOTHING until it is open, then the catalog ONCE
 * (`useAgentCatalog(true)`), and an agent's detail only when that agent accepts inline input (the editor's
 * template is built from its schema). Never a modal, a side panel or a portal: an in-page `<section>`.
 *
 * The anchor is the page's own run picker (no second run list): with no run picked, Run is disabled and the
 * panel says why. Run sends `POST /api/v1/agents/{id}/invoke` with `input.payload.test_run_id` = the picked run,
 * `inline_input` when the inline radio is chosen, and a fresh `Idempotency-Key` per click. A refusal (413 / 422 /
 * 404, and a 422's `errors`) is shown under the editor in the server's words; an accepted call writes
 * `?invocation=<id>` (replace) and shows `InvocationDetail`. The editor's text is component state only.
 *
 * Widths (CI's DejaVu Sans, the 910 px card body at 1280): the form column is `max-w-3xl` (768 px); the Agent
 * select is 360 px; the radio row ≈ 708 px (wraps below 768 by design); the textarea fills the column.
 */
import { useId, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useAgentCatalog, useAgentCatalogAgent } from '@/hooks/useAgentInvoke'
import { invoke, newIdempotencyKey } from '@/services/agentInvokeService'
import { inlineTemplate, parseInvocation } from '@/types/agentInvoke'
import InvocationDetail from './InvocationDetail'
import {
  AGENT_LABEL,
  AGENT_LOAD_ERROR,
  ANCHOR_LABEL,
  CATALOG_LOAD_ERROR,
  CLOSE_LABEL,
  EDITOR_LABEL,
  INLINE_ERRORS,
  INLINE_RADIO,
  INLINE_UNSUPPORTED,
  INPUT_LABEL,
  NO_AGENTS,
  NO_PROJECT,
  NO_RUN,
  PANEL_TITLE,
  RUNNING_LABEL,
  RUN_LABEL,
  STORED_RADIO,
  agentOptionLabel,
  byteCounter,
  invokableAgents,
  overCap,
  parseInline,
  payloadBytes,
  refusalLines,
  requestBody,
  withInvocation,
} from './tryAgentModel'

const LABEL = 'text-xs font-medium text-[var(--color-text-secondary)]'
const HINT = 'm-0 text-xs text-[var(--color-text-muted)]'
const REFUSAL = 'm-0 text-xs text-[var(--ui-danger-ink)] [overflow-wrap:anywhere]'
const LINK_BUTTON = 'text-xs font-medium text-[var(--color-accent)] hover:underline'

type InputMode = 'stored' | 'inline'

export interface TryAgentPanelProps {
  /** The run picked on the page (`?run=`): the invocation's anchor. */
  runId: string | undefined
  /** How the page's picker names that run ("API Regression · Run #7"). */
  runLabel?: string | null
  /** The run's project (the body's `project_id` assertion); null when the page cannot tell. */
  projectId: string | null
  onClose: () => void
}

export default function TryAgentPanel({ runId, runLabel, projectId, onClose }: TryAgentPanelProps) {
  const ids = useId()
  const [searchParams, setSearchParams] = useSearchParams()
  const invocationId = searchParams.get('invocation')
  const catalog = useAgentCatalog(true)
  const agents = useMemo(() => invokableAgents(catalog.data), [catalog.data])
  const [agentId, setAgentId] = useState('')
  const agent = agents.find((a) => a.agent_id === agentId) ?? null
  const supportsInline = agent?.inline_input_supported === true
  const detail = useAgentCatalogAgent(supportsInline ? agentId : null)
  const [mode, setMode] = useState<InputMode>('stored')
  const [typed, setTyped] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string[] | null>(null)

  const inline = supportsInline && mode === 'inline'
  const text = typed ?? inlineTemplate(detail.data)
  const parsed = useMemo(() => parseInline(text), [text])
  const bytes = useMemo(() => payloadBytes(text, parsed), [text, parsed])
  const tooLarge = overCap(bytes)
  const inlineBlocked = inline && (!detail.data || !parsed.ok || tooLarge)
  const canRun = Boolean(runId && projectId && agent) && !busy && !inlineBlocked

  function pickAgent(id: string) {
    setAgentId(id)
    setTyped(null)
    setRefusal(null)
    const picked = agents.find((a) => a.agent_id === id)
    setMode(picked?.inline_input_supported ? 'inline' : 'stored')
  }

  function close() {
    if (invocationId) setSearchParams((current) => withInvocation(current, null), { replace: true })
    onClose()
  }

  async function run() {
    if (!runId || !projectId || !agent || !canRun) return
    setBusy(true)
    setRefusal(null)
    try {
      const body = requestBody(projectId, agent.agent_id, runId, inline && parsed.ok ? parsed.value : undefined)
      const answer = parseInvocation(await invoke(agent.agent_id, body, newIdempotencyKey()))
      if (answer) setSearchParams((current) => withInvocation(current, answer.id), { replace: true })
    } catch (err) {
      setRefusal(refusalLines(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section id="try-agent" aria-label={PANEL_TITLE} data-try-agent="" className="card space-y-3">
      <div className="flex items-start justify-between gap-3">
        <h2 className="m-0 text-base font-semibold text-[var(--color-text)]">{PANEL_TITLE}</h2>
        <button type="button" className={LINK_BUTTON} onClick={close}>{CLOSE_LABEL}</button>
      </div>
      <div className="max-w-3xl space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={`${ids}-agent`} className={LABEL}>{AGENT_LABEL}</label>
          <select
            id={`${ids}-agent`}
            value={agentId}
            onChange={(e) => pickAgent(e.target.value)}
            disabled={busy || !catalog.data}
            className="w-[360px] max-w-full truncate rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1 text-xs"
          >
            <option value="">Choose an agent…</option>
            {agents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>{agentOptionLabel(a)}</option>
            ))}
          </select>
          {!catalog.data && !catalog.error && <LoadingSpinner size="sm" />}
        </div>
        {catalog.error && !catalog.data && (
          <p className={HINT} data-try-agent-catalog-error="">
            {CATALOG_LOAD_ERROR}{' '}
            <button type="button" className="underline" onClick={() => void catalog.mutate()}>Retry</button>
          </p>
        )}
        {catalog.data && agents.length === 0 && <p className={HINT}>{NO_AGENTS}</p>}

        <div className="space-y-0.5" data-try-agent-anchor="">
          <span className={LABEL}>{ANCHOR_LABEL}</span>
          {runId ? (
            <p className="m-0 max-w-full truncate text-sm text-[var(--color-text)]" title={runLabel || runId}>{runLabel || runId}</p>
          ) : (
            <p className={HINT}>{NO_RUN}</p>
          )}
          {runId && !projectId && <p className={HINT}>{NO_PROJECT}</p>}
        </div>

        <fieldset className="m-0 space-y-1 border-0 p-0">
          <legend className={`mb-1 ${LABEL}`}>{INPUT_LABEL}</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[var(--color-text)]">
            <label className="inline-flex items-center gap-1.5">
              <input type="radio" name={`${ids}-mode`} checked={!inline} disabled={busy} onChange={() => setMode('stored')} />
              {STORED_RADIO}
            </label>
            <label className="inline-flex items-center gap-1.5">
              <input
                type="radio"
                name={`${ids}-mode`}
                checked={inline}
                disabled={busy || !supportsInline}
                onChange={() => setMode('inline')}
              />
              {INLINE_RADIO}
            </label>
          </div>
          {agent && !supportsInline && <p className={HINT}>{INLINE_UNSUPPORTED}</p>}
        </fieldset>

        {inline && (
          <div className="space-y-1" data-try-agent-editor="">
            {detail.error && !detail.data ? (
              <p className={HINT}>
                {AGENT_LOAD_ERROR}{' '}
                <button type="button" className="underline" onClick={() => void detail.mutate()}>Retry</button>
              </p>
            ) : !detail.data ? (
              <LoadingSpinner size="sm" />
            ) : (
              <>
                <textarea
                  aria-label={EDITOR_LABEL}
                  rows={14}
                  spellCheck={false}
                  disabled={busy}
                  value={text}
                  onChange={(e) => {
                    setTyped(e.target.value)
                    setRefusal(null)
                  }}
                  className="input font-mono text-xs"
                />
                <p className={HINT} data-try-agent-bytes="">{byteCounter(bytes)}</p>
                {!parsed.ok && <p className={REFUSAL} data-try-agent-parse-error="">{parsed.message}</p>}
                {tooLarge && <p className={REFUSAL} data-try-agent-too-large="">{INLINE_ERRORS.inline_input_too_large}</p>}
              </>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-primary btn-sm" disabled={!canRun} onClick={() => void run()}>
            {busy ? RUNNING_LABEL : RUN_LABEL}
          </button>
          {busy && <LoadingSpinner size="sm" />}
        </div>
        {refusal && (
          <div role="alert" className="space-y-0.5" data-try-agent-refusal="">
            {refusal.map((line, i) => (
              <p key={i} className={REFUSAL}>{line}</p>
            ))}
          </div>
        )}
      </div>
      {invocationId && (
        <InvocationDetail key={invocationId} invocationId={invocationId} anchor={runId ? { runId, label: runLabel ?? null } : null} />
      )}
    </section>
  )
}
