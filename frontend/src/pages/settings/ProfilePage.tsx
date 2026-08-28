import { useState } from 'react'
import { Check, Eye, EyeOff, KeyRound, Loader2, UserCircle2 } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import MfaSecuritySection from '@/components/mfa/MfaSecuritySection'
import { useAuthStore } from '@/store/authStore'
import { api } from '@/services/api'

// ── Avatar colour palette ─────────────────────────────────────────────────────
const AVATAR_COLORS: { key: string; bg: string; ring: string }[] = [
  { key: 'slate',   bg: 'bg-[var(--color-bg-hover)]',   ring: 'ring-[var(--color-border-light)]' },
  { key: 'red',     bg: 'bg-[var(--status-failed-bg)]',     ring: 'ring-[var(--status-failed)]' },
  { key: 'orange',  bg: 'bg-[var(--status-broken-bg)]',  ring: 'ring-[var(--status-broken)]' },
  { key: 'amber',   bg: 'bg-[var(--status-broken-bg)]',   ring: 'ring-[var(--status-broken)]' },
  { key: 'lime',    bg: 'bg-[var(--status-passed-bg)]',    ring: 'ring-[var(--status-passed)]' },
  { key: 'emerald', bg: 'bg-[var(--status-passed-bg)]', ring: 'ring-[var(--status-passed)]' },
  { key: 'teal',    bg: 'bg-[var(--status-passed-bg)]',    ring: 'ring-[var(--status-passed)]' },
  { key: 'cyan',    bg: 'bg-[var(--color-accent-muted)]',    ring: 'ring-[var(--color-accent)]' },
  { key: 'blue',    bg: 'bg-[var(--color-accent-muted)]',    ring: 'ring-[var(--color-accent)]' },
  { key: 'violet',  bg: 'bg-[var(--status-flaky-bg)]',  ring: 'ring-[var(--status-flaky)]' },
  { key: 'fuchsia', bg: 'bg-[var(--status-flaky-bg)]', ring: 'ring-[var(--status-flaky)]' },
  { key: 'pink',    bg: 'bg-[var(--status-flaky-bg)]',    ring: 'ring-[var(--status-flaky)]' },
]

const COLOR_MAP = Object.fromEntries(AVATAR_COLORS.map(c => [c.key, c]))

function getInitials(fullName: string | null | undefined, username: string): string {
  if (fullName?.trim()) {
    const parts = fullName.trim().split(/\s+/)
    return parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : parts[0].slice(0, 2).toUpperCase()
  }
  return username.slice(0, 2).toUpperCase()
}

// ── Password strength ─────────────────────────────────────────────────────────
interface StrengthResult {
  score: number          // 0–4
  label: string
  color: string
  checks: { label: string; ok: boolean }[]
}

function getStrength(pw: string): StrengthResult {
  const checks = [
    { label: 'At least 8 characters', ok: pw.length >= 8 },
    { label: 'Uppercase letter',       ok: /[A-Z]/.test(pw) },
    { label: 'Lowercase letter',       ok: /[a-z]/.test(pw) },
    { label: 'Number',                 ok: /\d/.test(pw) },
    { label: 'Special character',      ok: /[^A-Za-z0-9]/.test(pw) },
  ]
  const score = checks.filter(c => c.ok).length
  const labels = ['', 'Weak', 'Fair', 'Good', 'Strong', 'Very strong']
  const colors = ['', 'bg-[var(--status-failed-bg)]', 'bg-[var(--status-broken-bg)]', 'bg-[var(--status-broken-bg)]', 'bg-[var(--status-passed-bg)]', 'bg-[var(--status-passed-bg)]']
  return { score, label: labels[score] ?? '', color: colors[score] ?? '', checks }
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function ProfilePage() {
  const user = useAuthStore(s => s.user)
  const setAuth = useAuthStore(s => s.setAuth)
  const token = useAuthStore(s => s.token)
  const refreshToken = useAuthStore(s => s.refreshToken)

  // ── Profile form state ────────────────────────────────────────────────────
  const [fullName,     setFullName]     = useState(user?.full_name ?? '')
  const [avatarColor,  setAvatarColor]  = useState(user?.avatar_color ?? 'blue')
  const [savingProfile, setSavingProfile] = useState(false)

  // ── Password form state ───────────────────────────────────────────────────
  const [currentPw,  setCurrentPw]  = useState('')
  const [newPw,      setNewPw]      = useState('')
  const [confirmPw,  setConfirmPw]  = useState('')
  const [showCur,    setShowCur]    = useState(false)
  const [showNew,    setShowNew]    = useState(false)
  const [showConf,   setShowConf]   = useState(false)
  const [savingPw,   setSavingPw]   = useState(false)

  const strength = getStrength(newPw)

  // Re-seed the editable form from the canonical user object whenever it
  // changes (e.g. after a save here, a save elsewhere, or re-login). This is
  // React's documented "adjust state during render" pattern: it stores the
  // value last synced from and resets the form fields when the user changes,
  // without an effect (which would render twice and trips
  // react-hooks/set-state-in-effect). Local edits are preserved because the
  // user object is unchanged while typing.
  const [syncedName, setSyncedName] = useState(user?.full_name ?? null)
  const [syncedColor, setSyncedColor] = useState(user?.avatar_color ?? null)
  if (syncedName !== (user?.full_name ?? null) || syncedColor !== (user?.avatar_color ?? null)) {
    setSyncedName(user?.full_name ?? null)
    setSyncedColor(user?.avatar_color ?? null)
    setFullName(user?.full_name ?? '')
    setAvatarColor(user?.avatar_color ?? 'blue')
  }

  // ── Handlers ─────────────────────────────────────────────────────────────
  const handleSaveProfile = async () => {
    setSavingProfile(true)
    try {
      const res = await api.patch('/api/v1/auth/me', {
        full_name:    fullName.trim() || null,
        avatar_color: avatarColor,
      })
      if (!token || !refreshToken) {
        throw new Error('Authentication tokens are missing')
      }
      setAuth(token, refreshToken, res.data)
      toast.success('Profile updated')
    } catch (err: unknown) {
      // api.ts interceptor already toasts for 5xx / network errors.
      // Only add a toast for cases the interceptor stays silent (401, 422).
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 401 || status === 422) {
        toast.error('Could not save profile — please try again')
      }
    } finally {
      setSavingProfile(false)
    }
  }

  const handleChangePassword = async () => {
    if (newPw !== confirmPw) {
      toast.error('New passwords do not match')
      return
    }
    if (strength.score < 2) {
      toast.error('Password is too weak — please make it stronger')
      return
    }
    setSavingPw(true)
    try {
      await api.post('/api/v1/auth/change-password', {
        current_password: currentPw,
        new_password:     newPw,
      })
      toast.success('Password changed successfully')
      setCurrentPw('')
      setNewPw('')
      setConfirmPw('')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail ?? 'Failed to change password')
    } finally {
      setSavingPw(false)
    }
  }

  if (!user) return null

  const initials   = getInitials(fullName || user.full_name, user.username)
  const colorEntry = COLOR_MAP[avatarColor] ?? COLOR_MAP['blue']

  return (
    <div className="space-y-6 max-w-2xl">
      <PageHeader
        title="My Profile"
        subtitle="Update your display name, avatar, password, and two-factor authentication"
      />

      {/* ── Profile Information ───────────────────────────────────────────── */}
      <section className="card space-y-6">
        <h2 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider flex items-center gap-2">
          <UserCircle2 className="h-4 w-4 text-[var(--color-text-muted)]" />
          Profile Information
        </h2>

        {/* Avatar preview + colour picker */}
        <div className="flex items-start gap-6">
          {/* Live preview */}
          <div className={`h-16 w-16 rounded-full flex items-center justify-center text-white font-bold text-xl flex-shrink-0 ${colorEntry.bg}`}>
            {initials}
          </div>

          {/* Colour swatches */}
          <div className="flex-1">
            <p className="text-xs font-medium text-[var(--color-text-muted)] mb-2">Avatar colour</p>
            <div className="flex flex-wrap gap-2">
              {AVATAR_COLORS.map(c => (
                <button
                  key={c.key}
                  type="button"
                  title={c.key}
                  onClick={() => setAvatarColor(c.key)}
                  className={`h-7 w-7 rounded-full ${c.bg} transition-all ${
                    avatarColor === c.key
                      ? `ring-2 ring-offset-2 ring-offset-[var(--color-bg-secondary)] ${c.ring} scale-110`
                      : 'opacity-70 hover:opacity-100'
                  }`}
                >
                  {avatarColor === c.key && (
                    <Check className="h-3.5 w-3.5 text-white mx-auto" />
                  )}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Full name */}
        <div>
          <label htmlFor="profile-field-0" className="block text-sm font-medium text-[var(--color-text)] mb-1">
            Full Name
          </label>
          <input id="profile-field-0"
            type="text"
            className="input w-full"
            placeholder="Your display name"
            value={fullName}
            onChange={e => setFullName(e.target.value)}
            maxLength={255}
          />
        </div>

        {/* Read-only fields */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="profile-field-1" className="block text-sm font-medium text-[var(--color-text-muted)] mb-1">
              Username <span className="text-[10px] font-normal">(read-only)</span>
            </label>
            <input id="profile-field-1"
              type="text"
              className="input w-full opacity-60 cursor-not-allowed"
              value={user.username}
              readOnly
            />
          </div>
          <div>
            <label htmlFor="profile-field-2" className="block text-sm font-medium text-[var(--color-text-muted)] mb-1">
              Email <span className="text-[10px] font-normal">(read-only)</span>
            </label>
            <input id="profile-field-2"
              type="email"
              className="input w-full opacity-60 cursor-not-allowed"
              value={user.email}
              readOnly
            />
          </div>
        </div>

        {/* Role badge */}
        <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <span>Role:</span>
          <span className="px-2 py-0.5 rounded text-xs font-medium bg-[var(--color-bg-secondary)] text-[var(--color-text)] border border-[var(--color-border)]">
            {user.role}
          </span>
        </div>

        <div className="flex justify-end">
          <button
            className="btn-primary flex items-center gap-2"
            onClick={handleSaveProfile}
            disabled={savingProfile}
          >
            {savingProfile ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            Save Profile
          </button>
        </div>
      </section>

      {/* ── Change Password ───────────────────────────────────────────────── */}
      <section className="card space-y-5">
        <h2 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-[var(--color-text-muted)]" />
          Change Password
        </h2>

        {/* Current password */}
        <div>
          <label htmlFor="profile-field-3" className="block text-sm font-medium text-[var(--color-text)] mb-1">
            Current Password
          </label>
          <div className="relative">
            <input id="profile-field-3"
              type={showCur ? 'text' : 'password'}
              className="input w-full pr-10"
              placeholder="Enter current password"
              value={currentPw}
              onChange={e => setCurrentPw(e.target.value)}
              autoComplete="current-password"
            />
            <button
              type="button"
              className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              onClick={() => setShowCur(v => !v)}
              tabIndex={-1}
            >
              {showCur ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* New password */}
        <div>
          <label htmlFor="profile-field-4" className="block text-sm font-medium text-[var(--color-text)] mb-1">
            New Password
          </label>
          <div className="relative">
            <input id="profile-field-4"
              type={showNew ? 'text' : 'password'}
              className="input w-full pr-10"
              placeholder="Enter new password"
              value={newPw}
              onChange={e => setNewPw(e.target.value)}
              autoComplete="new-password"
            />
            <button
              type="button"
              className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              onClick={() => setShowNew(v => !v)}
              tabIndex={-1}
            >
              {showNew ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>

          {/* Strength bar */}
          {newPw && (
            <div className="mt-2 space-y-1.5">
              <div className="flex gap-1">
                {[1, 2, 3, 4, 5].map(i => (
                  <div
                    key={i}
                    className={`h-1 flex-1 rounded-full transition-colors ${
                      i <= strength.score ? strength.color : 'bg-[var(--color-border)]'
                    }`}
                  />
                ))}
                <span className="text-xs text-[var(--color-text-muted)] ml-1 w-20">{strength.label}</span>
              </div>
              <ul className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                {strength.checks.map(c => (
                  <li key={c.label} className={`flex items-center gap-1 text-xs ${c.ok ? 'text-[var(--status-passed)]' : 'text-[var(--color-text-faint)]'}`}>
                    <Check className={`h-3 w-3 ${c.ok ? 'opacity-100' : 'opacity-0'}`} />
                    {c.label}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Confirm password */}
        <div>
          <label htmlFor="profile-field-5" className="block text-sm font-medium text-[var(--color-text)] mb-1">
            Confirm New Password
          </label>
          <div className="relative">
            <input id="profile-field-5"
              type={showConf ? 'text' : 'password'}
              className="input w-full pr-10"
              placeholder="Repeat new password"
              value={confirmPw}
              onChange={e => setConfirmPw(e.target.value)}
              autoComplete="new-password"
            />
            <button
              type="button"
              className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              onClick={() => setShowConf(v => !v)}
              tabIndex={-1}
            >
              {showConf ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          {confirmPw && newPw !== confirmPw && (
            <p className="text-xs text-[var(--status-failed)] mt-1">Passwords do not match</p>
          )}
        </div>

        <div className="flex justify-end">
          <button
            className="btn-primary flex items-center gap-2"
            onClick={handleChangePassword}
            disabled={savingPw || !currentPw || !newPw || !confirmPw || newPw !== confirmPw}
          >
            {savingPw ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
            Update Password
          </button>
        </div>
      </section>

      {/* ── Two-Factor Authentication ─────────────────────────────────────── */}
      <MfaSecuritySection />
    </div>
  )
}
