import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'

import { extractErrorMessage } from '@/services/apiErrors'
import { githubAppService } from '@/services/githubIntegrationService'

/**
 * W19 INT-06: GitHub sends the browser back to Settings › GitHub with `installation_id` and `state` after an App
 * installation. Post them to the setup callback once (the server verifies the signed, user-bound state), drop them
 * from the address bar, then call `onDone`.
 */
/** Post GitHub's `installation_id` + `state` once, then clear them from the URL. Calls `onDone` with the result. */
export function useGitHubAppCallback(enabled: boolean, onDone: () => void): void {
  const location = useLocation()
  const navigate = useNavigate()
  const sent = useRef(false)
  useEffect(() => {
    if (!enabled || sent.current) return
    const q = new URLSearchParams(location.search)
    const installationId = Number(q.get('installation_id'))
    const state = q.get('state')
    if (!installationId || !state) return
    sent.current = true
    navigate({ pathname: location.pathname }, { replace: true })
    githubAppService
      .setupCallback(installationId, state)
      .then((r) => {
        toast.success(`GitHub App installation ${r.installation_id} connected`)
        onDone()
      })
      .catch((err) => toast.error(extractErrorMessage((err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail, 'The GitHub App installation could not be recorded')))
  }, [enabled, location.pathname, location.search, navigate, onDone])
}

