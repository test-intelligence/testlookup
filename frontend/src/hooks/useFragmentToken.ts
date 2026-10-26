import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

/**
 * Read `#token=…` once and drop it from the address bar and history. The
 * fragment never reaches a server, a proxy log or a Referer header.
 *
 * The fragment is removed through the router (a replace navigation), not
 * `history.replaceState`: the router keeps its own copy of the location, and a
 * page that remounted after the token was spent (signing in re-renders the
 * app) read the stale hash and asked the server about a used token.
 */
export function useFragmentToken(): string | null {
  const location = useLocation()
  const navigate = useNavigate()
  const [token] = useState(() => new URLSearchParams(location.hash.replace(/^#/, '')).get('token'))
  useEffect(() => {
    if (location.hash.includes('token=')) {
      navigate({ pathname: location.pathname, search: location.search }, { replace: true })
    }
  }, [location.hash, location.pathname, location.search, navigate])
  return token
}
