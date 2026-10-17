/**
 * NTF-03 / NTF-04: the digests page's pure scope model — release ordering,
 * the row summary and its problem texts (with the enrichment guard), the
 * listed-view rule, the view row's texts, the editor's draft rules and the
 * preview's scope lines.
 */
import { describe, expect, it } from 'vitest'
import type { DigestScopeInfo, DigestSubscription } from '@/services/digestService'
import type { Release } from '@/types/releases'
import {
  FILTERED_NOTE,
  MAX_VIEW_SUITES,
  draftFromView,
  droppedNote,
  hasErrors,
  isFilteredScope,
  isListedView,
  parseSubscriptionScope,
  parseSuites,
  pickableViews,
  problemKind,
  releaseOptions,
  scheduleLabel,
  scopeHeadline,
  scopeProblemText,
  subscriptionSummary,
  validateDraft,
  viewAppliesTo,
  viewPageText,
  viewPayload,
  viewScopeText,
} from './digestScopeModel'

const rel = (id: string, name: string, over: Partial<Release> = {}) =>
  ({ id, name, project_id: 'p', status: 'released', is_active: false, ...over }) as Release

function sub(over: Partial<DigestSubscription> & Record<string, unknown> = {}): DigestSubscription {
  return {
    id: 's', user_id: 'u', project_id: 'p', saved_view_id: null, name: 'n', schedule: 'PER_RELEASE', channel: 'email',
    is_active: true, is_paused: false, scope_type: 'release', scope_value: 'r1', trigger_filter: null,
    send_when_unchanged: true, report_attachment: false, last_delivered_at: null, next_delivery_at: null,
    delivery_count: 2, created_at: '2026-10-01T00:00:00Z', updated_at: null,
    ...over,
  } as DigestSubscription
}

describe('releaseOptions', () => {
  it('the active release, then in-progress, then the rest, each in the list order', () => {
    const list = [
      rel('a', 'A'),
      rel('b', 'B', { status: 'in_progress' }),
      rel('c', 'C'),
      rel('d', 'D', { status: 'in_progress', is_active: true }),
      rel('e', 'E', { status: 'in_progress' }),
    ]
    expect(releaseOptions(list).map(r => r.id)).toEqual(['d', 'b', 'e', 'a', 'c'])
  })

  it.each([
    ['undefined', undefined],
    ['null', null],
    ['an object', { items: [] }],
  ])('%s → []', (_n, raw) => {
    expect(releaseOptions(raw as never)).toEqual([])
  })

  it('drops entries without an id and a name', () => {
    expect(releaseOptions([null, 3, { id: 'x' }, { name: 'y' }, { id: '', name: 'z' }, rel('ok', 'OK')]).map(r => r.id)).toEqual(['ok'])
  })
})

describe('schedules', () => {
  it('labels and the view schedules', () => {
    expect(scheduleLabel('PER_RELEASE')).toBe('Per Release')
    expect(scheduleLabel('WEEKLY_RETRO')).toBe('Weekly Retro')
    expect(scheduleLabel('SOMETHING_NEW')).toBe('SOMETHING NEW')
    expect(scheduleLabel(undefined)).toBe('')
    expect(['DAILY', 'WEEKLY', 'WEEKLY_RETRO', 'PER_RUN', 'PER_RELEASE', 'PER_SUITE'].filter(viewAppliesTo)).toEqual(['DAILY', 'WEEKLY'])
  })
})

describe('parseSubscriptionScope (the enrichment guard)', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['an empty object', {}],
    ['wrong types', { scope_label: 7, scope_problem: { x: 1 }, saved_view_name: ['v'] }],
    ['blank strings', { scope_label: ' ', scope_problem: '', saved_view_name: '' }],
  ])('%s → every field null', (_n, raw) => {
    expect(parseSubscriptionScope(raw)).toEqual({ label: null, problem: null, viewName: null })
  })

  it('a valid answer', () => {
    expect(parseSubscriptionScope({ scope_label: '2026.10', scope_problem: 'unscoped', saved_view_name: 'Checkout' })).toEqual({
      label: '2026.10', problem: 'unscoped', viewName: 'Checkout',
    })
  })
})

describe('the problem texts', () => {
  it.each([
    ['unscoped', 'PER_RELEASE', 'Not delivering — choose a release', 'delivery'],
    ['unscoped', 'PER_SUITE', 'Not delivering — choose a suite', 'delivery'],
    ['unscoped', 'WEEKLY', null, null],
    ['release_missing', 'PER_RELEASE', 'Not delivering — its release no longer exists', 'delivery'],
    ['saved_view_deleted', 'WEEKLY', 'View deleted — this digest covers the whole project', 'view'],
    ['saved_view_unavailable', 'DAILY', 'View no longer available to you — the whole project', 'view'],
    ['something_new', 'WEEKLY', null, null],
    [null, 'WEEKLY', null, null],
  ])('%s on %s → %j (%s)', (problem, schedule, text, kind) => {
    expect(scopeProblemText(problem, schedule)).toBe(text)
    expect(problemKind(problem, schedule)).toBe(kind)
  })
})

describe('subscriptionSummary', () => {
  it('Per Release · its release name via the channel; never the raw id', () => {
    const line = subscriptionSummary(sub({ scope_label: '2026.10' }))
    expect(line).toBe('Per Release · 2026.10 via email · Delivered: 2 times')
    expect(subscriptionSummary(sub({ scope_label: null }))).toBe('Per Release via email · Delivered: 2 times')
  })

  it('Per Suite falls back to its stored suite; a legacy scope value still reads Scope:', () => {
    expect(subscriptionSummary(sub({ schedule: 'PER_SUITE', scope_type: 'suite', scope_value: 'smoke', scope_label: null }))).toBe(
      'Per Suite · smoke via email · Delivered: 2 times',
    )
    expect(subscriptionSummary(sub({ schedule: 'WEEKLY', scope_type: 'project', scope_value: 'legacy' }))).toBe(
      'Weekly · Scope: legacy via email · Delivered: 2 times',
    )
  })

  it('the view name, only while the view is bound and has no problem; trigger and report as before', () => {
    const base = { schedule: 'WEEKLY' as const, scope_type: 'project' as const, scope_value: null }
    expect(subscriptionSummary(sub({ ...base, saved_view_id: 'v1', saved_view_name: 'Checkout', report_attachment: true }))).toBe(
      'Weekly via email · View: Checkout · Report attached · Delivered: 2 times',
    )
    expect(subscriptionSummary(sub({ ...base, saved_view_id: 'v1', saved_view_name: 'Checkout', scope_problem: 'saved_view_unavailable' }))).toBe(
      'Weekly via email · Delivered: 2 times',
    )
    expect(subscriptionSummary(sub({ ...base, saved_view_id: null, saved_view_name: 'Gone' }))).toBe('Weekly via email · Delivered: 2 times')
    expect(subscriptionSummary(sub({ schedule: 'PER_RUN', scope_type: null, scope_value: null, trigger_filter: 'failed_only' }))).toBe(
      'Per Run via email · failed only · Delivered: 2 times',
    )
  })
})

describe('the listed views', () => {
  it.each([
    ['a report view', { kind: 'report_view', page: 'trends', window: 7 }, true],
    ['a legacy empty row', {}, true],
    ['a widget layout', { page: 'trends', instances: [], version: 2 }, false],
    ['other filters without a kind', { status: 'failed' }, false],
  ])('%s → %s', (_n, filters, listed) => {
    expect(isListedView({ filters })).toBe(listed)
  })

  it('pickableViews: report views of the project only', () => {
    const rv = { kind: 'report_view' }
    const list = [
      { id: 'a', project_id: 'p', filters: rv },
      { id: 'b', project_id: null, filters: rv },
      { id: 'c', project_id: 'q', filters: rv },
      { id: 'd', project_id: 'p', filters: {} },
      { id: 'e', project_id: 'p', filters: { page: 'trends', instances: [] } },
    ]
    expect(pickableViews(list, 'p').map(v => v.id)).toEqual(['a'])
    expect(pickableViews(list, null)).toEqual([])
    expect(pickableViews(list, undefined)).toEqual([])
  })

  it('page and scope texts', () => {
    expect(viewPageText({ page: 'trends' })).toBe('Saved on Trends')
    expect(viewPageText({ page: 'summary_report' })).toBe('Saved on Summary')
    expect(viewPageText({ page: null })).toBeNull()
    const releases = [rel('r1', '2026.10'), rel('r2', '2026.11')]
    expect(viewScopeText({ filters: { kind: 'report_view', release_ids: ['r1'], suites: ['checkout'], window: 7 } }, releases)).toBe(
      'Releases: 2026.10 · Suites: checkout · Last 7 days',
    )
    expect(viewScopeText({ filters: { kind: 'report_view', window: 1 } }, releases)).toBe('Any release · Any suite · Last 1 day')
    expect(viewScopeText({ filters: { kind: 'report_view', release_ids: ['r1', 'gone'], window: 30 } }, releases)).toBe(
      'Releases: 2026.10, plus 1 not in this project · Any suite · Last 30 days',
    )
    expect(viewScopeText({ filters: { kind: 'report_view', release_ids: ['gone', 'gone2'] } }, releases)).toBe(
      'Releases: 2 not in this project · Any suite',
    )
    expect(viewScopeText({ filters: {} }, releases)).toBe('No filters — edit to add some')
    expect(viewScopeText({ filters: { kind: 'report_view', release_ids: 'r1', suites: [3], window: 'x' } }, releases)).toBe(
      'Any release · Any suite',
    )
  })
})

describe('the editor draft', () => {
  it('parseSuites: trimmed, empties dropped, repeats once', () => {
    expect(parseSuites(' checkout , ,payments,checkout ')).toEqual(['checkout', 'payments'])
    expect(parseSuites('')).toEqual([])
  })

  it('validateDraft: a name of 2-255 characters, at most 50 suites, a known window', () => {
    const ok = { name: 'Checkout', releaseIds: [], suitesText: 'a', windowDays: 7, isShared: false }
    expect(hasErrors(validateDraft(ok))).toBe(false)
    expect(validateDraft({ ...ok, name: ' C ' }).name).toBe('A name needs 2 to 255 characters.')
    expect(validateDraft({ ...ok, name: 'x'.repeat(256) }).name).toBe('A name needs 2 to 255 characters.')
    expect(validateDraft({ ...ok, name: 'x'.repeat(255) }).name).toBeUndefined()
    const fifty = Array.from({ length: MAX_VIEW_SUITES }, (_, i) => `s${i}`).join(',')
    expect(validateDraft({ ...ok, suitesText: fifty }).suites).toBeUndefined()
    expect(validateDraft({ ...ok, suitesText: `${fifty},one-more` }).suites).toBe('At most 50 suites.')
    expect(validateDraft({ ...ok, windowDays: 3 }).window).toBeTruthy()
  })

  it('viewPayload: the report-view shape, one release also under release_id', () => {
    expect(viewPayload({ name: ' Checkout ', releaseIds: ['r1'], suitesText: 'checkout', windowDays: 7, isShared: true })).toEqual({
      name: 'Checkout', page: 'trends', is_shared: true,
      filters: { kind: 'report_view', page: 'trends', window: 7, release_ids: ['r1'], release_id: 'r1', suites: ['checkout'] },
    })
    expect(viewPayload({ name: 'Two', releaseIds: ['r1', 'r2'], suitesText: '', windowDays: 14, isShared: false }, 'failures').filters).toEqual({
      kind: 'report_view', page: 'failures', window: 14, release_ids: ['r1', 'r2'],
    })
  })

  it('draftFromView: a report view reads back (releases the project still lists); a legacy row opens empty', () => {
    const releases = [rel('r1', '2026.10')]
    expect(
      draftFromView({ name: 'V', is_shared: true, filters: { kind: 'report_view', release_ids: ['r1', 'gone'], suites: ['a', 'b'], window: 30 } }, releases),
    ).toEqual({ name: 'V', releaseIds: ['r1'], suitesText: 'a, b', windowDays: 30, isShared: true })
    expect(draftFromView({ name: 'Old', is_shared: false, filters: {} }, releases)).toEqual({
      name: 'Old', releaseIds: [], suitesText: '', windowDays: 7, isShared: false,
    })
    expect(draftFromView({ name: 'W', is_shared: false, filters: { kind: 'report_view', window: 3 } }, releases).windowDays).toBe(7)
  })
})

describe('the preview scope lines', () => {
  const scope = (over: Partial<DigestScopeInfo> = {}): DigestScopeInfo => ({
    source: 'saved_view', view_id: 'v', view_name: 'Checkout regressions', release_ids: ['r1'], release_names: ['2026.10'],
    suites: ['checkout'], window_days: 7, dropped_release_count: 0, ...over,
  })

  it('the headline per source', () => {
    expect(scopeHeadline(scope())).toBe('Filtered by view: Checkout regressions — release 2026.10 · suite checkout · last 7 days')
    expect(scopeHeadline(scope({ release_names: ['a', 'b'], release_ids: ['1', '2'], suites: [], window_days: 1 }))).toBe(
      'Filtered by view: Checkout regressions — releases a, b · last 1 day',
    )
    expect(scopeHeadline(scope({ release_names: [], release_ids: [], suites: [], window_days: null }))).toBe('Filtered by view: Checkout regressions')
    expect(scopeHeadline(scope({ source: 'saved_view_deleted' }))).toBe('View deleted — this digest covers the whole project')
    expect(scopeHeadline(scope({ source: 'saved_view_unavailable' }))).toBe('View no longer available to you — the whole project')
    expect(scopeHeadline(scope({ source: 'project' }))).toBeNull()
    expect(scopeHeadline(scope({ source: 'unknown' }))).toBeNull()
  })

  it('the notes', () => {
    expect(droppedNote(2)).toBe('2 releases in this view no longer exist in this project and were left out.')
    expect(droppedNote(1)).toBe('1 release in this view no longer exists in this project and was left out.')
    expect(droppedNote(0)).toBeNull()
    expect(isFilteredScope(scope())).toBe(true)
    expect(isFilteredScope(scope({ release_ids: [], suites: [], window_days: null }))).toBe(false)
    expect(isFilteredScope(scope({ source: 'saved_view_deleted' }))).toBe(false)
    expect(FILTERED_NOTE).toBe('Project-wide sections (flaky debt by team, hours saved) are left out of a filtered digest.')
  })
})
