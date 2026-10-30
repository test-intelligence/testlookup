/**
 * W23d AGT-15 — Settings › Integrations › the Prometheus card (a full-width
 * card after Teams, part of the page's ONE form and its ONE Save).
 *
 * The page owns the form: it passes `form` / `upd` / `fieldErrors` /
 * `isAdmin` / `config`, and renders the URL and token inputs itself (its own
 * `Field` / `SecretField`, passed as `fields`, like every other card). This
 * card adds the metric templates editor and `Test connection`, which calls the
 * EXISTING probe route (`provider=prometheus`) against the SAVED settings and
 * shows the answer inline; it holds only that answer.
 *
 * Templates are the only queries the investigation can run: an id, a label, a
 * unit, and PromQL naming the service as `{service}`. A field-level 422 on the
 * list renders its sentence under it.
 */
import { useState, type ReactNode } from 'react'
import type { IntegrationsConfigRead, IntegrationsConfigUpdate } from '@/services/appSettingsService'
import { triggerProbe } from '@/services/integrationHealthService'
import { extractErrorMessage } from '@/services/apiErrors'
import { TEMPLATE_UNITS, type PrometheusMetricTemplate } from '@/types/prometheus'
import {
  ADD_TEMPLATE,
  PROMETHEUS_INTRO,
  PROMETHEUS_NOT_CONFIGURED,
  PROMETHEUS_TEMPLATES_FIELD,
  PROMETHEUS_TITLE,
  REMOVE_TEMPLATE,
  TEMPLATES_HEADING,
  TEMPLATES_HELP,
  TEMPLATES_MAX,
  TEMPLATE_UNIT_LABELS,
  TESTING,
  TEST_CONNECTION,
  TEST_FAILED,
  TEST_SAVED_NOTE,
  USE_DEFAULTS,
  blankTemplate,
  probeLine,
  templatesNote,
} from './integrationsForm'

const NEUTRAL_BADGE = 'badge border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]'
const CELL = 'input disabled:opacity-50'

export interface PrometheusCardProps {
  config: IntegrationsConfigRead
  form: IntegrationsConfigUpdate
  upd: (field: keyof IntegrationsConfigUpdate, value: unknown) => void
  fieldErrors: Record<string, string>
  isAdmin: boolean
  /** The page's `Enabled` toggle for `prometheus_enabled`. */
  toggle: ReactNode
  /** The page's `Base URL` field and `Bearer token` secret field. */
  fields: ReactNode
}

export default function PrometheusCard({ config, form, upd, fieldErrors, isAdmin, toggle, fields }: PrometheusCardProps) {
  const [probe, setProbe] = useState<string | null>(null)
  const [probing, setProbing] = useState(false)
  const templates: PrometheusMetricTemplate[] = form[PROMETHEUS_TEMPLATES_FIELD] ?? []
  const note = templatesNote(config, form)
  const templateError = fieldErrors[PROMETHEUS_TEMPLATES_FIELD]

  const setTemplates = (next: PrometheusMetricTemplate[]) => upd(PROMETHEUS_TEMPLATES_FIELD, next)
  const editRow = (index: number, patch: Partial<PrometheusMetricTemplate>) =>
    setTemplates(templates.map((t, i) => (i === index ? { ...t, ...patch } : t)))

  async function testConnection() {
    setProbing(true)
    setProbe(null)
    try {
      const results = await triggerProbe('prometheus')
      const result = results.find(r => r.provider === 'prometheus') ?? results[0]
      setProbe(result ? probeLine(result) : TEST_FAILED)
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      const sentence = detail === undefined ? '' : extractErrorMessage(detail, '')
      setProbe(sentence ? `${TEST_FAILED} ${sentence}` : TEST_FAILED)
    } finally {
      setProbing(false)
    }
  }

  return (
    <div className="card flex flex-col gap-3 xl:col-span-2" data-prometheus-card="">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h3 className="m-0 text-sm font-semibold text-[var(--color-text)]">{PROMETHEUS_TITLE}</h3>
          {!config.prometheus_base_url && <span className={NEUTRAL_BADGE} data-prometheus-not-configured="">{PROMETHEUS_NOT_CONFIGURED}</span>}
        </div>
        {toggle}
      </div>
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{PROMETHEUS_INTRO}</p>
      <div className="grid grid-cols-2 gap-3">{fields}</div>

      <div className="flex flex-col gap-2" data-prometheus-templates="">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h4 className="m-0 text-xs font-semibold text-[var(--color-text-secondary)]">{TEMPLATES_HEADING}</h4>
          {note && <span className="text-xs text-[var(--color-text-muted)]" data-templates-note="">{note}</span>}
        </div>
        <p className="m-0 text-xs text-[var(--color-text-muted)]">{TEMPLATES_HELP}</p>
        {templates.map((t, index) => (
          <TemplateRow key={index} index={index} template={t} disabled={!isAdmin}
            onChange={patch => editRow(index, patch)}
            onRemove={() => setTemplates(templates.filter((_, i) => i !== index))} />
        ))}
        {templateError && (
          <p role="alert" data-field-error={PROMETHEUS_TEMPLATES_FIELD} className="m-0 text-xs text-[var(--ui-danger-ink)]">{templateError}</p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn-secondary btn-sm" disabled={!isAdmin || templates.length >= TEMPLATES_MAX}
            onClick={() => setTemplates([...templates, blankTemplate()])}>
            {ADD_TEMPLATE}
          </button>
          <button type="button" className="text-xs text-[var(--color-accent)] hover:underline disabled:opacity-50" disabled={!isAdmin}
            onClick={() => setTemplates([])}>
            {USE_DEFAULTS}
          </button>
        </div>
      </div>

      <div className="flex flex-col items-start gap-1" data-prometheus-probe="">
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn-secondary btn-sm" disabled={probing} onClick={() => { void testConnection() }}>
            {probing ? TESTING : TEST_CONNECTION}
          </button>
          {probe && <p role="status" className="m-0 text-xs text-[var(--color-text)]" data-probe-result="">{probe}</p>}
        </div>
        <p className="m-0 text-xs text-[var(--color-text-muted)]">{TEST_SAVED_NOTE}</p>
      </div>
    </div>
  )
}

/** One template: id · label · unit · Remove on one line, its PromQL under them. */
function TemplateRow({ index, template, disabled, onChange, onRemove }: {
  index: number
  template: PrometheusMetricTemplate
  disabled: boolean
  onChange: (patch: Partial<PrometheusMetricTemplate>) => void
  onRemove: () => void
}) {
  const n = index + 1
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-[var(--color-border)] p-2" data-prometheus-template={index}>
      {/* 12rem of 12 px mono holds a default id (`memory_working_set`) whole on CI's DejaVu Sans Mono; 9rem holds
          "per second" and the select's chevron (7rem cut `per_second` to "per_secon"). */}
      <div className="grid grid-cols-[12rem_minmax(0,1fr)_9rem_auto] items-center gap-2">
        <input aria-label={`Template ${n} id`} value={template.id} disabled={disabled} placeholder="http_p95_latency"
          onChange={e => onChange({ id: e.target.value })} className={`${CELL} font-mono text-xs`} />
        <input aria-label={`Template ${n} label`} value={template.label} disabled={disabled} placeholder="p95 latency"
          onChange={e => onChange({ label: e.target.value })} className={CELL} />
        <select aria-label={`Template ${n} unit`} value={template.unit ?? 'count'} disabled={disabled}
          onChange={e => onChange({ unit: e.target.value })} className={CELL}>
          {TEMPLATE_UNITS.map(unit => <option key={unit} value={unit}>{TEMPLATE_UNIT_LABELS[unit] ?? unit}</option>)}
        </select>
        <button type="button" className="btn-secondary btn-sm" disabled={disabled} aria-label={`Remove template ${n}`} onClick={onRemove}>
          {REMOVE_TEMPLATE}
        </button>
      </div>
      <textarea aria-label={`Template ${n} PromQL`} rows={2} value={template.promql} disabled={disabled}
        onChange={e => onChange({ promql: e.target.value })} className={`${CELL} font-mono text-xs`} />
    </div>
  )
}
