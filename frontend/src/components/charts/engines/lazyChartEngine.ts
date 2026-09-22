/**
 * Loading a chart engine chunk, and what a failed load means (VIZ-103).
 *
 * Engine code is never eager: every chart type is a dynamic `import()`. After
 * a deploy, a tab opened on the previous build still asks for the previous
 * build's hashed chunk names, and nginx answers 404. The browser reports that
 * as a rejected dynamic import. Left alone it becomes a blank chart and an
 * unhandled rejection; here it becomes a typed `StaleBuildError`, which the
 * chart frame renders as "A new version is available" with a Reload action.
 *
 * A module that loads but throws while evaluating is a BUG, not a stale build,
 * and is re-thrown unchanged so it surfaces as one.
 */

export const STALE_BUILD_MESSAGE = 'A new version is available'
export const STALE_BUILD_ACTION = 'Reload'

export class StaleBuildError extends Error {
  readonly kind = 'stale-build' as const

  constructor(cause: unknown) {
    super(STALE_BUILD_MESSAGE)
    this.name = 'StaleBuildError'
    ;(this as { cause?: unknown }).cause = cause
  }
}

export function isStaleBuildError(error: unknown): error is StaleBuildError {
  return error instanceof StaleBuildError
}

/**
 * The messages each browser (and Vite's preload helper) uses for a chunk that
 * could not be FETCHED, as opposed to one that failed while running.
 */
const CHUNK_LOAD_FAILURE = [
  /Failed to fetch dynamically imported module/i, // Chromium
  /error loading dynamically imported module/i, // Firefox
  /Importing a module script failed/i, // Safari
  /Unable to preload CSS/i, // Vite preload helper
  /Loading (CSS )?chunk .* failed/i, // webpack-style wording, kept for mocks and proxies
]

export function isChunkLoadFailure(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  if (error.name === 'ChunkLoadError') return true
  return CHUNK_LOAD_FAILURE.some((pattern) => pattern.test(error.message))
}

/** Runs `loader`, converting a chunk-fetch failure into a `StaleBuildError`. */
export async function lazyChartEngine<T>(loader: () => Promise<T>): Promise<T> {
  try {
    return await loader()
  } catch (error) {
    if (isChunkLoadFailure(error)) throw new StaleBuildError(error)
    throw error
  }
}
