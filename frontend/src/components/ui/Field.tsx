import { useId, type ReactNode } from 'react'

interface FieldProps {
  /** Visible label text. Becomes the control's accessible name. */
  label: ReactNode
  /**
   * Classes for the `<label>`. The settings pages use eight different label
   * styles, so this component deliberately imposes none — pass whatever the
   * surrounding form already used and the rendering is unchanged.
   */
  labelClassName?: string
  /** Optional hint rendered after the control (e.g. "optional", units). */
  hint?: ReactNode
  /** Receives the generated id; put it on the control. */
  children: (id: string) => ReactNode
}

/**
 * A label wired to its control.
 *
 * Every settings form wrote the pair by hand:
 *
 *     <label className="...">SMTP Host</label>
 *     <input type="text" value={host} … />
 *
 * — visible label, but no `htmlFor`, no `id`, no `aria-label`. So the control
 * had **no accessible name**: `getByRole('textbox', { name: 'SMTP Host' })`
 * matched nothing, and a screen reader announced an unnamed edit field. 46 of
 * 61 controls across the settings pages were in that state, including password
 * and API-token inputs.
 *
 * Renders a fragment rather than a wrapper element on purpose: these pairs
 * already sit inside grid/flex containers whose classes carry the layout
 * (`sm:col-span-2` and friends), so introducing a div would change rendering.
 * Label and control stay exact siblings; the only difference is the wiring.
 *
 * `useId` keeps ids unique even when the same form is rendered twice.
 */
export default function Field({ label, labelClassName, hint, children }: FieldProps) {
  const id = useId()
  return (
    <>
      <label htmlFor={id} className={labelClassName}>
        {label}
        {hint}
      </label>
      {children(id)}
    </>
  )
}
