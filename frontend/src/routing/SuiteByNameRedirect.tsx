/**
 * `/coverage/suite?name=X` → the suite's own page, Charts tab (UX redesign P4).
 * The old page was keyed by name; the suite page by id, so the name is looked
 * up in the project's suites. An unknown (or missing) name goes to the suites
 * list rather than a blank page; any other query parameter is kept.
 *
 * Its own module, loaded lazily by App.tsx: the suite lookup (useSuites →
 * suitesService) would otherwise ride in the eager bundle with the other,
 * dependency-free redirects (+218 B gzip over the eager budget).
 */
import { Navigate, useLocation } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useSuites } from '@/hooks/useSuites'

export default function SuiteByNameRedirect() {
  const { search } = useLocation()
  const name = new URLSearchParams(search).get('name')
  const { data, error, isLoading } = useSuites()
  if (name && isLoading && !data) return <LoadingSpinner size="lg" />
  const suite = name && !error ? data?.items.find((s) => s.name === name) : undefined
  const rest = new URLSearchParams(search)
  rest.delete('name')
  if (!suite) return <Navigate to={`/suites${rest.toString() ? `?${rest}` : ''}`} replace />
  rest.set('tab', 'charts')
  return <Navigate to={`/suites/${suite.id}?${rest}`} replace />
}
