/**
 * The project forms must be usable with a screen reader.
 *
 * Found during UAT of the onboarding journey — the first real task a new user
 * performs. Every field in the "New Project" dialog rendered a visible
 * `<label>` ("Project Name *", "Slug *", …) that was **not associated with its
 * control**: no `htmlFor`, no wrapping `<label>`, no `aria-label`. Measured live
 * on the deployment, all five create-dialog inputs reported:
 *
 *     hasIdLabel: false, hasWrappingLabel: false, ariaLabel: null,
 *     ariaLabelledBy: null   // only a placeholder
 *
 * A screen reader therefore announces "edit text, blank" for the required
 * Project Name field, and because placeholders vanish on first keystroke even a
 * sighted user loses the field name while typing. That is WCAG 2.1 **1.3.1
 * Info and Relationships** and **3.3.2 Labels or Instructions**, on the primary
 * onboarding form.
 *
 * These query by accessible name only — `getByLabelText` resolves through the
 * same accessibility tree a screen reader uses, so they fail on unassociated
 * markup and pass once `htmlFor`/`id` are wired.
 */
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/services/projectsService', () => ({
  projectsService: {
    list: vi.fn(async () => []),
    getAll: vi.fn(async () => []),
    create: vi.fn(async () => ({})),
    update: vi.fn(async () => ({})),
    remove: vi.fn(async () => ({})),
    delete: vi.fn(async () => ({})),
  },
}))

// zustand store: the page calls it with a selector, so the mock must apply one.
const storeState = {
  setActiveProject: vi.fn(),
  refreshProjects: vi.fn(async () => []),   // resolves to the project list
  activeProjectId: null,
  activeProject: null,
  projects: [],
}
vi.mock('@/store/projectStore', () => ({
  useProjectStore: (selector?: (s: typeof storeState) => unknown) =>
    (selector ? selector(storeState) : storeState),
  ALL_PROJECTS_ID: 'all',
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, isQaLead: true, canEdit: true, canManageProjects: true }),
}))

vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import ProjectsPage from './ProjectsPage'
import { projectsService } from '@/services/projectsService'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'

const REQUIRED = [/project name/i, /slug/i]
const OPTIONAL = [/description/i, /jira project key/i, /ocp namespace/i]

async function openCreateDialog() {
  render(<ProjectsPage />)
  const btn = await screen.findByRole('button', { name: /new project/i })
  btn.focus()
  fireEvent.click(btn)
  await waitFor(() => expect(screen.getByText(/project name/i)).toBeTruthy())
  return btn
}

describe('ProjectsPage accessibility', () => {
  it('gives every create-dialog field an accessible name', async () => {
    await openCreateDialog()
    for (const label of [...REQUIRED, ...OPTIONAL]) {
      const field = screen.getByLabelText(label)
      expect(field).toBeTruthy()
    }
  })

  it('keeps the required fields required once they are labelled', async () => {
    await openCreateDialog()
    for (const label of REQUIRED) {
      expect((screen.getByLabelText(label) as HTMLInputElement).required).toBe(true)
    }
  })

  it('does not rely on placeholder text as the accessible name', async () => {
    await openCreateDialog()
    const name = screen.getByLabelText(/project name/i) as HTMLInputElement
    const id = name.getAttribute('id')
    // A placeholder is a hint, not a name: it disappears on input and several
    // screen readers ignore it entirely.
    expect(id).toBeTruthy()
    expect(document.querySelector(`label[for="${id}"]`)).toBeTruthy()
  })

  // W27b: New Project renders in the page (the in-page keyboard contract replaces the Tab trap): focus moves
  // in on Project Name, Tab from the last control is NOT prevented, Escape closes, focus returns to New Project.
  it('takes focus, does not trap Tab, closes on Escape, and restores the New Project trigger', async () => {
    const trigger = await openCreateDialog()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    const section = screen.getByRole('region', { name: 'New Project' })
    const focusable = Array.from(section.querySelectorAll<HTMLElement>(
      'button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled])',
    ))
    const first = focusable[0]
    const last = focusable[focusable.length - 1]

    expect(screen.getByLabelText(/project name/i)).toHaveFocus()
    last.focus()
    expect(fireEvent.keyDown(last, { key: 'Tab' }), 'Tab is not trapped').toBe(true)
    expect(last).toHaveFocus()
    first.focus()
    expect(fireEvent.keyDown(first, { key: 'Tab', shiftKey: true }), 'Shift+Tab is not trapped').toBe(true)
    expect(first).toHaveFocus()

    screen.getByLabelText(/project name/i).focus()
    fireEvent.keyDown(screen.getByLabelText(/project name/i), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'New Project' })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})

describe('ProjectsPage — the settings page template (UX redesign P5)', () => {
  it('has the compact header with the route help topic; New Project stays its one action', async () => {
    render(<ProjectsPage />)
    const newProject = await screen.findByRole('button', { name: /new project/i })
    expectTemplateHeader('Projects', '/projects')
    expect(newProject.closest('[data-page-header]')).not.toBeNull()
  })
})

// ── W27b PAN-05: the project forms and the Archive question are in the page ──

const LEGACY = { id: 'p-legacy', name: 'Legacy portal', slug: 'legacy-portal', description: '', is_active: true, created_at: '2026-01-07T09:00:00Z', tags: [] }
const OTHER = { id: 'p-other', name: 'Storefront', slug: 'storefront', description: '', is_active: true, created_at: '2026-01-08T09:00:00Z', tags: [] }

async function renderWithCards() {
  storeState.refreshProjects.mockImplementation(async () => [LEGACY, OTHER] as never[])
  render(<ProjectsPage />)
  await screen.findByText(LEGACY.name)
  return {
    card: (name: string) => screen.getByText(name, { selector: 'p' }).closest('.card') as HTMLElement,
  }
}

describe('ProjectsPage in the page (W27b)', () => {
  afterEach(() => {
    storeState.refreshProjects.mockImplementation(async () => [])
    vi.mocked(projectsService.delete).mockClear()
    storeState.setActiveProject.mockClear()
    vi.restoreAllMocks()
  })

  it('New Project opens right under the page header, as a region (no dialog)', async () => {
    await renderWithCards()
    fireEvent.click(screen.getByRole('button', { name: 'New Project' }))
    const section = screen.getByRole('region', { name: 'New Project' })
    expect(document.querySelector('[data-page-header]')?.nextElementSibling).toBe(section)
    expect(screen.getByRole('button', { name: 'Close new project dialog' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()

    // The header button stays reachable while the form is open (no backdrop): clicking it again keeps what is typed.
    fireEvent.change(screen.getByLabelText('Project Name *'), { target: { value: 'Payment Gateway API' } })
    fireEvent.click(screen.getByRole('button', { name: 'New Project' }))
    expect(screen.getByLabelText('Project Name *')).toHaveValue('Payment Gateway API')
    expect(screen.getByLabelText('Slug *')).toHaveValue('payment-gateway-api')
  })

  it('a card\'s Edit opens Edit Project as a full-width grid row right after that card; one project form at a time', async () => {
    const { card } = await renderWithCards()
    fireEvent.click(within(card(LEGACY.name)).getByTitle('Edit'))
    const section = screen.getByRole('region', { name: 'Edit Project' })
    const row = section.closest('[data-in-page-row]') as HTMLElement
    expect(row).toHaveClass('col-span-full')
    expect(card(LEGACY.name).nextElementSibling).toBe(row)
    expect(within(section).getByLabelText('Project Name *')).toHaveValue(LEGACY.name)
    expect(storeState.setActiveProject, 'opening the form never selects the card').not.toHaveBeenCalled()

    // Clean: New Project replaces it at once.
    fireEvent.click(screen.getByRole('button', { name: 'New Project' }))
    expect(screen.queryByRole('region', { name: 'Edit Project' })).toBeNull()
    expect(screen.getByRole('region', { name: 'New Project' })).toBeInTheDocument()

    // Typed: replacing it asks first, and Keep editing keeps it.
    fireEvent.change(screen.getByLabelText('Project Name *'), { target: { value: 'Typed' } })
    fireEvent.click(within(card(OTHER.name)).getByTitle('Edit'))
    const question = within(screen.getByRole('region', { name: 'New Project' })).getByRole('group')
    expect(question).toHaveTextContent('Discard your changes to "New Project"?')
    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Project Name *')).toHaveValue('Typed')
    expect(screen.queryByRole('region', { name: 'Edit Project' })).toBeNull()
  })

  // D-20's fallback: inside the ≈ 199 px card at 1280 the strip's Cancel + Archive left only 21 px (< 40 px), so it
  // asks in the full-width grid row right after the card, where Edit opens.
  it('Archive asks in the full-width row right after its card; the trigger sends nothing, the strip\'s Archive sends once; no native confirm', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    const { card } = await renderWithCards()
    fireEvent.click(within(card(LEGACY.name)).getByTitle('Archive'))
    const strip = document.querySelector('[data-inline-confirm]') as HTMLElement
    expect(strip, 'the question is asked').not.toBeNull()
    const row = strip.closest('[data-in-page-row]') as HTMLElement
    expect(row).toHaveClass('col-span-full')
    expect(card(LEGACY.name).nextElementSibling, 'right after its card').toBe(row)
    expect(card(LEGACY.name).contains(strip), 'not inside the select-on-click card').toBe(false)
    expect(strip.querySelector('p')?.textContent).toBe('Archive project "Legacy portal"? It will be hidden but not deleted.')
    expect(strip.querySelector('strong')?.textContent).toBe(LEGACY.name)
    expect(projectsService.delete, 'the trigger sends nothing').not.toHaveBeenCalled()

    fireEvent.click(within(strip).getByRole('button', { name: 'Cancel' }))
    expect(document.querySelector('[data-inline-confirm]')).toBeNull()
    expect(projectsService.delete).not.toHaveBeenCalled()

    fireEvent.click(within(card(LEGACY.name)).getByTitle('Archive'))
    const again = document.querySelector('[data-inline-confirm-action]') as HTMLElement
    expect(again).toHaveTextContent('Archive')
    fireEvent.click(again)
    await waitFor(() => expect(projectsService.delete).toHaveBeenCalledTimes(1))
    expect(projectsService.delete).toHaveBeenCalledWith(LEGACY.id)
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(storeState.setActiveProject, 'archiving never selects the card').not.toHaveBeenCalled()
  })
})
