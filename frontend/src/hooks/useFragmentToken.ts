import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'

/**
 * Read `#token=…` once and drop it from the address bar and history. The
 * fragment never reaches a server, a proxy log or a Referer header.
 */
export function useFragmentToken(): string | null {
  const location = useLocation()
  const [token] = useState(() => new URLSearchParams(location.hash.replace(/^#/, '')).get('token'))
  useEffect(() => {
    if (window.location.hash.includes('token=')) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
    }
  }, [])
  return token
}
