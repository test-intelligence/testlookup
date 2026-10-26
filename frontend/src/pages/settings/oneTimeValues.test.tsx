/**
 * W27b PAN-06 (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.3 "One-time secrets"): every one-time
 * secret G-C's pages show — Add User's temporary password, Invite User's link, the streaming key, the personal
 * key and the Default QA Lead's reset password — is shown ONCE, in the page, in the section that asked for it:
 *
 *  - it is on screen exactly there (never in a toast, a `role="status"` region, the URL or browser storage);
 *  - its section's own Done (or Close) removes it from the DOM;
 *  - opening another form in its place while it is shown asks first (the section keeps its slot dirty);
 *  - the QA lead reset (D-15) now SHOWS the password the prompt promised, and a refused clipboard says so in
 *    the toast instead of claiming it was copied.
 *
 * Add User and Invite User run on the REAL `useUserManagement` hooks under an app-shaped SWR provider (W27b
 * fix: `refreshUsers()` re-reads WITHOUT clearing the users keys; it used to write `undefined` into them, so the
 * list fell back to its spinner and the form holding the secret was unmounted before it was ever shown).
 *
 * Named oneTimeValues, not oneTimeSecrets (the contract's name): `.gitignore`'s `*secrets*` matches that name
 * case-insensitively on Windows, where `git add` would silently leave the file out of a commit.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect, type ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SwrMutateBridge } from '@/components/SwrMutateBridge'
import { resetAppMutate } from '@/utils/swrCacheMutate'

const svc = vi.hoisted(() => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  inviteUser: vi.fn(),
  listProjectMembers: vi.fn(),
  createApiKey: vi.fn(),
  listProjects: vi.fn(),
  resetQaLead: vi.fn(),
  createStreamingKey: vi.fn(),
  copy: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  toast: vi.fn(),
}))

vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    listUsers: (...a: unknown[]) => svc.listUsers(...a),
    // ADM-03: the Users page reads {items, total}; svc.listUsers still counts every read.
    listUsersPage: (...a: unknown[]) =>
      Promise.resolve(svc.listUsers(...a)).then((items: unknown) => ({
        items: Array.isArray(items) ? items : [], total: Array.isArray(items) ? items.length : 0,
      })),
    createUser: (...a: unknown[]) => svc.createUser(...a),
    inviteUser: (...a: unknown[]) => svc.inviteUser(...a),
    listProjectMembers: (...a: unknown[]) => svc.listProjectMembers(...a),
    createApiKey: (...a: unknown[]) => svc.createApiKey(...a),
    revokeApiKey: vi.fn(),
    updateUserRole: vi.fn(),
    updateUserStatus: vi.fn(),
    updateUserProfile: vi.fn(),
    getUserMemberships: vi.fn().mockResolvedValue([]),
    addProjectMember: vi.fn(),
    removeProjectMember: vi.fn(),
    updateProjectMemberRole: vi.fn(),
  },
}))
vi.mock('@/services/projectsService', () => ({
  projectsService: {
    list: (...a: unknown[]) => svc.listProjects(...a),
    update: vi.fn(),
    resetDefaultQaLeadPassword: (...a: unknown[]) => svc.resetQaLead(...a),
  },
}))
vi.mock('@/services/apiKeyService', () => ({
  apiKeyService: { create: (...a: unknown[]) => svc.createStreamingKey(...a), revoke: vi.fn() },
}))
vi.mock('@/hooks/useApiKeys', () => ({
  useApiKeys: () => ({ data: [], isLoading: false, error: undefined, mutate: vi.fn() }),
  refreshApiKeys: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/utils/clipboard', () => ({ copyTextToClipboard: (...a: unknown[]) => svc.copy(...a) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign((...a: unknown[]) => svc.toast(...a), {
    success: (...a: unknown[]) => svc.toastSuccess(...a),
    error: (...a: unknown[]) => svc.toastError(...a),
  }),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canManageUsers: true, canGenerateApiKeys: true, role: 'ADMIN' }),
}))
const auth = vi.hoisted(() => ({ state: { sessionGeneration: 1, user: { id: 'admin-1' }, fetchUser: () => Promise.resolve() } }))
vi.mock('@/store/authStore', () => ({
  useAuthStore: Object.assign(
    (selector: (s: typeof auth.state) => unknown) => selector(auth.state),
    { getState: () => auth.state },
  ),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: { activeProjectId: string; activeProject: { id: string; name: string } }) => unknown) =>
    selector({ activeProjectId: 'project-a', activeProject: { id: 'project-a', name: 'Project A' } }),
}))

import UserManagementPage from '../UserManagementPage'
import { ProjectMembersTab } from '../ProjectMembersTab'
import ApiKeysPage from './ApiKeysPage'
import PersonalKeysPage from './PersonalKeysPage'

// ── harness ────────────────────────────────────────────────────────────────

const seen = { url: '' }
function UrlProbe() {
  const location = useLocation()
  useEffect(() => {
    seen.url = `${location.pathname}${location.search}${location.hash}`
  })
  return null
}

function renderApp(ui: ReactNode, url = '/') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false, revalidateOnFocus: false }}>
      <SwrMutateBridge />
      <MemoryRouter initialEntries={[url]}>
        {ui}
        <UrlProbe />
      </MemoryRouter>
    </SWRConfig>,
  )
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

/** The secret is on screen exactly once, inside `section`, and nowhere it must never be. */
function expectShownOnceIn(section: HTMLElement, secret: string) {
  const holders = Array.from(document.body.querySelectorAll('*')).filter(
    (el) => el.children.length === 0 && (el.textContent ?? '').includes(secret),
  )
  expect(holders.length, 'the secret is on screen').toBeGreaterThan(0)
  for (const el of holders) expect(section.contains(el), 'only in its own section').toBe(true)
  for (const status of screen.queryAllByRole('status')) {
    expect(status.textContent ?? '', 'never in a role="status" region').not.toContain(secret)
  }
  expectNeverLeaked(secret)
}

/** No toast argument, no URL, no browser storage holds the secret. */
function expectNeverLeaked(secret: string) {
  const toastArgs = JSON.stringify([...svc.toast.mock.calls, ...svc.toastSuccess.mock.calls, ...svc.toastError.mock.calls])
  expect(toastArgs, 'no toast carries the secret').not.toContain(secret)
  expect(seen.url, 'the URL is read').not.toBe('')
  expect(seen.url, 'the URL never carries the secret').not.toContain(secret)
  for (const store of [window.localStorage, window.sessionStorage]) {
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i) ?? ''
      expect(`${key}=${store.getItem(key) ?? ''}`, 'browser storage never holds the secret').not.toContain(secret)
    }
  }
}

const gone = (secret: string) => expect(document.body.textContent ?? '', 'Done drops the secret').not.toContain(secret)

beforeEach(() => {
  for (const fn of Object.values(svc)) fn.mockReset()
  svc.copy.mockResolvedValue(true)
  svc.listProjects.mockResolvedValue([])
  window.localStorage.clear()
  window.sessionStorage.clear()
})
afterEach(() => resetAppMutate())

const PRIYA = { id: 'u-priya', email: 'priya@example.test', username: 'priya', full_name: 'Priya Nair', role: 'QA_ENGINEER', is_active: true, created_at: '2026-01-01T00:00:00Z' }
const JDOE = { id: 'u-jdoe', email: 'jane@example.test', username: 'jdoe', full_name: null, role: 'TESTER', is_active: true, created_at: '2026-10-17T00:00:00Z' }

// ── Add User / Invite User (real hooks) ────────────────────────────────────

describe('Add User: the temporary password is shown once, in its section', () => {
  const SECRET = 'Tmp-once-Zq81x'

  it('is shown after Create User while the list re-reads in place, never leaks, asks before Invite User replaces it, and Done drops it', async () => {
    const reread = deferred<unknown[]>()
    svc.listUsers.mockResolvedValueOnce([PRIYA]).mockReturnValueOnce(reread.promise)
    svc.createUser.mockResolvedValue({ id: JDOE.id, username: 'jdoe', temp_password: SECRET })
    renderApp(<UserManagementPage />, '/users')
    await screen.findByText('Priya Nair')

    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    const section = screen.getByRole('region', { name: 'Add User' })
    fireEvent.change(within(section).getByLabelText('Email address'), { target: { value: JDOE.email } })
    fireEvent.change(within(section).getByLabelText('Username'), { target: { value: 'jdoe' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Create User' }))

    await within(section).findByText(SECRET)
    expect(svc.createUser).toHaveBeenCalledWith(JDOE.email, 'jdoe', 'QA_ENGINEER', undefined)
    expect(within(section).getByText(/created\. Share the temporary password:/)).toBeInTheDocument()
    // The re-read is in flight: the list stays on screen (the hook no longer clears the users keys).
    await waitFor(() => expect(svc.listUsers).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('table'), 'the list is not replaced by the spinner while it re-reads').toBeInTheDocument()
    expect(screen.getByText('Priya Nair')).toBeInTheDocument()
    expectShownOnceIn(section, SECRET)
    expect(svc.toastSuccess).toHaveBeenCalledWith('User created')

    // Copy goes through the clipboard util, never a toast.
    fireEvent.click(within(section).getByRole('button', { name: 'Copy Password' }))
    await waitFor(() => expect(svc.copy).toHaveBeenCalledWith(SECRET))

    // Its only exit is Done: Escape is swallowed, there is no X.
    within(section).getByRole('button', { name: 'Done' }).focus()
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    expect(within(section).getByText(SECRET)).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'Close' })).toBeNull()

    // Opening Invite User in its place asks first; Keep editing keeps the password.
    fireEvent.click(screen.getByRole('button', { name: 'Invite User' }))
    const question = within(section).getByRole('group')
    expect(question).toHaveTextContent('Discard your changes to "Add User"?')
    expect(screen.queryByRole('region', { name: 'Invite User' })).toBeNull()
    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    expect(within(section).getByText(SECRET)).toBeInTheDocument()

    await act(async () => {
      reread.resolve([PRIYA, JDOE])
      await reread.promise
    })
    expect(await screen.findByText('jdoe', { selector: 'div' })).toBeInTheDocument()
    expect(within(section).getByText(SECRET), 'the re-read never drops it').toBeInTheDocument()

    fireEvent.click(within(section).getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('region', { name: 'Add User' })).toBeNull()
    gone(SECRET)
    expectNeverLeaked(SECRET)
  })
})

describe('Invite User: the invitation link is shown once, in its section', () => {
  const TOKEN = 'invite-tok-once-77'

  it('is shown after Send Invitation, never leaks, asks before Add User replaces it, and Close drops it', async () => {
    svc.listUsers.mockResolvedValue([PRIYA])
    svc.inviteUser.mockResolvedValue({ invitation_link: `/accept-invite?token=${TOKEN}` })
    renderApp(<UserManagementPage />, '/users')
    await screen.findByText('Priya Nair')

    fireEvent.click(screen.getByRole('button', { name: 'Invite User' }))
    const section = screen.getByRole('region', { name: 'Invite User' })
    fireEvent.change(within(section).getByLabelText('Email address'), { target: { value: 'new.hire@example.test' } })
    fireEvent.change(within(section).getByLabelText('Role'), { target: { value: 'VIEWER' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Send Invitation' }))

    const link = await within(section).findByText(new RegExp(`/accept-invite\\?token=${TOKEN}$`))
    expect(link.textContent).toBe(new URL(`/accept-invite?token=${TOKEN}`, window.location.origin).toString())
    expect(svc.inviteUser).toHaveBeenCalledWith('new.hire@example.test', 'VIEWER')
    expect(screen.getByRole('table')).toBeInTheDocument()
    expectShownOnceIn(section, TOKEN)

    fireEvent.click(within(section).getByRole('button', { name: 'Copy link' }))
    await waitFor(() => expect(svc.copy).toHaveBeenCalledWith(link.textContent))

    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    expect(within(section).getByRole('group')).toHaveTextContent('Discard your changes to "Invite User"?')
    expect(screen.queryByRole('region', { name: 'Add User' })).toBeNull()
    fireEvent.click(within(within(section).getByRole('group')).getByRole('button', { name: 'Keep editing' }))

    fireEvent.click(within(section).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'Invite User' })).toBeNull()
    gone(TOKEN)
    expectNeverLeaked(TOKEN)
  })
})

// ── The streaming key (/settings/api-keys) ─────────────────────────────────

describe('The streaming key: shown once in the generate form\'s place', () => {
  const SECRET = 'qai_stream_once_0001'
  const CREATED = {
    id: 'key-1', name: 'ci-runner', key_hint: 'qai_str...', raw_key: SECRET, scopes: ['stream:write'],
    project_id: 'project-a', is_active: true, expires_at: null, last_used_at: null, created_at: '2026-10-17T00:00:00Z',
  }

  async function generate() {
    fireEvent.click(screen.getByRole('button', { name: 'Generate streaming key' }))
    fireEvent.change(screen.getByPlaceholderText('ci-runner-prod'), { target: { value: 'ci-runner' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))
    return screen.findByRole('region', { name: 'API key generated' })
  }

  it('is shown once (key + its snippets, all in the section), never in a toast; Generate streaming key asks before replacing it; Discard drops it', async () => {
    svc.createStreamingKey.mockResolvedValue(CREATED)
    renderApp(<ApiKeysPage />, '/settings/api-keys')
    const section = await generate()
    expect(within(section).getByText(SECRET, { selector: 'code' })).toBeInTheDocument()
    expectShownOnceIn(section, SECRET)

    fireEvent.click(screen.getByRole('button', { name: 'Generate streaming key' }))
    const question = within(section).getByRole('group')
    expect(question).toHaveTextContent('Discard your changes to "API key generated"?')
    expect(screen.queryByPlaceholderText('ci-runner-prod'), 'nothing replaced it yet').toBeNull()
    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    expect(within(section).getByText(SECRET, { selector: 'code' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Generate streaming key' }))
    fireEvent.click(within(within(section).getByRole('group')).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'API key generated' })).toBeNull()
    expect(screen.getByPlaceholderText('ci-runner-prod'), 'the generate form took its place').toBeInTheDocument()
    gone(SECRET)
    expectNeverLeaked(SECRET)
  })

  it('Done, the X and Escape each close it and drop the key', async () => {
    svc.createStreamingKey.mockResolvedValue(CREATED)
    renderApp(<ApiKeysPage />, '/settings/api-keys')
    let section = await generate()
    fireEvent.click(within(section).getByRole('button', { name: 'Done' }))
    gone(SECRET)

    section = await generate()
    fireEvent.click(within(section).getByRole('button', { name: 'Close' }))
    gone(SECRET)

    section = await generate()
    expect(section.contains(document.activeElement), 'focus is inside').toBe(true)
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'API key generated' })).toBeNull()
    gone(SECRET)
    expectNeverLeaked(SECRET)
  })
})

// ── The personal key (/settings/my-api-keys) ───────────────────────────────

describe('The personal key: shown once under the intro row, Done its only exit', () => {
  const SECRET = 'qai_personal_once_0002'

  it('is shown once, never in a toast; Escape and Generate key keep it; Done drops it', async () => {
    svc.createApiKey.mockResolvedValue({ raw_key: SECRET })
    renderApp(<PersonalKeysPage />, '/settings/my-api-keys')
    fireEvent.click(screen.getByRole('button', { name: 'Generate key' }))
    const section = screen.getByRole('region', { name: 'Generate my API key' })
    fireEvent.change(within(section).getByLabelText('Key name'), { target: { value: 'laptop' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Generate' }))
    await within(section).findByText(SECRET)
    expect(within(section).getByText('Copy this key now — it will not be shown again.')).toBeInTheDocument()
    expectShownOnceIn(section, SECRET)

    within(section).getByRole('button', { name: 'Done' }).focus()
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    expect(within(section).getByText(SECRET), 'Escape is swallowed while the key is shown').toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Generate key' }))
    expect(within(section).getByText(SECRET), 'Generate key never replaces a shown key').toBeInTheDocument()

    fireEvent.click(within(section).getByRole('button', { name: 'Copy' }))
    await waitFor(() => expect(svc.copy).toHaveBeenCalledWith(SECRET))
    fireEvent.click(within(section).getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('region', { name: 'Generate my API key' })).toBeNull()
    gone(SECRET)
    expectNeverLeaked(SECRET)
  })
})

// ── The Default QA Lead's reset password (D-15) ────────────────────────────

describe('Reset QA Lead password (D-15): the new password is shown once in the card', () => {
  const SECRET = 'QaLead-once-Pw77'
  const EMAIL = 'qa-lead@example.test'
  const PROJECT = { id: 'p-1', name: 'Checkout', slug: 'checkout', is_active: true, created_at: '2026-01-01T00:00:00Z', default_qa_lead_user_id: null }
  const OTHER = { ...PROJECT, id: 'p-2', name: 'Search', slug: 'search' }
  const LEAD = { id: 'm-1', project_id: 'p-1', user_id: 'u-lead', role: 'QA_LEAD', full_name: 'Lead', username: 'lead', email: EMAIL, created_at: '2026-01-01T00:00:00Z' }

  async function openCardAndReset() {
    svc.listProjects.mockResolvedValue([PROJECT, OTHER])
    svc.listUsers.mockResolvedValue([])
    svc.listProjectMembers.mockResolvedValue([LEAD])
    svc.resetQaLead.mockResolvedValue({ email: EMAIL, password: SECRET })
    renderApp(<ProjectMembersTab isAdmin canManageUsers />)
    await screen.findByRole('option', { name: 'Checkout' })
    fireEvent.change(screen.getByLabelText('Select Project:'), { target: { value: 'p-1' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Reset QA Lead password' }))
    fireEvent.click(within(document.querySelector('[data-inline-confirm]') as HTMLElement).getByRole('button', { name: 'Reset password' }))
    await waitFor(() => expect(svc.resetQaLead).toHaveBeenCalledTimes(1))
    const result = await waitFor(() => {
      const el = document.querySelector('[data-qa-lead-reset-result]') as HTMLElement | null
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    return result
  }

  it('copied: today\'s toast; the password shown once in the card (never in a toast or status region); Done drops it', async () => {
    svc.copy.mockResolvedValue(true)
    const result = await openCardAndReset()
    expect(svc.resetQaLead).toHaveBeenCalledWith('p-1')
    expect(svc.copy).toHaveBeenCalledWith(SECRET)
    expect(result).toHaveTextContent(`New password for ${EMAIL}`)
    expect(within(result).getByText(SECRET, { selector: 'code' })).toBeInTheDocument()
    expect(result.closest('[role="status"]')).toBeNull()
    await waitFor(() =>
      expect(svc.toastSuccess).toHaveBeenCalledWith(`Password reset for ${EMAIL}. Copied to clipboard.`, { duration: 8000 }),
    )
    expectShownOnceIn(result, SECRET)

    // Asking again keeps it until the new reset is confirmed (the strip is the question; nothing sent yet).
    fireEvent.click(screen.getByRole('button', { name: 'Reset QA Lead password' }))
    expect(document.querySelector('[data-inline-confirm]')).not.toBeNull()
    expect(within(result).getByText(SECRET)).toBeInTheDocument()
    expect(svc.resetQaLead).toHaveBeenCalledTimes(1)
    fireEvent.click(within(document.querySelector('[data-inline-confirm]') as HTMLElement).getByRole('button', { name: 'Cancel' }))

    fireEvent.click(within(result).getByRole('button', { name: 'Done' }))
    expect(document.querySelector('[data-qa-lead-reset-result]')).toBeNull()
    gone(SECRET)
    expectNeverLeaked(SECRET)
  })

  it('a refused clipboard: the toast says so (it does not claim a copy), and the password is still shown once', async () => {
    svc.copy.mockResolvedValue(false)
    const result = await openCardAndReset()
    await waitFor(() =>
      expect(svc.toastSuccess).toHaveBeenCalledWith(`Password reset for ${EMAIL}. Copy it below — it is shown once.`, { duration: 8000 }),
    )
    expect(svc.toastSuccess).not.toHaveBeenCalledWith(`Password reset for ${EMAIL}. Copied to clipboard.`, expect.anything())
    expectShownOnceIn(result, SECRET)
  })

  it('a project change drops the shown password (it does not come back when the other project\'s card renders)', async () => {
    const result = await openCardAndReset()
    expect(within(result).getByText(SECRET)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Select Project:'), { target: { value: 'p-2' } })
    // The other project's members load and its Default QA Lead card renders again.
    await waitFor(() => expect(svc.listProjectMembers).toHaveBeenCalledWith('p-2'))
    expect(await screen.findByRole('button', { name: 'Reset QA Lead password' })).toBeInTheDocument()
    expect(document.querySelector('[data-qa-lead-reset-result]')).toBeNull()
    gone(SECRET)
    expectNeverLeaked(SECRET)
  })
})
