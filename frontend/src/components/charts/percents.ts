/**
 * The rounding rule for every part-to-whole chart (the donut's slice percents,
 * a 100% stacked bar's segments): largest remainder, so the drawn percents sum
 * to exactly 100.0. See `chartCatalog.ts` (2) for why.
 *
 * A leaf module, apart from the chart registry on purpose: the bar model needs
 * only this, and importing it from `chartCatalog` brought the registry along
 * into whatever chunk the bar model landed in (Wave 2.6 R1-3). `chartCatalog`
 * re-exports it for its callers.
 */

/**
 * `values` as percentages of their total, rounded to `decimals` places by the
 * largest-remainder method, so the result sums to exactly 100 whenever
 * anything was measured at all. A total of zero (or nothing at all) gives
 * zeros — never NaN, and never a share of nothing.
 *
 * Ties are paid from the BACK: three equal thirds read 33.3, 33.3, 33.4.
 */
export function largestRemainderPercents(values: readonly number[], decimals = 1): number[] {
  const scale = 10 ** decimals
  const units = 100 * scale
  // A NEGATIVE value has no share of a whole. Passed through it makes the
  // total smaller than the positives that make it up, so the shares run past
  // 100% and the negative one draws a bar pointing out of the stack. It is 0%
  // here, exactly as the donut drops a negative bucket rather than drawing it.
  const shares = values.map((value) => (Number.isFinite(value) && value > 0 ? value : 0))
  const total = shares.reduce((sum, value) => sum + value, 0)
  if (!(total > 0)) return values.map(() => 0)

  const exact = shares.map((value) => (value / total) * units)
  const floors = exact.map((value) => Math.floor(value))
  let owed = units - floors.reduce((sum, value) => sum + value, 0)

  // Biggest remainder first; on a tie the LATER index is paid first.
  const order = exact
    .map((value, index) => ({ index, remainder: value - floors[index] }))
    .sort((a, b) => b.remainder - a.remainder || b.index - a.index)

  for (const { index } of order) {
    if (owed <= 0) break
    floors[index] += 1
    owed -= 1
  }
  return floors.map((value) => value / scale)
}
