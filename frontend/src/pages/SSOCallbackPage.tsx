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

/**
 * The last code this tab spent. The ref below stops a second effect in the SAME
 * instance; this stops a NEW instance (the route remounts when the session
 * appears, and the router still holds the old hash) from spending it again — a
 * duplicate POST /sso/exchange answered 400 (found in the W20 ADM-08b walk).
 * The code is already spent and dead within a minute, so keeping it is harmless.
 */
const SPENT_CODE_KEY = 'sso:spent-code'

function alreadySpent(code: string): boolean {
  try {
    if (sessionStorage.getItem(SPENT_CODE_KEY) === code) return true
    sessionStorage.setItem(SPENT_CODE_KEY, code)
  } catch {
    // Storage unavailable: the per-instance ref still guards.
  }
  return false
}

export default function SSOCallbackPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [signInError, setSignInError] = useState(false)
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
    if (alreadySpent(code)) return
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
      .catch(() => setSignInError(true))
  }, [code, next, navigate, setAuth])

  // No code in the URL is the same dead end as a refused exchange.
  const showFailure = signInError || !code

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-bg)] px-4">
      <div className="w-full max-w-sm card p-6 text-center space-y-4">
        <div className="flex justify-center"><AppLogo /></div>
        {showFailure ? (
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
