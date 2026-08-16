import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import FirstRunGuide, { FIRST_RUN_DISMISS_KEY } from './FirstRunGuide'
import { uploadCommand, ingestApiCommand, DEFAULT_INGEST_URL } from './firstRunSteps'
import { backendUrl } from '@/services/api'

const copyMock = vi.fn(async (_value: string) => true)
vi.mock('@/utils/clipboard', () => ({
  copyTextToClipboard: (v: string) => copyMock(v),
}))

function renderGuide(props = {}) {
  return render(
    <MemoryRouter>
      <FirstRunGuide {...props} />
    </MemoryRouter>,
  )
}

describe('FirstRunGuide', () => {
  it('shows the three onboarding steps with copy-paste commands', () => {
    renderGuide()
    expect(screen.getByText(/Load the demo dataset/i)).toBeInTheDocument()
    expect(screen.getByText('make quickstart')).toBeInTheDocument()
    expect(screen.getByText(/ingest your own test results/i)).toBeInTheDocument()
    expect(
      screen.getByText('testlookup upload file results.xml -p <project-id> -b <build>'),
    ).toBeInTheDocument()
    expect(screen.getByText(/explore the intelligence/i)).toBeInTheDocument()
  })

  it('links to the key first-insight destinations', () => {
    renderGuide()
    expect(screen.getByRole('link', { name: /failure analysis/i })).toHaveAttribute('href', '/failures')
    expect(screen.getByRole('link', { name: /flaky coach/i })).toHaveAttribute('href', '/flaky-coach')
    expect(screen.getByRole('link', { name: /release gate/i })).toHaveAttribute('href', '/releases')
  })

  it('offers an in-app path to the guided setup checklist', () => {
    // The empty-dashboard guide should hand off to the progress-tracked
    // /getting-started onboarding flow — an internal, offline-safe route,
    // not only the external GETTING_STARTED.md docs link.
    renderGuide()
    expect(screen.getByRole('link', { name: /setup checklist/i })).toHaveAttribute(
      'href',
      '/getting-started',
    )
  })

  it('shows the project name when provided', () => {
    renderGuide({ projectName: 'Checkout API' })
    expect(screen.getByText(/Checkout API/)).toBeInTheDocument()
  })

  it('splices the real project id into the upload command when scoped to a project', () => {
    const id = '6783f331-9f51-4b0b-a27a-acc31e117b21'
    renderGuide({ projectId: id })
    // The copy-ready command carries the actual id, not the placeholder.
    expect(
      screen.getByText(`testlookup upload file results.xml -p ${id} -b <build>`),
    ).toBeInTheDocument()
    expect(screen.queryByText(/-p <project-id>/)).toBeNull()
  })

  it('copies the id-substituted command verbatim', async () => {
    const id = '6783f331-9f51-4b0b-a27a-acc31e117b21'
    renderGuide({ projectId: id })
    fireEvent.click(screen.getByRole('button', { name: /copy command: testlookup upload/i }))
    await waitFor(() =>
      expect(copyMock).toHaveBeenCalledWith(
        `testlookup upload file results.xml -p ${id} -b <build>`,
      ),
    )
  })

  it('keeps the <project-id> placeholder in All Projects mode (no id / blank id)', () => {
    renderGuide()
    expect(
      screen.getByText('testlookup upload file results.xml -p <project-id> -b <build>'),
    ).toBeInTheDocument()
  })

  it('offers a raw ingest-API curl for CI runners that skip the CLI', () => {
    // Step 2 names the ingest API but historically only showed the CLI; CI
    // runners that curl the endpoint now get a copy-paste example too.
    renderGuide()
    expect(screen.getByText(/POST straight to the ingest API/i)).toBeInTheDocument()
    expect(
      screen.getByText(content => content.includes('/api/v1/ingest/file')),
    ).toBeInTheDocument()
  })

  it('splices the real project id into the ingest-API curl when scoped', () => {
    const id = '6783f331-9f51-4b0b-a27a-acc31e117b21'
    renderGuide({ projectId: id })
    expect(
      screen.getByText(content => content.includes(`project_id=${id}`)),
    ).toBeInTheDocument()
    // The build label stays a placeholder — it is per-run and caller-only.
    expect(
      screen.getByText(content => content.includes('build_number=<build>')),
    ).toBeInTheDocument()
  })

  it('copies the ingest-API curl verbatim', async () => {
    const id = '6783f331-9f51-4b0b-a27a-acc31e117b21'
    renderGuide({ projectId: id })
    fireEvent.click(screen.getByRole('button', { name: /copy command: curl -x post/i }))
    await waitFor(() =>
      // The rendered curl targets the deployment's own backend origin (resolved
      // the same way the axios client resolves requests), not the hardcoded
      // localhost:8000 default.
      expect(copyMock).toHaveBeenCalledWith(
        ingestApiCommand(id, backendUrl('/api/v1/ingest/file')),
      ),
    )
  })

  it('resolves the ingest curl through backendUrl, not a hardcoded host', () => {
    // Regression: the app is deploy-target-agnostic (services/api.ts resolves
    // the backend from VITE_API_BASE_URL, else same-origin behind an ingress).
    // The guide must render whatever THAT resolves to — proving it delegates to
    // backendUrl rather than embedding a fixed dev-only localhost:8000. What
    // backendUrl resolves to is exercised env-by-env in api.backendUrl.test.ts;
    // here we only assert the guide uses it.
    renderGuide()
    const resolved = backendUrl('/api/v1/ingest/file')
    expect(
      screen.getByText(content => content.includes(`curl -X POST ${resolved} `)),
    ).toBeInTheDocument()
  })

  describe('uploadCommand', () => {
    it('uses the placeholder for undefined, empty, and whitespace-only ids', () => {
      const placeholder = 'testlookup upload file results.xml -p <project-id> -b <build>'
      expect(uploadCommand()).toBe(placeholder)
      expect(uploadCommand('')).toBe(placeholder)
      expect(uploadCommand('   ')).toBe(placeholder)
    })

    it('splices a concrete id in', () => {
      expect(uploadCommand('abc-123')).toBe(
        'testlookup upload file results.xml -p abc-123 -b <build>',
      )
    })
  })

  describe('ingestApiCommand', () => {
    const base =
      'curl -X POST http://localhost:8000/api/v1/ingest/file ' +
      '-H "Authorization: Bearer $TL_TOKEN" -F file=@results.xml -F project_id='

    it('uses the placeholder for undefined, empty, and whitespace-only ids', () => {
      const placeholder = `${base}<project-id> -F build_number=<build> -F format=auto`
      expect(ingestApiCommand()).toBe(placeholder)
      expect(ingestApiCommand('')).toBe(placeholder)
      expect(ingestApiCommand('   ')).toBe(placeholder)
    })

    it('splices a concrete id in while keeping token and build placeholders', () => {
      expect(ingestApiCommand('abc-123')).toBe(
        `${base}abc-123 -F build_number=<build> -F format=auto`,
      )
    })

    it('defaults to the dev localhost endpoint when no ingest URL is given', () => {
      expect(DEFAULT_INGEST_URL).toBe('http://localhost:8000/api/v1/ingest/file')
      expect(ingestApiCommand('abc-123')).toContain(DEFAULT_INGEST_URL)
    })

    it('targets an explicit deployment ingest URL when provided', () => {
      const url = 'https://tl.example.com/api/v1/ingest/file'
      const cmd = ingestApiCommand('abc-123', url)
      expect(cmd).toBe(
        `curl -X POST ${url} -H "Authorization: Bearer $TL_TOKEN" ` +
          `-F file=@results.xml -F project_id=abc-123 -F build_number=<build> -F format=auto`,
      )
      expect(cmd).not.toContain('localhost:8000')
    })
  })

  it('copies a command to the clipboard', async () => {
    renderGuide()
    fireEvent.click(screen.getByRole('button', { name: /copy command: make quickstart/i }))
    await waitFor(() => expect(copyMock).toHaveBeenCalledWith('make quickstart'))
  })

  it('calls onDismiss when dismissed', () => {
    const onDismiss = vi.fn()
    renderGuide({ onDismiss })
    fireEvent.click(screen.getByRole('button', { name: /dismiss getting started/i }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('omits the dismiss control when no handler is given', () => {
    renderGuide()
    expect(screen.queryByRole('button', { name: /dismiss getting started/i })).toBeNull()
  })

  it('exports a stable dismiss key', () => {
    expect(FIRST_RUN_DISMISS_KEY).toBe('tl_first_run_guide_dismissed')
  })
})
