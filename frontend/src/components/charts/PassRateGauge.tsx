import { RingGaugeView } from './RingGauge'

interface Props {
  value: number
  size?: number
  /** Forwarded to Recharts' `isAnimationActive`; `undefined` keeps Recharts' default. */
  animate?: boolean
}

/**
 * The pass-rate ring: `RingGauge` with its defaults (95 / 80 bands, one
 * decimal and "%", "Pass Rate" under it). It keeps Recharts' accessibility
 * layer, as it always had — `rechartsA11y.test.tsx` and the gallery's
 * LEGACY_APPLICATION_LAYER ratchet count it — so its bytes are unchanged
 * (`RingGauge.defaultRender.test.tsx`). New callers use `RingGauge`.
 */
export default function PassRateGauge({ value, size, animate }: Props) {
  return <RingGaugeView value={value} size={size} animate={animate} legacyApplicationLayer />
}
