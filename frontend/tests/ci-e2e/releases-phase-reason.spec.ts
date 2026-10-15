/**
 * W05 REL-10, hermetic (`docs/deferred-scope-2026-10-13/work/W05-contract.md` §2.3), on a release's own page
 * with phase-gate enforcement on (the fixture answers as `release_service._enforce_phase_gate` does):
 *
 *  - Completed on a phase whose live gate is NO_GO: the PUT answers 409; the page opens the reason dialog
 *    quoting the server's sentence (no toast repeats it); Submit stays disabled under 3 characters; with a
 *    reason the page resends `{ status, gate_override_reason }` (the exact field name), the dialog closes and
 *    the phase reads Completed;
 *  - Skipped: the 409 opens the skip dialog; Cancel sends nothing more and leaves the phase as it was; a
 *    reason resends `{ status, skip_reason }`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-releases.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { PHASE_GATE_REFUSAL, PHASE_SKIP_REFUSAL, RELEASE, releasesHandlers } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { name: 'Release Phases' })
const status = (page: Page, phase: string) => page.getByLabel(`Status of phase ${phase}`)
const PHASE_PUT = (phaseId: string) => `PUT /api/v1/releases/${RELEASE.active}/phases/${phaseId}`

test('Completed over a No-Go phase gate: the dialog quotes the server, the reason is sent as gate_override_reason', async ({ page }) => {
  const { handlers, writes } = releasesHandlers()
  const { api, errors } = await openRollout(page, `/releases/${RELEASE.active}`, { handlers, ready })

  await status(page, 'Payments soak').selectOption('completed')

  const dialog = page.locator('[data-phase-reason-dialog="complete"]')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('heading', { name: 'Complete "Payments soak" anyway?' })).toBeVisible()
  await expect(dialog.locator('[data-phase-gate-detail]')).toHaveText(PHASE_GATE_REFUSAL)
  // The sentence is the dialog's, once: the first attempt was quiet (no global toast).
  await expect(page.getByText(PHASE_GATE_REFUSAL)).toHaveCount(1)
  await expect(page.getByText('Failed to update phase')).toHaveCount(0)

  const submit = dialog.getByRole('button', { name: 'Complete with override' })
  const reason = dialog.getByLabel('Reason (recorded in the audit log)')
  await expect(submit).toBeDisabled()
  await reason.fill('  ok  ')
  await expect(submit, 'two characters once trimmed').toBeDisabled()
  await reason.fill('  Customer waiver CAB-142 ')
  await expect(submit).toBeEnabled()
  await submit.click()

  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('Phase "Payments soak" completed with an override')).toBeVisible()
  await expect(status(page, 'Payments soak')).toHaveValue('completed')
  await networkQuiet(page, api)
  expect(writes).toEqual([
    { line: PHASE_PUT('ph-soak'), body: { status: 'completed' } },
    { line: PHASE_PUT('ph-soak'), body: { status: 'completed', gate_override_reason: 'Customer waiver CAB-142' } },
  ])
  assertHermetic(api, errors)
})

test('Skipped: Cancel leaves the phase as it was with one request; a reason is sent as skip_reason', async ({ page }) => {
  const { handlers, writes } = releasesHandlers()
  const { api, errors } = await openRollout(page, `/releases/${RELEASE.active}`, { handlers, ready })

  await status(page, 'Customer acceptance').selectOption('skipped')
  const dialog = page.locator('[data-phase-reason-dialog="skip"]')
  await expect(dialog.getByRole('heading', { name: 'Skip "Customer acceptance"?' })).toBeVisible()
  await expect(dialog.locator('[data-phase-gate-detail]')).toHaveText(PHASE_SKIP_REFUSAL)
  await dialog.getByRole('button', { name: 'Cancel' }).click()

  await expect(dialog).toHaveCount(0)
  await expect(status(page, 'Customer acceptance')).toHaveValue('in_progress')
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: PHASE_PUT('ph-uat'), body: { status: 'skipped' } }])

  // Again, with a reason this time.
  await status(page, 'Customer acceptance').selectOption('skipped')
  await dialog.getByLabel('Reason (recorded in the audit log)').fill('Covered by the payments soak')
  await dialog.getByRole('button', { name: 'Skip phase' }).click()
  await expect(page.getByText('Phase "Customer acceptance" skipped')).toBeVisible()
  await expect(status(page, 'Customer acceptance')).toHaveValue('skipped')
  await networkQuiet(page, api)
  expect(writes.slice(1)).toEqual([
    { line: PHASE_PUT('ph-uat'), body: { status: 'skipped' } },
    { line: PHASE_PUT('ph-uat'), body: { status: 'skipped', skip_reason: 'Covered by the payments soak' } },
  ])
  assertHermetic(api, errors)
})
