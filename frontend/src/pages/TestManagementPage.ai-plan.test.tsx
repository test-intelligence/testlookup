/**
 * D-58: "AI Create Plan" never claims a plan it did not build.
 *
 * The page sent `project_id: ''` when no project was set, toasted "AI is building the test plan in the
 * background" for every accepted request and dropped the task id, so a task that failed (no approved case, no
 * model) was never reported. Now it sends the real project id, follows the task on the status route that
 * `record_ai_task_owner` set up, and toasts the worker's outcome: the plan built, or the worker's sentence. A
 * server refusal (400 / 409) is toasted once, by the API interceptor, with the server's sentence.
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import toast from 'react-hot-toast'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { testManagementService } from '@/services/testManagementService'
import { useAuthStore } from '@/store/authStore'
import TestManagementPage from './TestManagementPage'

const plansMutate = vi.hoisted(() => vi.fn())

vi.mock('@/store/projectStore', () => {
  const state = { activeProjectId: 'project-1', activeProject: { id: 'project-1', name: 'Project One' } }
  return {
    ALL_PROJECTS_ID: '__ALL__',
    useProjectStore: (selector: (s: typeof state) => unknown) => selector(state),
  }
})
vi.mock('@/hooks/useFeatureFlags', () => ({ useFeatureEnabled: () => false }))
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useTestCases: () => ({ data: { items: [], total: 0, page: 1, size: 25, pages: 0 }, error: undefined, isLoading: false, mutate: vi.fn() }),
  useAuditLog: () => ({ data: { items: [], total: 0, page: 1, size: 5, pages: 0 } }),
  useTestPlans: () => ({ data: { items: [], total: 0, page: 1, size: 10, pages: 0 }, isLoading: false, mutate: plansMutate }),
  usePlanItems: () => ({ data: [], isLoading: false, mutate: vi.fn() }),
  useUsers: () => ({ data: [] }),
}))
vi.mock('@/hooks/useSuites', () => ({ useSuites: () => ({ data: { items: [], total: 0 } }) }))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [] }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/pages/test-management/StrategyTab', () => ({ default: () => null }))
vi.mock('@/pages/test-management/DuplicatesTab', () => ({ default: () => null }))
vi.mock('@/pages/test-management/AuditTab', () => ({ default: () => null }))
vi.mock('@/pages/test-management/ApprovalsTab', () => ({ default: () => null }))
vi.mock('@/pages/test-management/KnowledgeGenerationTab', () => ({ default: () => null }))
vi.mock('@/services/testManagementService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testManagementService')>()
  return {
    ...actual,
    testManagementService: {
      ...actual.testManagementService,
      listSuites: vi.fn(async () => []),
      listReviewsForRun: vi.fn(async () => []),
      aiCreatePlanAsync: vi.fn(),
      aiTaskStatus: vi.fn(),
    },
  }
})
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), loading: vi.fn() }),
}))

const NO_ANSWER = 'The model did not answer, so no test plan was built. Check Settings › AI and try again.'

function renderPlans() {
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/test-management?tab=plans']}>
        <Routes>
          <Route path="/test-management" element={<TestManagementPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

async function clickAiCreatePlan() {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /AI Create Plan/ })) })
}

/** Lets the page's next status poll run (AI_PLAN_POLL_MS = 3 s). */
async function nextPoll() {
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
}

beforeEach(() => {
  useAuthStore.setState({ user: { id: 'u-qa', role: 'QA_ENGINEER' } as never })
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  vi.mocked(toast).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
  vi.mocked(toast.loading).mockReset()
  plansMutate.mockReset()
  vi.mocked(testManagementService.aiCreatePlanAsync).mockReset()
  vi.mocked(testManagementService.aiTaskStatus).mockReset()
})
afterEach(() => {
  vi.useRealTimers()
  useAuthStore.setState({ user: null })
})

describe('AI Create Plan follows its task (D-58)', () => {
  it('sends the project id and reports a failed task with the worker\'s sentence, never success', async () => {
    vi.mocked(testManagementService.aiCreatePlanAsync).mockResolvedValue({ task_id: 't-1', status: 'queued' })
    vi.mocked(testManagementService.aiTaskStatus)
      .mockResolvedValueOnce({ task_id: 't-1', status: 'pending' })
      .mockResolvedValueOnce({ task_id: 't-1', status: 'failure', error: NO_ANSWER })
    renderPlans()

    await clickAiCreatePlan()
    expect(testManagementService.aiCreatePlanAsync).toHaveBeenCalledWith({ project_id: 'project-1' })
    expect(toast.loading).toHaveBeenCalledWith('AI is building the test plan…', { id: 'ai-plan-t-1' })

    await nextPoll()
    expect(toast.error).not.toHaveBeenCalled()
    await nextPoll()

    expect(testManagementService.aiTaskStatus).toHaveBeenCalledWith('t-1')
    expect(toast.error).toHaveBeenCalledWith(NO_ANSWER, { id: 'ai-plan-t-1' })
    expect(toast.success).not.toHaveBeenCalled()
    expect(plansMutate).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /AI Create Plan/ })).not.toBeDisabled()
  })

  it('a built plan is reported once the task succeeds, and the list reloads', async () => {
    vi.mocked(testManagementService.aiCreatePlanAsync).mockResolvedValue({ task_id: 't-2', status: 'queued' })
    vi.mocked(testManagementService.aiTaskStatus).mockResolvedValue({ task_id: 't-2', status: 'success' })
    renderPlans()

    await clickAiCreatePlan()
    expect(toast.success).not.toHaveBeenCalled()             // queued is not built
    await nextPoll()

    expect(toast.success).toHaveBeenCalledWith('AI built the test plan.', { id: 'ai-plan-t-2' })
    expect(plansMutate).toHaveBeenCalledTimes(1)
  })

  it('a failure without a sentence shows the generic line, never raw text (review finding 10)', async () => {
    vi.mocked(testManagementService.aiCreatePlanAsync).mockResolvedValue({ task_id: 't-3', status: 'queued' })
    vi.mocked(testManagementService.aiTaskStatus).mockResolvedValue({ task_id: 't-3', status: 'failure', error: undefined })
    renderPlans()

    await clickAiCreatePlan()
    await nextPoll()

    expect(toast.error).toHaveBeenCalledWith('The plan could not be built. Try again later.', { id: 'ai-plan-t-3' })
  })

  it('stops following when the page goes away (review finding 8)', async () => {
    vi.mocked(testManagementService.aiCreatePlanAsync).mockResolvedValue({ task_id: 't-4', status: 'queued' })
    vi.mocked(testManagementService.aiTaskStatus).mockResolvedValue({ task_id: 't-4', status: 'pending' })
    renderPlans()
    await clickAiCreatePlan()
    await nextPoll()
    const readsBefore = vi.mocked(testManagementService.aiTaskStatus).mock.calls.length
    expect(readsBefore).toBe(1)

    cleanup()                                                  // unmount the page
    for (let i = 0; i < 5; i += 1) await nextPoll()

    expect(vi.mocked(testManagementService.aiTaskStatus).mock.calls.length).toBe(readsBefore)
    expect(plansMutate).not.toHaveBeenCalled()
    expect(toast).toHaveBeenCalledWith('The test plan is still being built. Refresh the list in a moment.', { id: 'ai-plan-t-4' })
  })

  it('one failed status read does not end the follow (review finding 8)', async () => {
    vi.mocked(testManagementService.aiCreatePlanAsync).mockResolvedValue({ task_id: 't-5', status: 'queued' })
    vi.mocked(testManagementService.aiTaskStatus)
      .mockRejectedValueOnce(new Error('502'))
      .mockRejectedValueOnce(new Error('502'))
      .mockResolvedValueOnce({ task_id: 't-5', status: 'success' })
    renderPlans()

    await clickAiCreatePlan()
    await nextPoll()
    await nextPoll()
    expect(toast.error).not.toHaveBeenCalled()
    await nextPoll()

    expect(toast.success).toHaveBeenCalledWith('AI built the test plan.', { id: 'ai-plan-t-5' })
  })

  it('past its limit it says the plan is still being built, not that it failed (review finding 8)', async () => {
    vi.mocked(testManagementService.aiCreatePlanAsync).mockResolvedValue({ task_id: 't-6', status: 'queued' })
    vi.mocked(testManagementService.aiTaskStatus).mockResolvedValue({ task_id: 't-6', status: 'pending' })
    renderPlans()

    await clickAiCreatePlan()
    await act(async () => { await vi.advanceTimersByTimeAsync(11 * 60 * 1000) })
    expect(toast).not.toHaveBeenCalledWith('The test plan is still being built. Refresh the list in a moment.', expect.anything())
    await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60 * 1000) })

    expect(toast).toHaveBeenCalledWith('The test plan is still being built. Refresh the list in a moment.', { id: 'ai-plan-t-6' })
    expect(toast.error).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /AI Create Plan/ })).not.toBeDisabled()
  })

  it('a refusal (the server\'s sentence, toasted by the API interceptor) adds no toast and polls nothing', async () => {
    vi.mocked(testManagementService.aiCreatePlanAsync).mockRejectedValue({
      response: { status: 409, data: { detail: 'A test plan is built by a model, and no model can answer right now.' } },
    })
    renderPlans()

    await clickAiCreatePlan()
    await nextPoll()

    expect(toast.error).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
    expect(toast.loading).not.toHaveBeenCalled()
    expect(testManagementService.aiTaskStatus).not.toHaveBeenCalled()
  })
})
