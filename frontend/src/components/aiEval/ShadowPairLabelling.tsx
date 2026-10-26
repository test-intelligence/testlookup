/**
 * The labelling queue of the Tiers tab (W16 AGT-13): the project's PENDING shadow pairs, one card each — the
 * incumbent and candidate outputs side by side, a link to the run, and Incumbent / Candidate / Both / Neither
 * (+ the failure category on a root-cause pair). A label is final: the card leaves the queue.
 *
 * The outputs are stored model text: DATA, rendered as `JSON.stringify` text inside a `<pre>` — never markup, never
 * a markdown renderer, never an `href` or a `style` from them. The run link is built here from the run's id only.
 * The default view reads the SAME key as the tab badge (`useShadowPairs(projectId, undefined, 'pending')`).
 */
import { useState } from 'react'
import { clsx } from 'clsx'
import { Link } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Panel } from '@/components/ui/PanelHeader'
import { useLabelShadowPair, useShadowPairs } from '@/hooks/useAiEval'
import { SHADOW_VERDICTS, SHADOW_VERDICT_LABELS, type ShadowPair } from '@/types/aiEval'
import {
  COPY,
  FAILURE_CATEGORIES,
  QUEUE_PAGE_SIZE,
  ROOT_CAUSE_AGENT_ID,
  TIER_AGENTS,
  expiresIn,
  outputText,
  serverMessage,
  sourceText,
  verdictBody,
} from './shadowPairModel'

const MUTED = 'text-[var(--color-text-muted)]'

/** The run link, built from the run id only (`/runs/{id}?tab=tests` for a test case). */
function runHref(pair: ShadowPair): string | null {
  const source = pair.source
  if (!source || !source.run_id) return null
  const base = `/runs/${encodeURIComponent(source.run_id)}`
  return source.kind === 'test_case' ? `${base}?tab=tests` : base
}

function OutputColumn({ title, output }: { title: string; output: unknown }) {
  return (
    <div className="min-w-0">
      <p className="eyebrow m-0 mb-1 text-[var(--color-text-secondary)]">{title}</p>
      <pre
        className="m-0 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-2 font-mono text-2xs text-[var(--color-text)]"
        data-pair-output=""
      >
        {outputText(output)}
      </pre>
    </div>
  )
}

interface CardProps {
  pair: ShadowPair
  now: Date
  onLabel: (pairId: string, body: ReturnType<typeof verdictBody>) => Promise<void>
}

function PairCard({ pair, now, onLabel }: CardProps) {
  const [category, setCategory] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const rootCause = pair.agent_id === ROOT_CAUSE_AGENT_ID
  const href = runHref(pair)
  const linkText = sourceText(pair.source)
  const expiry = expiresIn(pair.expires_at, now)
  const testName = pair.source?.kind === 'test_case' ? pair.source.label : null

  const label = async (verdict: string) => {
    setSaving(true)
    setError(null)
    try {
      await onLabel(pair.id, verdictBody(verdict, rootCause ? category : null, notes))
    } catch (err) {
      setError(serverMessage(err, COPY.labelFailed))
      setSaving(false)
    }
  }

  return (
    <li className="space-y-3 border-t border-[var(--color-border)] px-4 py-3 first:border-t-0" data-shadow-pair={pair.id}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span className="font-medium text-[var(--color-text)]">{pair.agent_label}</span>
        <span aria-hidden className={MUTED}>·</span>
        {href && linkText ? (
          <Link to={href} className="text-[var(--color-accent)] hover:underline" data-pair-source="">{linkText}</Link>
        ) : (
          <span className={MUTED} data-pair-source-gone="">{COPY.sourceGone}</span>
        )}
        {testName && (
          <span className={clsx(MUTED, 'max-w-[18rem] truncate font-mono')} title={testName}>{testName}</span>
        )}
        {expiry && (
          <>
            <span aria-hidden className={MUTED}>·</span>
            <span className={MUTED}>{expiry}</span>
          </>
        )}
      </div>
      <div className="grid grid-cols-2 gap-4">
        <OutputColumn title={`Incumbent · ${pair.incumbent_tier}`} output={pair.incumbent_output} />
        <OutputColumn title={`Candidate · ${pair.candidate_tier}`} output={pair.candidate_output} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {rootCause && (
          <select
            aria-label="Failure category"
            className="input w-56 text-xs" /* w-44 cut "Failure category (optional)" on CI's DejaVu */
            value={category}
            disabled={saving}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">Failure category (optional)</option>
            {FAILURE_CATEGORIES.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        )}
        <input
          aria-label="Notes"
          className="input w-56 text-xs"
          placeholder="Notes (optional)"
          maxLength={500}
          value={notes}
          disabled={saving}
          onChange={(e) => setNotes(e.target.value)}
        />
        {SHADOW_VERDICTS.map((verdict) => (
          <button
            key={verdict}
            type="button"
            className="btn-secondary btn-sm"
            disabled={saving}
            onClick={() => { void label(verdict) }}
          >
            {SHADOW_VERDICT_LABELS[verdict]}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]" data-pair-error="">{error}</p>}
    </li>
  )
}

export default function ShadowPairLabelling({ projectId }: { projectId: string }) {
  const [agentId, setAgentId] = useState<string | undefined>(undefined)
  const [offset, setOffset] = useState(0)
  // Labelled here: hidden at once (the list revalidates behind it), so a card never takes a second click.
  const [labelled, setLabelled] = useState<ReadonlySet<string>>(new Set())
  const { data, error, isLoading, mutate } = useShadowPairs(projectId, agentId, 'pending', offset)
  const labelPair = useLabelShadowPair(projectId)
  const now = new Date()

  const listed = Array.isArray(data?.items) ? data.items : []
  const items = listed.filter((pair) => !labelled.has(pair.id))
  // A pair labelled here but still in a not-yet-revalidated answer is not pending any more.
  const stale = listed.length - items.length
  const total = Math.max(0, (typeof data?.total === 'number' ? data.total : listed.length) - stale)
  const pendingTotal = typeof data?.pending_total === 'number' ? Math.max(0, data.pending_total - stale) : null
  const seen = data?.live_tier_pairs_seen !== false
  const pages = Math.max(1, Math.ceil(total / QUEUE_PAGE_SIZE))
  const page = Math.floor(offset / QUEUE_PAGE_SIZE) + 1

  const onLabel = async (pairId: string, body: ReturnType<typeof verdictBody>) => {
    await labelPair(pairId, body)
    setLabelled((prev) => new Set(prev).add(pairId))
  }
  const pick = (next: string | undefined) => {
    setAgentId(next)
    setOffset(0)
  }

  const chips = (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Agent">
      {[{ id: undefined, label: 'All agents' }, ...TIER_AGENTS].map((agent) => (
        <button
          key={agent.id ?? 'all'}
          type="button"
          aria-pressed={agentId === agent.id}
          className={clsx('btn-ghost btn-sm', agentId === agent.id && 'text-[var(--color-accent)]')}
          onClick={() => pick(agent.id)}
        >
          {agent.label}
        </button>
      ))}
    </div>
  )

  let body
  if (isLoading && !data) {
    body = <div className="flex justify-center p-4"><LoadingSpinner size="sm" /></div>
  } else if (error && !data) {
    body = (
      <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]" data-pairs-error="">
        {COPY.loadError.pairs}{' '}
        <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
      </p>
    )
  } else if (!seen) {
    body = <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]" data-pairs-empty="none">{COPY.noPairs}</p>
  } else if (items.length === 0) {
    body = (
      <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]" data-pairs-empty="labelled">
        {agentId && pendingTotal ? 'No pending pairs for this agent.' : COPY.allLabelled}
      </p>
    )
  } else {
    body = (
      <ul className="m-0 list-none p-0">
        {items.map((pair) => <PairCard key={pair.id} pair={pair} now={now} onLabel={onLabel} />)}
      </ul>
    )
  }

  return (
    <Panel
      title={`Label live pairs (${pendingTotal ?? '—'})`}
      subtitle="A label is final; a pending pair is deleted after 30 days."
      actions={pages > 1 ? (
        <>
          <button
            type="button"
            className="btn-secondary btn-sm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - QUEUE_PAGE_SIZE))}
          >
            Previous
          </button>
          <span>Page {page} of {pages}</span>
          <button
            type="button"
            className="btn-secondary btn-sm"
            disabled={page >= pages}
            onClick={() => setOffset(offset + QUEUE_PAGE_SIZE)}
          >
            Next
          </button>
        </>
      ) : undefined}
      data-testid="shadow-pair-queue"
    >
      {seen && data && <div className="border-b border-[var(--color-border)] px-4 py-2">{chips}</div>}
      {body}
    </Panel>
  )
}
