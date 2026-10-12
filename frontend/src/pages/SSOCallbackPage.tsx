/**
 * Where the browser lands after a SAML sign-in.
 *
 * The identity provider form-POSTs its response to the API's ACS, which
 * validates it and answers with a 303 to `/sso/callback#code=…&next=…`. The code
 * is one-time and short-lived; this page exchanges it for the session, then
 * goes where the sign-in started. The tokens never appear in a URL, and the
 * fragment (which carries the code) never reaches a server log.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'

import AppLogo from '@/components/ui/AppLogo'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { fetchCurrentUser } from '@/services/authService'
import { exchangeSSOCode } from '@/services/ssoService'
import { useAuthStore } from '@/store/authStore'
import { returnPathFromString } from '@/utils/returnPath'

export default function SSOCallbackPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [failed, setFailed] = useState(false)
  // The code works once: never spend it twice (a re-render, or StrictMode's double effect).
  const started = useRef(false)
  // Read once: the address bar is cleared of the code right after.
  const [{ code, next }] = useState(() => {
    const params = new URLSearchParams(location.hash.replace(/^#/, ''))
    return { code: params.get('code'), next: returnPathFromString(params.get('next')) }
  })

  useEffect(() => {
    if (started.current || !code) return
    started.current = true
    // Keep the code out of the address bar and history.
    if (window.location.hash.includes('code=')) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
    }

    exchangeSSOCode(code)
      .then(async (tokens) => {
        const user = await fetchCurrentUser(tokens.access_token)
        setAuth(tokens.access_token, tokens.refresh_token, user)
        toast.success('Signed in with SSO')
        // ProtectedRoute sends a user who must change their password to /reset-password.
        navigate(next, { replace: true })
      })
      .catch(() => setFailed(true))
  }, [code, next, navigate, setAuth])

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-bg)] px-4">
      <div className="w-full max-w-sm card p-6 text-center space-y-4">
        <div className="flex justify-center"><AppLogo /></div>
        {failed || !code ? (
          <>
            <p role="alert" className="text-sm text-[var(--color-text)]">
              This single sign-on link has expired or was already used.
            </p>
            <Link to="/login" className="btn-primary inline-flex justify-center w-full">
              Back to sign in
            </Link>
          </>
        ) : (
          <div className="flex items-center justify-center gap-2 text-sm text-[var(--color-text-muted)]" role="status">
            <LoadingSpinner size="sm" /> Signing you in…
          </div>
        )}
      </div>
    </div>
  )
}
