import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import FirstRunGuide, { FIRST_RUN_DISMISS_KEY } from './FirstRunGuide'
import { uploadCommand } from './firstRunSteps'

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
