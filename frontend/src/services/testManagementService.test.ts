import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { deleteData, getData, patchData, postData, putData } from './http'
import { testManagementService, usersService } from './testManagementService'

vi.mock('./api', () => ({
  api: { get: vi.fn() },
}))

vi.mock('./http', () => ({
  deleteData: vi.fn(),
  getData: vi.fn(),
  patchData: vi.fn(),
  postData: vi.fn(),
  putData: vi.fn(),
}))

describe('testManagementService request contracts', () => {
  beforeEach(() => vi.clearAllMocks())

  it('scopes list requests when a project is selected and preserves filters', () => {
    testManagementService.listCases('project-1', { status: 'approved', page: 2 })
    testManagementService.listPlans('project-1', { status: 'active' })
    testManagementService.getAuditLog('project-1', { entity_type: 'test_case', page: 3 })

    expect(getData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/cases', {
      params: { project_id: 'project-1', status: 'approved', page: 2 },
    })
    expect(getData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/plans', {
      params: { project_id: 'project-1', status: 'active' },
    })
    expect(getData).toHaveBeenNthCalledWith(3, '/api/v1/test-management/audit', {
      params: { project_id: 'project-1', entity_type: 'test_case', page: 3 },
    })
  })

  it('omits project_id instead of sending an all-project sentinel', () => {
    testManagementService.listCases(null, { page: 1 })
    testManagementService.listPlans(null)
    testManagementService.listStrategies(null)
    testManagementService.listSuites(null)

    expect(getData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/cases', {
      params: { page: 1 },
    })
    expect(getData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/plans', {
      params: {},
    })
    expect(getData).toHaveBeenNthCalledWith(3, '/api/v1/test-management/strategies', {
      params: {},
    })
    expect(getData).toHaveBeenNthCalledWith(4, '/api/v1/test-management/suites', {
      params: {},
    })
  })

  it('uses the case CRUD, history, review, and comment endpoints', () => {
    const draft = { title: 'Checkout' }
    testManagementService.createCase(draft)
    testManagementService.getCase('case-1')
    testManagementService.updateCase('case-1', {
      expected_version: 4,
      title: 'Checkout v2',
      change_summary: 'clarify',
    })
    testManagementService.getCaseHistory('case-1')
    testManagementService.getCaseReviews('case-1')
    testManagementService.getCaseComments('case-1')
    testManagementService.addComment('case-1', { content: 'Looks good', comment_type: 'general' })
    testManagementService.requestReview('case-1')
    testManagementService.reviewAction('case-1', 'approve', 'verified')

    expect(postData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/cases', draft)
    expect(getData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/cases/case-1')
    expect(patchData).toHaveBeenCalledWith('/api/v1/test-management/cases/case-1', {
      expected_version: 4,
      title: 'Checkout v2',
      change_summary: 'clarify',
    })
    expect(getData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/cases/case-1/history')
    expect(getData).toHaveBeenNthCalledWith(3, '/api/v1/test-management/cases/case-1/reviews')
    expect(getData).toHaveBeenNthCalledWith(4, '/api/v1/test-management/cases/case-1/comments')
    expect(postData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/cases/case-1/comments', {
      content: 'Looks good',
      comment_type: 'general',
    })
    expect(postData).toHaveBeenNthCalledWith(3, '/api/v1/test-management/cases/case-1/request-review')
    expect(postData).toHaveBeenNthCalledWith(4, '/api/v1/test-management/cases/case-1/review-action', {
      action: 'approve',
      notes: 'verified',
    })
  })

  it('keeps optional delete reasons out of the request when absent', () => {
    testManagementService.deleteCase('case-1')
    expect(deleteData).toHaveBeenCalledWith('/api/v1/test-management/cases/case-1', undefined, undefined)
  })

  it('uses the exact synchronous and asynchronous AI operation contracts', () => {
    const generation = { project_id: 'project-1', requirements: 'checkout', persist: true }
    testManagementService.aiGenerate(generation)
    testManagementService.aiGenerateAsync(generation)
    testManagementService.aiTaskStatus('task-1')
    testManagementService.aiReview('case-1')
    testManagementService.aiCoverage({ project_id: 'project-1', requirements: 'checkout' })

    expect(postData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/cases/ai-generate', generation)
    expect(postData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/cases/ai-generate/async', generation)
    expect(getData).toHaveBeenCalledWith('/api/v1/test-management/cases/ai-task/task-1')
    expect(postData).toHaveBeenNthCalledWith(3, '/api/v1/test-management/cases/case-1/ai-review')
    expect(postData).toHaveBeenNthCalledWith(4, '/api/v1/test-management/cases/ai-coverage', {
      project_id: 'project-1',
      requirements: 'checkout',
    })
  })

  it('uses the plan item and execution contracts', () => {
    testManagementService.createPlan({ name: 'Regression' })
    testManagementService.getPlan('plan-1')
    testManagementService.updatePlan('plan-1', { status: 'active' })
    testManagementService.getPlanItems('plan-1')
    testManagementService.addPlanItem('plan-1', { test_case_id: 'case-1', order_index: 2 })
    testManagementService.removePlanItem('plan-1', 'item-1')
    testManagementService.executeItem('plan-1', 'item-1', {
      execution_status: 'passed',
      actual_duration_minutes: 4,
    })

    expect(postData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/plans', { name: 'Regression' })
    expect(getData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/plans/plan-1')
    expect(patchData).toHaveBeenCalledWith('/api/v1/test-management/plans/plan-1', { status: 'active' })
    expect(getData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/plans/plan-1/items')
    expect(postData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/plans/plan-1/items', {
      test_case_id: 'case-1',
      order_index: 2,
    })
    expect(deleteData).toHaveBeenCalledWith('/api/v1/test-management/plans/plan-1/items/item-1')
    expect(postData).toHaveBeenNthCalledWith(3, '/api/v1/test-management/plans/plan-1/items/item-1/execute', {
      execution_status: 'passed',
      actual_duration_minutes: 4,
    })
  })

  it('uses the plan and strategy AI contracts', () => {
    const plan = { project_id: 'project-1', plan_name: 'Release' }
    const strategy = { project_id: 'project-1', project_context: 'web app', strategy_name: 'Risk based' }
    testManagementService.aiCreatePlan(plan)
    testManagementService.aiCreatePlanAsync(plan)
    testManagementService.getStrategy('strategy-1')
    testManagementService.updateStrategy('strategy-1', { status: 'approved' })
    testManagementService.aiGenerateStrategy(strategy)
    testManagementService.aiGenerateStrategyAsync(strategy)

    expect(postData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/plans/ai-create', plan)
    expect(postData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/plans/ai-create/async', plan)
    expect(getData).toHaveBeenCalledWith('/api/v1/test-management/strategies/strategy-1')
    expect(putData).toHaveBeenCalledWith('/api/v1/test-management/strategies/strategy-1', { status: 'approved' })
    expect(postData).toHaveBeenNthCalledWith(3, '/api/v1/test-management/strategies/ai-generate', strategy)
    expect(postData).toHaveBeenNthCalledWith(4, '/api/v1/test-management/strategies/ai-generate/async', strategy)
  })

  it('builds export URLs without empty query values', () => {
    expect(testManagementService.exportCasesExcelUrl('project 1', {
      status: 'approved',
      empty: '',
      absent: null,
      page: 2,
    })).toBe('/api/v1/test-management/cases/export/excel?project_id=project+1&status=approved&page=2')
    expect(testManagementService.exportPlanWordUrl('plan-1')).toBe('/api/v1/test-management/plans/plan-1/export/word')
    expect(testManagementService.exportPlanPdfUrl('plan-1')).toBe('/api/v1/test-management/plans/plan-1/export/pdf')
    expect(testManagementService.exportStrategyWordUrl('strategy-1')).toBe('/api/v1/test-management/strategies/strategy-1/export/word')
    expect(testManagementService.exportStrategyPdfUrl('strategy-1')).toBe('/api/v1/test-management/strategies/strategy-1/export/pdf')
  })

  it('encodes suite names and carries only supplied pagination filters', () => {
    testManagementService.getSuiteTrend('Checkout / card', 'project-1', 14)
    testManagementService.getSuiteCases('Checkout / card', 'project-1', { page: 2 })
    testManagementService.getSuiteMembership('Checkout / card', null, 'active')
    testManagementService.getSuiteChanges('Checkout / card', 'project-1', 'run-1')
    testManagementService.getSuiteDeleted('Checkout / card', null)

    const suite = 'Checkout%20%2F%20card'
    expect(getData).toHaveBeenNthCalledWith(1, `/api/v1/test-management/suites/${suite}/trend`, {
      params: { project_id: 'project-1', days: 14 },
    })
    expect(getData).toHaveBeenNthCalledWith(2, `/api/v1/test-management/suites/${suite}/cases`, {
      params: { project_id: 'project-1', page: 2 },
    })
    expect(getData).toHaveBeenNthCalledWith(3, `/api/v1/test-management/suites/${suite}/membership`, {
      params: { status: 'active' },
    })
    expect(getData).toHaveBeenNthCalledWith(4, `/api/v1/test-management/suites/${suite}/changes`, {
      params: { project_id: 'project-1', run_id: 'run-1' },
    })
    expect(getData).toHaveBeenNthCalledWith(5, `/api/v1/test-management/suites/${suite}/deleted`, {
      params: {},
    })
  })

  it('uses exact suite owner and review contracts', () => {
    testManagementService.listSuiteOwners('project-1')
    testManagementService.resolveSuiteOwner('Checkout / card', 'project-1')
    testManagementService.setSuiteOwner('Checkout / card', 'project-1', null)
    testManagementService.listReviewsForRun('run-1')
    testManagementService.listSuiteReviews('project-1', { state: 'pending' })
    testManagementService.upsertSuiteReview('run-1', 'Checkout / card', 'confirmed', 'verified')

    expect(getData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/suite-owners', {
      params: { project_id: 'project-1' },
    })
    expect(getData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/suite-owners', {
      params: { project_id: 'project-1', suite_name: 'Checkout / card' },
    })
    expect(putData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/suite-owners/Checkout%20%2F%20card', {
      owner_user_id: null,
    }, { params: { project_id: 'project-1' } })
    expect(getData).toHaveBeenNthCalledWith(3, '/api/v1/test-management/suite-reviews/by-run/run-1')
    expect(getData).toHaveBeenNthCalledWith(4, '/api/v1/test-management/suite-reviews', {
      params: { project_id: 'project-1', state: 'pending' },
    })
    expect(putData).toHaveBeenNthCalledWith(
      2,
      '/api/v1/test-management/suite-reviews/by-run/run-1/Checkout%20%2F%20card',
      { state: 'confirmed', note: 'verified' },
    )
  })

  it('uses project-scoped duplicate detection contracts', () => {
    testManagementService.getDuplicateCandidates('project-1', { band: 'strong', page: 2 })
    testManagementService.runDuplicateDetection('project-1', { enable_semantic: false })
    testManagementService.dismissDuplicate('project-1', 'candidate-1')
    testManagementService.mergeDuplicate('project-1', 'candidate-1', {
      candidate_id: 'candidate-1',
      keep_case_id: 'case-1',
    })

    expect(getData).toHaveBeenCalledWith('/api/v1/projects/project-1/duplicate-candidates', {
      params: { band: 'strong', page: 2 },
    })
    expect(postData).toHaveBeenNthCalledWith(
      1,
      '/api/v1/projects/project-1/duplicate-candidates/detect',
      undefined,
      { params: { enable_semantic: false } },
    )
    expect(postData).toHaveBeenNthCalledWith(2, '/api/v1/projects/project-1/duplicate-candidates/candidate-1/dismiss')
    expect(postData).toHaveBeenNthCalledWith(3, '/api/v1/projects/project-1/duplicate-candidates/candidate-1/merge', {
      candidate_id: 'candidate-1',
      keep_case_id: 'case-1',
    })
  })

  it('lists users through the auth endpoint', () => {
    usersService.listUsers()
    expect(getData).toHaveBeenCalledWith('/api/v1/auth/users')
  })
})

describe('testManagementService downloads', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:test')
    vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => undefined)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  })

  afterEach(() => vi.restoreAllMocks())

  it('decodes RFC 5987 filenames and always releases the object URL', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: new Blob(['document']),
      headers: { 'content-disposition': "attachment; filename*=UTF-8''release%20plan.docx" },
    })

    await testManagementService.downloadPlanWord('plan-1')

    expect(api.get).toHaveBeenCalledWith('/api/v1/test-management/plans/plan-1/export/word', {
      responseType: 'blob',
    })
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
    expect(window.URL.revokeObjectURL).toHaveBeenCalledWith('blob:test')
  })

  it.each([
    ['downloadPlanPdf', 'plan-1', '/api/v1/test-management/plans/plan-1/export/pdf', 'test-plan-plan-1.pdf'],
    ['downloadStrategyWord', 'strategy-1', '/api/v1/test-management/strategies/strategy-1/export/word', 'test-strategy-strategy-1.docx'],
    ['downloadStrategyPdf', 'strategy-1', '/api/v1/test-management/strategies/strategy-1/export/pdf', 'test-strategy-strategy-1.pdf'],
  ] as const)('uses fallback filename for %s when the header is absent', async (method, id, url, fallback) => {
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['document']), headers: {} })
    const created: HTMLAnchorElement[] = []
    const realCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tagName) => {
      const element = realCreateElement(tagName)
      if (tagName === 'a') created.push(element as HTMLAnchorElement)
      return element
    })

    await testManagementService[method](id)

    expect(api.get).toHaveBeenCalledWith(url, { responseType: 'blob' })
    expect(created).toHaveLength(1)
    expect(created[0]?.download).toBe(fallback)
    expect(created[0]?.isConnected).toBe(false)
  })
})
