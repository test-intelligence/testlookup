import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import KnowledgeGenerationTab from './KnowledgeGenerationTab'

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
