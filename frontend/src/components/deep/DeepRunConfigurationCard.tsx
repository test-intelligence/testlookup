/**
 * W14 AGT-07: "Run configuration" — the pre-scan's signature groups as a selection, and the tighten-only
 * configuration of a deep run (the model tier, the failures analysed at most, the evidence sources).
 *
 * **Estimate** re-asks the estimate (a GET with the configuration as query values: nothing is queued);
 * **Run on selection** posts the selected groups' failure ids and the configuration. The server judges both
 * (tighten-only, ≤ 500 failures, the failures belong to this run); the card only refuses what it can see:
 * nothing selected, more than 500 failures, a group whose member list was cut. When the server's deep gate
 * refuses, its sentence stands where Run on selection would be. A role that cannot trigger sees the groups only.
 */
import type { ReactNode } from 'react'
import { Panel } from '@/components/ui/PanelHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import type { DeepPreScan, DeepRunConfiguration } from '@/types/agentOps'
import DeepPreScanList from './DeepPreScanList'
import {
  PRESCAN_EMPTY, PRESCAN_LOAD_ERROR, RUN_CONFIG_TOO_MANY, selectionBlock, selectionFromGroups, selectionLine,
  type DeepRunConfig, type Selection,
} from './deepRunModel'

export interface DeepRunConfigurationCardProps {
  preScan: DeepPreScan | undefined
  preScanError: unknown
  onRetryPreScan: () => void
  /** QA_ENGINEER: the selection and the controls; a lower role sees the groups only. */
  canConfigure: boolean
  /** The estimate's configuration answer (allowed tiers, the caps, the sources); undefined while it loads. */
  configuration: DeepRunConfiguration | undefined
  config: DeepRunConfig
  onChange: (next: DeepRunConfig) => void
  onEstimate: () => void
  estimating: boolean
  /** Null: a run may be offered. Else the gate's (or the toggle's) sentence, shown in its place. */
  blockedMessage: string | null
  onRun: () => void
  running: boolean
  /** What the last trigger answered ("Deep investigation already in progress", queued, a refusal). */
  runNote: string | null
}

const SELECT = 'rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1 text-xs text-[var(--color-text)]'

/** The tighten-only controls and the two actions (a QA engineer's only). */
function DeepRunControls({
  configuration, config, onChange, onEstimate, estimating, blockedMessage, onRun, running, runNote, selection, block,
}: DeepRunConfigurationCardProps & { selection: Selection; block: string | null }) {
  const projectTier = configuration?.project_model_tier
  const cap = configuration?.project_max_failures_analyzed
  const sources = configuration?.project_evidence_sources ?? []
  const chosenSources = config.evidenceSources ?? sources
  const setTier = (tier: string) => onChange({ ...config, modelTier: tier === projectTier ? null : tier })
  const setCap = (raw: string) => {
    const n = Number.parseInt(raw, 10)
    if (!raw || Number.isNaN(n)) return onChange({ ...config, maxFailures: null })
    // Never above the project's cap (the server would refuse a loosening): equal to it tightens nothing.
    onChange({ ...config, maxFailures: Math.max(1, cap != null ? Math.min(n, cap) : n) })
  }
  const toggleSource = (source: string) => {
    const next = chosenSources.includes(source) ? chosenSources.filter((s) => s !== source) : [...chosenSources, source]
    const same = next.length === sources.length && sources.every((s) => next.includes(s))
    onChange({ ...config, evidenceSources: same ? null : sources.filter((s) => next.includes(s)) })
  }
  const narrowingOff = configuration ? !configuration.tool_narrowing_allowed : true
  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-2.5" data-run-controls="">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {/* Short visible labels (the full names are the controls' accessible names): the row holds tier, cap and
            both actions on ONE line at 1280 px with ≥ 40 px to spare on CI's wider DejaVu font. */}
        <label className="inline-flex items-center gap-1.5 text-[var(--color-text-secondary)]">
          Tier
          <select
            aria-label="Model tier"
            className={SELECT}
            value={config.modelTier ?? projectTier ?? ''}
            onChange={(e) => setTier(e.target.value)}
            disabled={!configuration || configuration.allowed_tiers.length <= 1}
            title={configuration && configuration.allowed_tiers.length <= 1 ? 'This project does not allow a lower model tier' : undefined}
          >
            {(configuration?.allowed_tiers ?? []).map((tier) => <option key={tier} value={tier}>{tier}</option>)}
          </select>
        </label>
        <label className="inline-flex items-center gap-1.5 text-[var(--color-text-secondary)]">
          Max
          <input
            type="number"
            aria-label="Failures analysed at most"
            className={`${SELECT} w-20 tabular-nums`}
            min={1}
            max={cap}
            placeholder={cap != null ? String(cap) : undefined}
            value={config.maxFailures ?? ''}
            onChange={(e) => setCap(e.target.value)}
            disabled={!configuration}
          />
        </label>
        <button type="button" className="btn-secondary inline-flex items-center gap-1.5 text-xs" onClick={onEstimate} disabled={estimating}>
          {estimating && <LoadingSpinner size="sm" />}
          Estimate
        </button>
        {blockedMessage != null ? (
          <span data-testid="deep-config-gate-note" className="text-xs text-[var(--color-text-muted)]">{blockedMessage}</span>
        ) : (
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5 text-xs"
            onClick={onRun}
            disabled={block != null || running}
            title={block ?? `Run a deep investigation of the ${selection.failures} selected failures`}
          >
            {running && <LoadingSpinner size="sm" />}
            Run on selection
          </button>
        )}
      </div>
      {sources.length > 0 && (
        <fieldset className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 border-0 p-0 text-xs text-[var(--color-text-secondary)]">
          <legend className="float-left mr-2 p-0 text-[var(--color-text-muted)]">Evidence sources</legend>
          {sources.map((source) => (
            <label key={source} className="inline-flex items-center gap-1" title={narrowingOff ? 'This project does not allow narrowing the evidence sources' : undefined}>
              <input
                type="checkbox"
                checked={chosenSources.includes(source)}
                disabled={narrowingOff || (chosenSources.length === 1 && chosenSources.includes(source))}
                onChange={() => toggleSource(source)}
              />
              <span className="font-mono">{source}</span>
            </label>
          ))}
        </fieldset>
      )}
      {(selection.tooMany || selection.truncated) && (
        <p role="note" className="m-0 text-xs text-[var(--ui-warning-ink)]">{RUN_CONFIG_TOO_MANY}</p>
      )}
      {runNote && <p role="note" data-run-note="" className="m-0 text-xs text-[var(--color-text-secondary)]">{runNote}</p>}
    </div>
  )
}

export default function DeepRunConfigurationCard(props: DeepRunConfigurationCardProps) {
  const { preScan, preScanError, onRetryPreScan, canConfigure, config, onChange } = props
  const groups = preScan?.groups ?? []
  const selection = selectionFromGroups(groups, config.selected)
  const toggle = (id: string) =>
    onChange({
      ...config,
      selected: config.selected.includes(id) ? config.selected.filter((s) => s !== id) : [...config.selected, id],
    })
  const allSelected = groups.length > 0 && groups.every((g) => config.selected.includes(g.signature_id))
  const selectAll = () => onChange({ ...config, selected: allSelected ? [] : groups.map((g) => g.signature_id) })

  let body: ReactNode
  if (!preScan && preScanError) {
    body = (
      <p className="m-0 flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
        {PRESCAN_LOAD_ERROR}
        <button type="button" className="btn-secondary text-xs" onClick={onRetryPreScan}>Retry</button>
      </p>
    )
  } else if (!preScan) {
    body = <div className="flex justify-center py-4"><LoadingSpinner size="sm" /></div>
  } else if (!preScan.measured || groups.length === 0) {
    body = <p className="m-0 text-sm text-[var(--color-text-secondary)]">{PRESCAN_EMPTY}</p>
  } else {
    body = (
      <div className="flex flex-col gap-2">
        <p className="m-0 text-2xs text-[var(--color-text-muted)]">
          {preScan.refinement_note}
          {preScan.groups_total > groups.length && <> Showing the {groups.length} largest of {preScan.groups_total} groups.</>}
        </p>
        {canConfigure && (
          <div className="flex items-center justify-between gap-2 text-xs">
            <span data-selection-line="" className="tabular-nums text-[var(--color-text-secondary)]">{selectionLine(selection)}</span>
            <button type="button" className="btn-ghost text-xs" onClick={selectAll}>
              {allSelected ? 'Clear selection' : 'Select all'}
            </button>
          </div>
        )}
        <DeepPreScanList groups={groups} selected={config.selected} onToggle={canConfigure ? toggle : undefined} />
        {canConfigure && (
          <DeepRunControls {...props} selection={selection} block={selectionBlock(selection)} />
        )}
      </div>
    )
  }

  return (
    <Panel
      title="Run configuration"
      actions={preScan?.measured ? <span>{preScan.groups_total} signature groups</span> : undefined}
      data-testid="deep-run-configuration"
    >
      <div className="px-4 py-3">{body}</div>
    </Panel>
  )
}
