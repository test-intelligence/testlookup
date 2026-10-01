import { lazy as reactLazy, type ComponentType, type LazyExoticComponent } from 'react'

/**
 * Wrap React.lazy so a stale-chunk failure auto-reloads the page.
 *
 * After a deploy, the user's open tab still has the OLD index.html in
 * memory which references chunk filenames like ``DefectsPage-urxoen35.js``.
 * Vite generates fresh content-hashed names on every build, so when the
 * user navigates to a not-yet-loaded route, the chunk 404s with
 * "Failed to fetch dynamically imported module". Without this wrapper
 * the user sees an "ErrorBoundary" page on every cross-deploy navigation.
 *
 * Strategy: on the first import failure of the session, reload
 * ``window.location`` so the browser fetches a fresh index.html with
 * the current chunk hashes. The sessionStorage flag guards against an
 * infinite reload loop in case the failure isn't deploy-related (e.g.
 * permanent network issue).
 *
 * Lives here rather than in App.tsx so the report pages' lazy SECTIONS
 * (VIZ-408) get the same one-shot reload as the routes: a section chunk is
 * just as hashed, and a tab opened before a deploy 404s on it the same way.
 * The second failure still throws, into the section's own error boundary.
 */
export const CHUNK_RELOAD_FLAG = '__testlookup_chunk_reload_attempted'

export function lazyWithRetry<P extends object>(
  importFn: () => Promise<{ default: ComponentType<P> }>,
): LazyExoticComponent<ComponentType<P>> {
  return reactLazy(async () => {
    try {
      const mod = await importFn()
      // Successful load — clear the flag so a future stale-chunk
      // error gets its own one-shot reload chance.
      try { sessionStorage.removeItem(CHUNK_RELOAD_FLAG) } catch { /* noop */ }
      return mod
    } catch (err) {
      const alreadyTried = (() => {
        try { return sessionStorage.getItem(CHUNK_RELOAD_FLAG) === '1' } catch { return false }
      })()
      if (!alreadyTried) {
        try { sessionStorage.setItem(CHUNK_RELOAD_FLAG, '1') } catch { /* noop */ }
        // ``location.reload()`` fetches a fresh index.html — which is
        // served with ``Cache-Control: no-cache`` so the browser gets
        // the new chunk hashes immediately.
        window.location.reload()
        // Halt the promise chain — the reload will replace the page
        // before this never-resolving promise settles.
        return await new Promise<never>(() => undefined)
      }
      throw err
    }
  })
}
