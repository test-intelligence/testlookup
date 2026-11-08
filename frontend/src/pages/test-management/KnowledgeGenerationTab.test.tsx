import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import KnowledgeGenerationTab from './KnowledgeGenerationTab'
import { PANEL_TITLE_CLASS } from '@/components/ui/PanelHeader'

const state = { activeProjectId: 'project-a' }

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (value: typeof state) => unknown) => selector(state),
}))
vi.mock('@/hooks/useGenerationBatch', () => ({
  useRagStatus: () => ({ data: { enabled: true } }),
  useKnowledgeSources: () => ({
    data: { items: [{ id: 'source-a', title: 'Source A' }] }, isLoading: false,
  }),
}))
vi.mock('@/components/rag/KnowledgeSourcePicker', () => ({
  default: ({ selectedIds, onToggle }: { selectedIds: string[]; onToggle: (id: string) => void }) => (
    <button onClick={() => onToggle('source-a')}>Selected {selectedIds.length}</button>
  ),
}))
vi.mock('@/components/rag/GenerationReviewPanel', () => ({ default: () => <div>review result</div> }))
vi.mock('@/services/ragGenerationService', () => ({
  ragService: { syncSource: vi.fn(), retrieve: vi.fn(), generate: vi.fn() },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

describe('KnowledgeGenerationTab project isolation', () => {
  it('clears prompt and source selection when the active project changes', async () => {
    state.activeProjectId = 'project-a'
    const view = render(<MemoryRouter><KnowledgeGenerationTab /></MemoryRouter>)
    const prompt = screen.getByPlaceholderText(/Describe what test cases/) as HTMLTextAreaElement
    fireEvent.change(prompt, { target: { value: 'Project A secret prompt' } })
    fireEvent.click(screen.getByRole('button', { name: 'Selected 0' }))
    expect(prompt.value).toBe('Project A secret prompt')
    expect(screen.getByRole('button', { name: 'Selected 1' })).toBeInTheDocument()

    state.activeProjectId = 'project-b'
    view.rerender(<MemoryRouter><KnowledgeGenerationTab /></MemoryRouter>)
    await waitFor(() => {
      expect((screen.getByPlaceholderText(/Describe what test cases/) as HTMLTextAreaElement).value).toBe('')
      expect(screen.getByRole('button', { name: 'Selected 0' })).toBeInTheDocument()
    })
  })
})

describe('KnowledgeGenerationTab heading roles (W40 F-1)', () => {
  it('each card heading reads the panel-title role, and its card spaces with flex gap (a space-y gap loses to m-0)', () => {
    state.activeProjectId = 'project-a'
    render(<MemoryRouter><KnowledgeGenerationTab /></MemoryRouter>)
    for (const name of ['Knowledge Sources', 'Generation Prompt']) {
      const heading = screen.getByRole('heading', { level: 3, name })
      expect(heading.className).toBe(PANEL_TITLE_CLASS)
      // Tailwind v4: `space-y-*` reaches the children through a zero-specificity :where(), so the title's `m-0`
      // would zero the gap under it; the card spaces its children with `gap-*` instead.
      const card = heading.parentElement as HTMLElement
      expect(card.className.split(/\s+/)).toEqual(expect.arrayContaining(['card', 'flex', 'flex-col']))
      expect(card.className).not.toMatch(/\bspace-y-/)
    }
  })
})
