/**
 * ExportChoiceSection — the export step inside the criteria preview (W22b
 * ACT-03).
 *
 * `Delete without an archive` is the default (D-7); `Download an archive
 * first` (collect) and `Keep an archive in storage` (retain) build a verified
 * archive before anything is deleted. The three tiers show the server's
 * estimates (`not measured`, never 0); a tier over the size limit is disabled
 * with its title; `full` adds its warning; the not-a-backup sentence is ALWAYS
 * shown. When the set cannot be archived (over 2000 runs, nothing to archive)
 * the two archive targets are disabled and the server's refusal is shown.
 *
 * Controlled: the panel owns the choice and sends it with execute. Rendered
 * only when the preview carries `export_estimate` (an older server offers no
 * export). React text only; in-page only.
 */
import {
  ARCHIVE_NOT_BACKUP,
  ARCHIVE_CONTENTS_LABEL,
  ARCHIVE_STEP_LABEL,
  EXPORT_COLLECT_NOTE,
  EXPORT_FULL_WARNING,
  EXPORT_RETAIN_NOTE,
  overCapTitle,
  reclaimLine,
  TARGET_OPTIONS,
  TIER_LABELS,
  tierLine,
  type ExportEstimateLike,
  type TargetChoice,
  type TierEstimateLike,
  type TierKey,
} from './exportModel'

export interface ExportChoiceSectionProps {
  estimate: ExportEstimateLike
  target: TargetChoice
  tier: TierKey
  onChange: (next: { target: TargetChoice; tier: TierKey }) => void
  disabled?: boolean
  /** The execute refusal's sentence (over the limit, too many runs …), shown inline. */
  error?: string | null
}

const TIER_ORDER = Object.keys(TIER_LABELS) as TierKey[]

/** The three tiers in order, whatever the server sent (a missing one reads `not measured`). */
function tiersOf(estimate: ExportEstimateLike): TierEstimateLike[] {
  const sent = Array.isArray(estimate.tiers) ? estimate.tiers : []
  return TIER_ORDER.map(
    (tier) => sent.find((t) => t.tier === tier) ?? { tier, archive_bytes: null, measured: false },
  )
}

export default function ExportChoiceSection({
  estimate,
  target,
  tier,
  onChange,
  disabled = false,
  error = null,
}: ExportChoiceSectionProps) {
  const archive = target !== 'none'
  const exportable = estimate.exportable
  const tiers = tiersOf(estimate)

  return (
    <fieldset data-criteria-export="" className="space-y-2 border-t border-[var(--color-border)] pt-3">
      <legend className="font-medium text-[var(--color-text)]">{ARCHIVE_STEP_LABEL}</legend>
      {/* Stacked, not a row: the three labels side by side leave < 40 px of the
          676 px preview box at 1280 on CI's DejaVu. */}
      <div data-criteria-export-targets="" className="flex flex-col gap-1">
        {TARGET_OPTIONS.map((option) => (
          <label key={option.value} className="inline-flex items-center gap-1.5 text-[var(--color-text)]">
            <input
              type="radio"
              name="criteria-export-target"
              value={option.value}
              checked={target === option.value}
              disabled={disabled || (option.value !== 'none' && !exportable)}
              onChange={() => onChange({ target: option.value, tier })}
            />
            {option.label}
          </label>
        ))}
      </div>
      {!exportable && estimate.refusal && (
        <p data-criteria-export-refusal="" className="text-[var(--color-text-muted)]">
          {estimate.refusal}
        </p>
      )}
      <p
        id="criteria-export-contents"
        className={archive ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'}
      >
        {ARCHIVE_CONTENTS_LABEL}
      </p>
      <div
        data-criteria-export-tiers=""
        role="radiogroup"
        aria-labelledby="criteria-export-contents"
        className="flex flex-col gap-1 border-l border-[var(--color-border)] pl-3"
      >
        {tiers.map((t) => {
          const overCap = Boolean(t.over_cap)
          return (
            <label
              key={t.tier}
              title={overCap ? overCapTitle(estimate.max_bytes) : undefined}
              className={`inline-flex items-center gap-1.5 ${archive ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'}`}
            >
              <input
                type="radio"
                name="criteria-export-tier"
                value={t.tier}
                checked={archive && tier === t.tier}
                disabled={disabled || !archive || overCap}
                onChange={() => onChange({ target, tier: t.tier as TierKey })}
              />
              {tierLine(t)}
            </label>
          )
        })}
      </div>
      <p data-criteria-export-reclaim="" className="text-[var(--color-text)]">
        {reclaimLine(estimate)}
      </p>
      {archive && (
        <p className="text-[var(--color-text-muted)]">
          {target === 'collect' ? EXPORT_COLLECT_NOTE : EXPORT_RETAIN_NOTE}
        </p>
      )}
      {archive && tier === 'full' && (
        <p data-criteria-export-full-warning="" className="text-[var(--ui-warning-ink)]">
          {EXPORT_FULL_WARNING}
        </p>
      )}
      <p data-criteria-export-note="" className="text-[var(--color-text-muted)]">
        {ARCHIVE_NOT_BACKUP}
      </p>
      {error && (
        <p role="alert" data-criteria-export-error="" className="text-[var(--ui-danger-ink)]">
          {error}
        </p>
      )}
    </fieldset>
  )
}
