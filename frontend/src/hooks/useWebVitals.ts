/**
 * TestLookup — Core Web Vitals hook.
 *
 * Registers all six Core Web Vitals measurements (CLS, FID, LCP, FCP, TTFB, INP)
 * from the web-vitals library and forwards each report to reportWebVital() for
 * batched delivery to the backend observability endpoint.
 *
 * Usage:
 *   Call useWebVitals() once at the root of your app (e.g. in App.tsx).
 */
import { useEffect } from 'react'
import { reportWebVital } from '../utils/errorReporting'

export function useWebVitals(): void {
  useEffect(() => {
    // Dynamic import keeps web-vitals out of the initial bundle chunk
    import('web-vitals').then(({ onCLS, onFID, onLCP, onFCP, onTTFB, onINP }) => {
      onCLS(reportWebVital)
      onFID(reportWebVital)
      onLCP(reportWebVital)
      onFCP(reportWebVital)
      onTTFB(reportWebVital)
      // INP (Interaction to Next Paint) — available in web-vitals v3+
      if (onINP) onINP(reportWebVital)
    }).catch(() => {
      /* web-vitals not available in older browsers — fail silently */
    })
  }, []) // runs once on mount
}
