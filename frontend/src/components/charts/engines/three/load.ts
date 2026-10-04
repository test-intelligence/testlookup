/**
 * Loading the 3D scatter engine (VIZ-508): three.js and the scatter drawn
 * with it, one lazy chunk, fetched the first time a reader asks for the 3D
 * view and never before.
 *
 * Its own loader, not a `registry.ts` entry: the registry hands back an
 * ECharts `init`, and this engine is not one. The rules are the registry's:
 * loaded once, a failed load NOT cached (the next attempt asks the network
 * again), and a missing chunk (a tab opened before a deploy) rejects with a
 * `StaleBuildError`, which the view shows as "A new version is available".
 */
import type { Scatter3DEngine } from '../../scatter3d.model'
import { lazyChartEngine } from '../lazyChartEngine'

let loaded: Promise<Scatter3DEngine> | null = null

export function loadScatter3D(): Promise<Scatter3DEngine> {
  if (loaded) return loaded
  const pending = lazyChartEngine((): Promise<Scatter3DEngine> => import('./scatter3d'))
  loaded = pending
  pending.catch(() => {
    if (loaded === pending) loaded = null
  })
  return pending
}

/** Test seam. */
export function resetScatter3DCache(): void {
  loaded = null
}
