import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FirstRunGuide from './FirstRunGuide'
import {
  uploadCommand,
  ingestApiCommand,
  DEFAULT_INGEST_URL,
  CLI_INSTALL_COMMAND,
  FIRST_RUN_DISMISS_KEY,
  firstRunDismissKey,
  isFirstRunGuideDismissed,
  dismissFirstRunGuide,
  SUPPORTED_FORMAT_SUMMARY,
  INGEST_FORMAT_LABELS,
} from './firstRunSteps'
import { SUPPORTED_FORMATS } from '@/services/reportUploadService'
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
    // The "Release gate" button is the release-risk gate feature (the Sidebar's
    // "Release Gate" → /release-gate, a role-open route), NOT the management-only
    // Releases-workflow CRUD page at /releases. Regression: it linked to
    // /releases — a different feature, AND a route gated to QA_LEAD/ADMIN, so a
    // fresh ENGINEER/viewer self-hoster clicking it during first-run was
    // silently bounced to /overview. The other two buttons already match their
    // feature routes (/failures, /flaky-coach); this brings the third in line.
    expect(screen.getByRole('link', { name: /release gate/i })).toHaveAttribute('href', '/release-gate')
    expect(screen.getByRole('link', { name: /release gate/i })).not.toHaveAttribute('href', '/releases')
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

  it('links the docs hand-off to the in-app route, not external github', () => {
    // TestLookup is local-first and routinely self-hosted air-gapped, where an
    // outbound github.com/blob/main/GETTING_STARTED.md link is dead — the exact
    // first-run reader who most needs the docs can't reach them. The guide must
    // point at the in-app /docs/getting-started page (served off the
    // deployment's own origin) so the docs always resolve.
    renderGuide()
    const docs = screen.getByRole('link', { name: /documentation/i })
    expect(docs).toHaveAttribute('href', '/docs/getting-started')
    // It is not an off-origin absolute URL (github.com, or any http(s):// host).
    expect(docs.getAttribute('href')).not.toMatch(/^https?:\/\//)
    expect(docs.getAttribute('href')).not.toContain('github.com')
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

  describe('advertised ingest formats', () => {
    // The guide's step-2 body must name every format the backend `/ingest/file`
    // endpoint accepts, so a self-hoster can see their CI runner is supported.
    // Regression: the guide long advertised only six of the eleven — a NUnit /
    // xUnit / TRX / Robot / Cucumber shop saw its format missing and could
    // reasonably conclude TestLookup couldn't ingest it, when it can.
    const nonAuto = SUPPORTED_FORMATS.filter((f) => f.value !== 'auto')

    it('names every non-auto supported format from the canonical registry', () => {
      renderGuide()
      const step = screen.getByText(/Point your CI at the ingest API/i).textContent ?? ''
      const haystack = step.toLowerCase()
      for (const f of nonAuto) {
        // Each canonical format value appears (case-insensitively) inside its
        // display label — 'nunit' in "NUnit", 'trx' in "TRX", etc.
        expect(haystack).toContain(f.value.toLowerCase())
      }
    })

    it('surfaces the formats that were previously omitted', () => {
      renderGuide()
      const step = screen.getByText(/Point your CI at the ingest API/i).textContent ?? ''
      for (const label of ['NUnit', 'xUnit', 'TRX', 'Robot Framework', 'Cucumber']) {
        expect(step).toContain(label)
      }
    })

    it('derives the summary from the registry, excluding the auto mode', () => {
      // 'auto' is the detection MODE (the endpoint default), not a report
      // format, so it is not advertised as one.
      expect(SUPPORTED_FORMAT_SUMMARY).not.toMatch(/\bauto(-detect)?\b/i)
      expect(SUPPORTED_FORMAT_SUMMARY).toBe(
        nonAuto
          .map((f) => INGEST_FORMAT_LABELS[f.value as keyof typeof INGEST_FORMAT_LABELS])
          .join(' / '),
      )
    })

    it('has a display label for every non-auto format (no blank slots)', () => {
      for (const f of nonAuto) {
        const label = INGEST_FORMAT_LABELS[f.value as keyof typeof INGEST_FORMAT_LABELS]
        expect(label).toBeTruthy()
        expect(label.trim()).toBe(label)
      }
    })
  })

  it('offers the CLI install command so the upload command is actually runnable', () => {
    // Regression: step 2 shows `testlookup upload …`, but the CLI is not on
    // PyPI — it ships in the repo (cli/pyproject.toml). A fresh self-hoster who
    // copies the upload command hits `command not found: testlookup` unless the
    // guide points at the editable install first.
    renderGuide()
    expect(screen.getByText(/No.*command yet\?.*install it once/i)).toBeInTheDocument()
    expect(screen.getByText(CLI_INSTALL_COMMAND)).toBeInTheDocument()
    expect(CLI_INSTALL_COMMAND).toBe('pip install -e cli/')
    // It is not the (non-existent) PyPI package.
    expect(CLI_INSTALL_COMMAND).not.toContain('install testlookup-cli')
  })

  it('copies the CLI install command verbatim', async () => {
    renderGuide()
    fireEvent.click(screen.getByRole('button', { name: `Copy command: ${CLI_INSTALL_COMMAND}` }))
    await waitFor(() => expect(copyMock).toHaveBeenCalledWith(CLI_INSTALL_COMMAND))
  })

  it('points to the API Keys settings page for the credential the CI curl needs', () => {
    // The ingest curl sends X-API-Key; a new self-hoster has no key yet. The
    // guide must hand off to /settings/api-keys — the in-app, project-scoped
    // key generator — so the copied command is actually runnable.
    renderGuide()
    expect(screen.getByRole('link', { name: /Settings → API Keys/i })).toHaveAttribute(
      'href',
      '/settings/api-keys',
    )
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
      '-H "X-API-Key: $TL_API_KEY" -F file=@results.xml -F project_id='

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
        `curl -X POST ${url} -H "X-API-Key: $TL_API_KEY" ` +
          `-F file=@results.xml -F project_id=abc-123 -F build_number=<build> -F format=auto`,
      )
      expect(cmd).not.toContain('localhost:8000')
    })

    it('authenticates the CI curl with a project API key, not a login bearer token', () => {
      // The apiCommand targets unattended CI runners, which need a long-lived,
      // project-scoped credential (an API key sent as X-API-Key) rather than the
      // short-lived login-session bearer token from the quickstart. /api/v1/ingest/file
      // accepts either (get_api_key_context), so this is the self-servable path.
      const cmd = ingestApiCommand('abc-123')
      expect(cmd).toContain('-H "X-API-Key: $TL_API_KEY"')
      expect(cmd).not.toContain('Authorization: Bearer')
    })
  })

  it('copies a command to the clipboard', async () => {
    renderGuide()
    fireEvent.click(screen.getByRole('button', { name: /copy command: make quickstart/i }))
    await waitFor(() => expect(copyMock).toHaveBeenCalledWith('make quickstart'))
  })

  it('surfaces a manual-copy hint when the clipboard is unavailable', async () => {
    // Regression: a plain-HTTP self-host can block both clipboard paths (the
    // secure-context async API and the legacy execCommand fallback). A failed
    // copy must not be silently inert — the guide points the user at the
    // manual copy so the command stays reachable.
    copyMock.mockResolvedValueOnce(false)
    renderGuide()
    expect(screen.queryByRole('status')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /copy command: make quickstart/i }))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/select the command above/i),
    )
  })

  it('shows no failure hint on a successful copy', async () => {
    copyMock.mockResolvedValueOnce(true)
    renderGuide()
    fireEvent.click(screen.getByRole('button', { name: /copy command: make quickstart/i }))
    await waitFor(() => expect(copyMock).toHaveBeenCalled())
    expect(screen.queryByRole('status')).toBeNull()
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

  describe('per-scope dismissal', () => {
    beforeEach(() => {
      // Purge only this feature's keys so a prior test's dismissal cannot leak;
      // leave the rest of localStorage (e.g. the time-window store) untouched.
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const k = localStorage.key(i)
        if (k && k.startsWith(FIRST_RUN_DISMISS_KEY)) localStorage.removeItem(k)
      }
    })

    it('namespaces the key under the base key, per scope', () => {
      expect(firstRunDismissKey('proj-1')).toBe('tl_first_run_guide_dismissed:proj-1')
      expect(firstRunDismissKey('__ALL__')).toBe('tl_first_run_guide_dismissed:__ALL__')
    })

    it('remembers dismissal for the scope it was set on', () => {
      expect(isFirstRunGuideDismissed('proj-1')).toBe(false)
      dismissFirstRunGuide('proj-1')
      expect(isFirstRunGuideDismissed('proj-1')).toBe(true)
    })

    it('does NOT suppress the guide on a different, still-empty scope', () => {
      // The whole point of scoping: onboarding project A must not hide the
      // first-run help for a genuinely new project B created later.
      dismissFirstRunGuide('proj-A')
      expect(isFirstRunGuideDismissed('proj-A')).toBe(true)
      expect(isFirstRunGuideDismissed('proj-B')).toBe(false)
      expect(isFirstRunGuideDismissed('__ALL__')).toBe(false)
    })

    it('ignores the legacy browser-wide flag — it no longer suppresses any scope', () => {
      // Pre-scoping builds wrote the bare key with no scope suffix. That value
      // must not be read as "dismissed everywhere", or the bug survives the fix.
      localStorage.setItem(FIRST_RUN_DISMISS_KEY, '1')
      expect(isFirstRunGuideDismissed('proj-1')).toBe(false)
      expect(isFirstRunGuideDismissed('__ALL__')).toBe(false)
    })

    it('treats a falsy scope as not-dismissed without touching storage', () => {
      dismissFirstRunGuide(null)
      dismissFirstRunGuide(undefined)
      dismissFirstRunGuide('')
      expect(isFirstRunGuideDismissed(null)).toBe(false)
      expect(isFirstRunGuideDismissed(undefined)).toBe(false)
      expect(isFirstRunGuideDismissed('')).toBe(false)
      // Nothing was persisted under the base key for a falsy scope.
      let wrote = false
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i)
        if (k && k.startsWith(FIRST_RUN_DISMISS_KEY)) wrote = true
      }
      expect(wrote).toBe(false)
    })
  })
})
