/**
 * W19 INT-06 on Settings › GitHub: PAT or GitHub App per project, "Install GitHub App" goes to GitHub's install
 * page, GitHub's return (installation_id + state) is posted once and cleared from the URL, and the admin card
 * never shows a secret — only "(set)".
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const app = vi.hoisted(() => ({
  getSettings: vi.fn(),
  putSettings: vi.fn(),
  installUrl: vi.fn(),
  setupCallback: vi.fn(),
}))
vi.mock('@/services/githubIntegrationService', () => ({ githubAppService: app }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import toast from 'react-hot-toast'
import { GitHubAppAdminCard, GitHubAuthMethod } from './GitHubAppSection'
import { useGitHubAppCallback } from '@/hooks/useGitHubAppCallback'

function Where() {
  const { pathname, search } = useLocation()
  return <output data-testid="where">{pathname + search}</output>
}

describe('GitHubAuthMethod', () => {
  beforeEach(() => vi.clearAllMocks())

  it('switches between PAT and GitHub App and starts the installation', async () => {
    const onChange = vi.fn()
    app.installUrl.mockResolvedValue({ url: 'https://github.com/apps/x/installations/new?state=s', expires_in: 600 })
    const assign = vi.fn()
    Object.defineProperty(window, 'location', { value: { ...window.location, assign }, writable: true })
    const { rerender } = render(<GitHubAuthMethod projectId="p1" authKind="pat" installationId={null} hasPat canEdit canInstallApp onChange={onChange} />)
    expect(screen.getByLabelText(/Personal access token \(stored\)/)).toBeChecked()
    fireEvent.click(screen.getByLabelText('GitHub App'))
    expect(onChange).toHaveBeenCalledWith('app')
    rerender(<GitHubAuthMethod projectId="p1" authKind="app" installationId={null} hasPat canEdit canInstallApp onChange={onChange} />)
    expect(screen.getByTestId('github-installation')).toHaveTextContent('Not installed for this project yet.')
    fireEvent.click(screen.getByRole('button', { name: 'Install GitHub App' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://github.com/apps/x/installations/new?state=s'))
    expect(app.installUrl).toHaveBeenCalledWith('p1')
    rerender(<GitHubAuthMethod projectId="p1" authKind="app" installationId={777} hasPat canEdit canInstallApp onChange={onChange} />)
    expect(screen.getByTestId('github-installation')).toHaveTextContent('Installation 777')
  })

  // Security audit 2026-11-18: the page navigated to whatever address the server returned.
  it.each(['javascript:alert(1)', '//attacker.example/install', '/settings', ''])(
    'does not leave the app for an install address that is not a web link (%s)',
    async (url) => {
      app.installUrl.mockResolvedValue({ url, expires_in: 600 })
      const assign = vi.fn()
      Object.defineProperty(window, 'location', { value: { ...window.location, assign }, writable: true })
      render(<GitHubAuthMethod projectId="p1" authKind="app" installationId={null} hasPat canEdit canInstallApp onChange={vi.fn()} />)
      const button = screen.getByRole('button', { name: 'Install GitHub App' })
      fireEvent.click(button)
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('not a secure web link')))
      expect(assign).not.toHaveBeenCalled()
      await waitFor(() => expect(button).toBeEnabled())
    },
  )

  it('leaves installation approval to an instance administrator', () => {
    render(<GitHubAuthMethod projectId="p1" authKind="app" installationId={null} hasPat canEdit canInstallApp={false} onChange={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Install GitHub App' })).not.toBeInTheDocument()
    expect(screen.getByText(/instance administrator must install or reinstall/i)).toBeInTheDocument()
  })
})

describe('useGitHubAppCallback', () => {
  beforeEach(() => vi.clearAllMocks())

  function Probe({ onDone }: { onDone: () => void }) {
    useGitHubAppCallback(true, onDone)
    return <Where />
  }

  it('posts installation_id + state once and clears them from the URL', async () => {
    app.setupCallback.mockResolvedValue({ project_id: 'p1', installation_id: 777, auth_kind: 'app' })
    const onDone = vi.fn()
    render(
      <MemoryRouter initialEntries={['/settings/github?installation_id=777&setup_action=install&state=abc.def']}>
        <Routes><Route path="/settings/github" element={<Probe onDone={onDone} />} /></Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(app.setupCallback).toHaveBeenCalledTimes(1)
    expect(app.setupCallback).toHaveBeenCalledWith(777, 'abc.def')
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/settings\/github$/)
  })
})

describe('GitHubAppAdminCard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows only (set) for secrets and sends a new key once', async () => {
    app.getSettings.mockResolvedValue({ app_id: '4242', slug: 'acme-app', private_key_set: true, webhook_secret_set: false, configured: true })
    app.putSettings.mockResolvedValue({ app_id: '4242', slug: 'acme-app', private_key_set: true, webhook_secret_set: true, configured: true })
    render(<GitHubAppAdminCard />)
    expect(await screen.findByTestId('github-app-status')).toHaveTextContent('Configured')
    expect(screen.getByText(/Private key \(PEM\) — \(set\)/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('4242')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save GitHub App' }))
    await waitFor(() => expect(app.putSettings).toHaveBeenCalledWith({ app_id: '4242', slug: 'acme-app', private_key: null, webhook_secret: null }))
  })

  // D-80: a PEM header as the placeholder made the empty field look filled in.
  it('the empty private-key field shows plain hint text, not a PEM header', async () => {
    app.getSettings.mockResolvedValue({ app_id: '', slug: '', private_key_set: false, webhook_secret_set: false, configured: false })
    const { container } = render(<GitHubAppAdminCard />)
    await screen.findByTestId('github-app-status')
    const placeholder = container.querySelector('textarea')?.getAttribute('placeholder')
    expect(placeholder).toBe('Paste the contents of the .pem file')
    expect(placeholder).not.toMatch(/BEGIN|-----/)
  })
})
