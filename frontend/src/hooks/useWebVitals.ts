/**
 * TestLookup — Core Web Vitals hook.
 *
 * Registers the Core Web Vitals measurements (CLS, LCP, FCP, TTFB, INP) from the
 * web-vitals library and forwards each report to reportWebVital() for batched
 * delivery to the backend observability endpoint.
 *
 * Note: FID (First Input Delay) was retired in web-vitals v5 in favour of INP
 * (Interaction to Next Paint), so `onFID` is no longer registered.
 *
 * Usage:
 *   Call useWebVitals() once at the root of your app (e.g. in App.tsx).
 */
import { useEffect } from 'react'
import { reportWebVital } from '../utils/errorReporting'

export function useWebVitals(): void {
  useEffect(() => {
    // Dynamic import keeps web-vitals out of the initial bundle chunk
    import('web-vitals').then(({ onCLS, onLCP, onFCP, onTTFB, onINP }) => {
      onCLS(reportWebVital)
      onLCP(reportWebVital)
      onFCP(reportWebVital)
      onTTFB(reportWebVital)
      // INP (Interaction to Next Paint) replaced FID as a Core Web Vital in v5
      onINP(reportWebVital)
    }).catch(() => {
      /* web-vitals not available in older browsers — fail silently */
    })
  }, []) // runs once on mount
}
