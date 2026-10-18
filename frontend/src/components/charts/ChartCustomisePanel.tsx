/**
 * VIZ-604 — "Customise": not a modal, so the chart it changes stays in view
 * and redraws as each setting changes. "Reset to default" restores the chart
 * as shipped.
 *
 * W27b PAN-05b: a section IN THE PAGE (an `InlineFormSection as="aside"`,
 * named `Customise <chart>`), which its host renders under Explore's header or
 * right after the Trends chart's frame; its fields sit in a wrapping row so the
 * chart above stays in view. Nothing is sent by the form itself.
 *
 * TRND-10: the panel is generic. A chart hands it a `CustomiseSpec` — its
 * fields (text or select, each with its `data-customise` id), the options it
 * does not offer and why (`unavailable`: a pie for series over time, stacked
 * rates, a log scale for rates or zeros, more lines than stay readable), and
 * whether it is at its default — and applies `onField(id, value)` itself. A
 * disabled option is listed "(unavailable)" in its select, and every reason is
 * listed below the fields, so a misleading chart is never one click away and
 * never silently missing. The DOM hooks (`data-customise="title"`, `metric`,
 * `series`, `bucket`, `topN`, `chartType`, `scale`, `reset`) are what they
 * were for the suite series.
 */
import { useId } from 'react'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { CustomiseField, CustomiseSpec } from './chartCustomiseModel'

export interface ChartCustomisePanelProps {
  /** The chart's name, for the panel's heading. */
  chartTitle: string
  spec: CustomiseSpec
  onField: (id: string, value: string) => void
  onReset: () => void
  onClose: () => void
}

const SELECT =
  'min-h-8 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-2 text-sm text-[var(--color-text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]'
const LABEL = 'text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]'

function Field({ field, inputId, onField }: { field: CustomiseField; inputId: string; onField: (id: string, value: string) => void }) {
  const options = Array.isArray(field.options) ? field.options : []
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={inputId} className={LABEL}>
        {field.label}
      </label>
      {field.kind === 'text' ? (
        <input
          id={inputId}
          data-customise={field.id}
          value={field.value}
          maxLength={field.maxLength}
          placeholder={field.placeholder}
          onChange={(event) => onField(field.id, event.target.value)}
          className={SELECT}
        />
      ) : (
        <select
          id={inputId}
          data-customise={field.id}
          value={field.value}
          onChange={(event) => onField(field.id, event.target.value)}
          className={SELECT}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled} title={option.reason}>
              {option.disabled ? `${option.label} (unavailable)` : option.label}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

export default function ChartCustomisePanel({ chartTitle, spec, onField, onReset, onClose }: ChartCustomisePanelProps) {
  const base = useId()
  const fields = Array.isArray(spec.fields) ? spec.fields : []
  const unavailable = Array.isArray(spec.unavailable) ? spec.unavailable : []

  return (
    <InlineFormSection
      as="aside"
      title="Customise"
      ariaLabel={`Customise ${chartTitle}`}
      description={<p className="m-0 text-xs text-[var(--color-text-secondary)] break-words">{chartTitle}</p>}
      onClose={onClose}
      closeLabel="Close customise panel"
      dataAttributes={{ 'data-chart-customise-panel': '' }}
      className="mt-3 min-w-0"
    >
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-[repeat(auto-fill,minmax(180px,1fr))]">
          {fields.map((field) => (
            <Field key={field.id} field={field} inputId={`${base}-${field.id}`} onField={onField} />
          ))}
        </div>

        <ul data-customise-reasons="" className="flex flex-col gap-1 text-xs text-[var(--color-text-secondary)]">
          {unavailable.map((option) => (
            <li key={`${option.control}-${option.label}`}>
              <span className="font-semibold">{option.label}</span>: {option.reason}
            </li>
          ))}
        </ul>

        <button
          type="button"
          data-customise="reset"
          disabled={spec.isDefault}
          onClick={onReset}
          className="self-start rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm font-medium text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
        >
          Reset to default
        </button>
      </div>
    </InlineFormSection>
  )
}
