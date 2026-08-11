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
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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

const REQUIRED = [/project name/i, /slug/i]
const OPTIONAL = [/description/i, /jira project key/i, /ocp namespace/i]

async function openCreateDialog() {
  render(<ProjectsPage />)
  const btn = await screen.findByRole('button', { name: /new project/i })
  fireEvent.click(btn)
  await waitFor(() => expect(screen.getByText(/project name/i)).toBeTruthy())
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
})
