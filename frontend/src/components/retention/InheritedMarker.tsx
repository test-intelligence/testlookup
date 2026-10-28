/**
 * InheritedMarker — the help line under an inheritable policy field (W22b
 * ACT-06): where the field's value comes from, and the one action that flips
 * it.
 *
 * - Saved from the instance / the code default, still inherited:
 *   `180 days (instance default) · Override` — Override enables the input and
 *   the field is then sent on Save as the project's own value.
 * - Saved as the project's value: `Project value · Reset to inherited` —
 *   Reset puts the inherited value in the input, disables it, and the field
 *   goes in the PUT's `inherit` list on Save.
 * - The two in-between states (overridden / reset, not yet saved) say so and
 *   offer the inverse action.
 *
 * Renders NOTHING when the saved policy carries no source for the field (an
 * older server or fixture): absence is not a value. React text only.
 */
import {
  fieldSource,
  markerLine,
  OVERRIDDEN_LINE,
  OVERRIDE_LABEL,
  PROJECT_VALUE_LABEL,
  RESET_LINE,
  RESET_TO_INHERITED_LABEL,
  type FieldMode,
  type InheritableField,
  type MarkerSource,
  type MarkerUnit,
} from './scheduleModel'

export interface InheritedMarkerProps {
  field: InheritableField
  /** The SAVED policy (its sources and inherit values). */
  policy: MarkerSource
  /** The form's current mode for the field; defaults to the saved one. */
  mode?: FieldMode
  unit?: MarkerUnit
  onOverride: () => void
  onReset: () => void
  disabled?: boolean
}

export default function InheritedMarker({
  field,
  policy,
  mode,
  unit,
  onOverride,
  onReset,
  disabled = false,
}: InheritedMarkerProps) {
  const source = fieldSource(field, policy)
  if (source === null) return null
  const savedMode: FieldMode = source === 'project' ? 'own' : 'inherit'
  const current = mode ?? savedMode
  let text: string | null
  if (current === 'inherit') {
    text = savedMode === 'inherit' ? markerLine(field, policy, unit) : RESET_LINE
  } else {
    text = savedMode === 'own' ? PROJECT_VALUE_LABEL : OVERRIDDEN_LINE
  }
  const action = current === 'inherit' ? OVERRIDE_LABEL : RESET_TO_INHERITED_LABEL
  return (
    <span
      data-retention-inherited={field}
      data-source={source}
      data-mode={current}
      className="mt-1 flex flex-wrap items-center gap-x-1 text-xs text-[var(--color-text-faint)]"
    >
      {text && <span>{text}</span>}
      {text && <span aria-hidden="true">·</span>}
      <button
        type="button"
        onClick={current === 'inherit' ? onOverride : onReset}
        disabled={disabled}
        className="text-xs text-[var(--color-accent)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
      >
        {action}
      </button>
    </span>
  )
}
