/**
 * W23a TRND-03: the expected-cadence form, the pause form and their pure model (`cadenceFormModel`).
 *
 * The model is what goes on the wire, so its cases assert exact bodies: a weekly cadence sends no cron and
 * a cron one no weekdays / time; a PATCH sends only what changed (`clear_suite` for "any run"); the pause
 * `until` carries the browser's offset. The browser's refusals read as the server's own sentences.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { cadence, statusItem, missedWindow } from '@/test/insightCadenceMocks'
import { CADENCE_ERRORS } from '@/types/runCadence'
import { CadenceForm, PauseForm } from './CadenceForm'
import {
  GRACE_INVALID,
  SUITE_REQUIRED,
  emptyForm,
  fromCadence,
  localIsoWithOffset,
  pauseBody,
  stateLines,
  statusLine,
  timeZoneOptions,
  toCreateBody,
  toPatchBody,
  validate,
  validatePause,
  type CadenceFormState,
} from './cadenceFormModel'

function form(over: Partial<CadenceFormState> = {}): CadenceFormState {
  return { ...emptyForm('Europe/Berlin'), ...over }
}

function refusal(code: keyof typeof CADENCE_ERRORS, status = 409) {
  return Object.assign(new Error('Request failed'), {
    isAxiosError: true,
    response: { status, data: { detail: { code, message: CADENCE_ERRORS[code] } } },
  })
}

afterEach(() => vi.restoreAllMocks())

describe('cadenceFormModel: the bodies', () => {
  it('a weekly suite cadence sends days and time, no cron', () => {
    const body = toCreateBody(form({
      scope: 'suite', suiteName: '  nightly-regression ', weekdays: ['fri', 'mon', 'wed', 'tue', 'thu'], expectedBy: '06:00',
    }))
    expect(body).toEqual({
      suite_name: 'nightly-regression', mode: 'weekly', weekdays: ['mon', 'tue', 'wed', 'thu', 'fri'],
      expected_by: '06:00', timezone: 'Europe/Berlin', grace_minutes: 60,
    })
  })

  it('any run sends suite_name null; a cron cadence sends the normalised expression only', () => {
    expect(toCreateBody(form({ weekdays: ['sat'], expectedBy: '23:30' })).suite_name).toBeNull()
    expect(toCreateBody(form({ mode: 'cron', cron: ' 0  6 * *  1-5 ', grace: '15', weekdays: ['mon'] }))).toEqual({
      suite_name: null, mode: 'cron', cron: '0 6 * * 1-5', timezone: 'Europe/Berlin', grace_minutes: 15,
    })
  })

  it('a PATCH sends only what changed, clear_suite for any run, the new mode with its fields', () => {
    const saved = cadence({ suite_name: 'nightly-regression' })
    expect(toPatchBody(fromCadence(saved), saved)).toEqual({})
    expect(toPatchBody({ ...fromCadence(saved), grace: '30' }, saved)).toEqual({ grace_minutes: 30 })
    expect(toPatchBody({ ...fromCadence(saved), scope: 'any' }, saved)).toEqual({ clear_suite: true })
    expect(toPatchBody({ ...fromCadence(saved), suiteName: 'smoke' }, saved)).toEqual({ suite_name: 'smoke' })
    expect(toPatchBody({ ...fromCadence(saved), weekdays: ['mon'], timezone: 'UTC' }, saved)).toEqual({
      weekdays: ['mon'], timezone: 'UTC',
    })
    expect(toPatchBody({ ...fromCadence(saved), mode: 'cron', cron: '30 2 * * *' }, saved)).toEqual({
      mode: 'cron', cron: '30 2 * * *',
    })
    const cron = cadence({ mode: 'cron', cron: '30 2 * * *', weekdays: null, expected_by: null })
    expect(toPatchBody({ ...fromCadence(cron), mode: 'weekly', weekdays: ['tue'], expectedBy: '07:00' }, cron)).toEqual({
      mode: 'weekly', weekdays: ['tue'], expected_by: '07:00',
    })
  })
})

describe('cadenceFormModel: the refusals read as the server says them', () => {
  it('validate', () => {
    expect(validate(form({ scope: 'suite', suiteName: '  ', weekdays: ['mon'], expectedBy: '06:00' }))).toBe(SUITE_REQUIRED)
    expect(validate(form({ expectedBy: '06:00' }))).toBe(CADENCE_ERRORS.weekly_fields)
    expect(validate(form({ weekdays: ['mon'], expectedBy: '6:00' }))).toBe(CADENCE_ERRORS.weekly_fields)
    expect(validate(form({ mode: 'cron', cron: '   ' }))).toBe(CADENCE_ERRORS.cron_fields)
    expect(validate(form({ weekdays: ['mon'], expectedBy: '06:00', timezone: '' }))).toBe(CADENCE_ERRORS.unknown_timezone)
    expect(validate(form({ weekdays: ['mon'], expectedBy: '06:00', grace: '721' }))).toBe(GRACE_INVALID)
    expect(validate(form({ weekdays: ['mon'], expectedBy: '06:00', grace: '2.5' }))).toBe(GRACE_INVALID)
    expect(validate(form({ weekdays: ['mon'], expectedBy: '06:00' }))).toBeNull()
    expect(validate(form({ mode: 'cron', cron: '0 6 1 * *' }))).toBeNull()      // the server judges cron text
  })

  it('validatePause and pauseBody: the offset is the browser’s', () => {
    const now = new Date(2026, 9, 28, 12, 0)
    expect(validatePause('', 'freeze', now)).toBe('Pick when the pause ends.')
    expect(validatePause('2026-10-28T11:59', 'freeze', now)).toBe(CADENCE_ERRORS.pause_until_past)
    expect(validatePause('2027-01-27T12:01', 'freeze', now)).toBe(CADENCE_ERRORS.pause_too_long)
    expect(validatePause('2026-10-30T23:59', '   ', now)).toBe(CADENCE_ERRORS.pause_reason_required)
    expect(validatePause('2026-10-30T23:59', 'release freeze', now)).toBeNull()
    const body = pauseBody('2026-10-30T23:59', '  release freeze ')
    expect(body.reason).toBe('release freeze')
    expect(body.until).toMatch(/^2026-10-30T23:59:00[+-]\d\d:\d\d$/)
    expect(Date.parse(body.until)).toBe(new Date(2026, 9, 30, 23, 59).getTime())
    expect(localIsoWithOffset('30/10/2026')).toBeNull()
  })
})

describe('cadenceFormModel: the table lines', () => {
  it('stateLines: Active, or Paused until (the browser’s own local time, as it was picked), the reason and who', () => {
    expect(stateLines(cadence())).toEqual({ paused: false, label: 'Active', lines: [] })
    const until = new Date(2026, 9, 30, 23, 59).toISOString()
    const paused = cadence({
      paused: true, paused_until: until, paused_reason: 'Release freeze sentinel', paused_by_name: 'Alice Lead',
    })
    expect(stateLines(paused)).toEqual({
      paused: true, label: 'Paused', lines: ['until Fri Oct 30, 23:59', 'Release freeze sentinel', 'set by Alice Lead'],
    })
    expect(stateLines({ ...paused, paused_until: 'not a date', paused_by_name: null }).lines).toEqual(['Release freeze sentinel'])
  })

  it('statusLine: the last 14 days, never "all met" for nothing judged', () => {
    expect(statusLine(undefined)).toBe('No expected run in the last 14 days yet')
    expect(statusLine(statusItem({ measured: false, expected: 0, met: 0 }))).toBe('No expected run in the last 14 days yet')
    const item = statusItem({
      expected: 14, met: 12, missed: 2,
      missed_windows: [missedWindow(1, { local_label: 'Wed 28 Oct' }), missedWindow(2, { local_label: 'Tue 27 Oct' })],
    })
    expect(statusLine(item)).toBe('Last 14 days: 12 met · 2 missed (Tue, Wed)')
    expect(statusLine(statusItem({ met: 4, missed: 0, paused: 1 }))).toBe('Last 14 days: 4 met · 0 missed · 1 paused')
  })

  it('the zone list falls back to UTC and the browser’s zone, and keeps a saved zone', () => {
    const intl = Intl as unknown as { supportedValuesOf?: unknown }
    const original = intl.supportedValuesOf
    try {
      intl.supportedValuesOf = undefined
      const zones = timeZoneOptions()
      expect(zones[0]).toBe('UTC')
      expect(zones).toEqual(Array.from(new Set(['UTC', Intl.DateTimeFormat().resolvedOptions().timeZone])))
      expect(timeZoneOptions('Asia/Kolkata')).toContain('Asia/Kolkata')
    } finally {
      intl.supportedValuesOf = original
    }
  })
})

describe('CadenceForm', () => {
  it('Weekdays sets Mon–Fri; Save hands the form over; cron mode hides the day toggles', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<CadenceForm initial={form()} onSubmit={onSubmit} onCancel={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Weekdays' }))
    for (const day of ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']) {
      expect(screen.getByRole('button', { name: day })).toHaveAttribute('aria-pressed', 'true')
    }
    expect(screen.getByRole('button', { name: 'Sat' })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.change(screen.getByLabelText('Expected by'), { target: { value: '06:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(toCreateBody(onSubmit.mock.calls[0][0])).toEqual({
      suite_name: null, mode: 'weekly', weekdays: ['mon', 'tue', 'wed', 'thu', 'fri'], expected_by: '06:00',
      timezone: 'Europe/Berlin', grace_minutes: 60,
    })
    fireEvent.click(screen.getByLabelText('Cron (advanced)'))
    expect(screen.queryByRole('button', { name: 'Mon' })).toBeNull()
    expect(screen.queryByLabelText('Expected by')).toBeNull()
    expect(screen.getByLabelText('Cron expression')).toBeInTheDocument()
  })

  it('says the browser’s refusal and sends nothing; shows the API’s 409 sentence inline', async () => {
    const onSubmit = vi.fn().mockRejectedValueOnce(refusal('duplicate_scope'))
    render(<CadenceForm initial={form()} onSubmit={onSubmit} onCancel={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(CADENCE_ERRORS.weekly_fields)
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Every day' }))
    fireEvent.change(screen.getByLabelText('Expected by'), { target: { value: '02:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(CADENCE_ERRORS.duplicate_scope))
  })
})

describe('PauseForm', () => {
  it('a reason is required; then the local time and the reason are handed over', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<PauseForm onSubmit={onSubmit} onCancel={vi.fn()} now={() => new Date(2026, 9, 28, 12, 0)} />)
    fireEvent.change(screen.getByLabelText('Pause until'), { target: { value: '2026-10-30T23:59' } })
    fireEvent.click(screen.getByRole('button', { name: 'Pause expectations' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(CADENCE_ERRORS.pause_reason_required)
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'release freeze' } })
    fireEvent.click(screen.getByRole('button', { name: 'Pause expectations' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('2026-10-30T23:59', 'release freeze'))
  })
})
