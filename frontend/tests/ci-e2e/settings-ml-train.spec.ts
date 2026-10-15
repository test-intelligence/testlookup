/**
 * W04 AIC-05 on `/settings/ai`, hermetic (`docs/deferred-scope-2026-10-13/work/W04-contract.md` §2.9, §3.2,
 * §4.2): the ML card.
 *
 *  - below the 200-sample floor: "Samples 142 / 200 · human labels 12 · no model yet", "Train now" disabled with
 *    the reason beside it, and nothing posted;
 *  - enough samples: Train now -> one `POST /settings/ai/ml/retrain` -> "Training…" -> the status polls (every
 *    3 s while a job runs) walk queued -> running -> "Model <version> · accuracy 0.84 · trained just now", and the
 *    ML radio badge agrees ("Ready (84% accuracy)");
 *  - a QA lead (not ADMIN) sees the sample line and no button.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-ai-config.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { USER } from '../visual/production/fixtures'
import {
  ADMIN,
  aiConfigHandlers,
  ML_RETRAIN_PATH,
  ML_STATUS_PATH,
  MODEL_VERSION,
} from '../visual/production/fixtures-ai-config'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const card = (page: Page) => page.locator('[data-ml-training-card]')
const trainNow = (page: Page) => card(page).locator('[data-ml-train-now]')

async function open(page: Page, samples: number, user = ADMIN) {
  const fixture = aiConfigHandlers({ samples })
  const opened = await openRollout(page, '/settings/ai', {
    handlers: fixture.handlers,
    user,
    ready: (p) => p.locator('[data-ml-training-card] [data-ml-samples]'),
  })
  return { ...opened, fixture }
}

test('below the floor: the button is disabled and says why; nothing is posted', async ({ page }) => {
  const { api, errors, fixture } = await open(page, 142)

  await expect(card(page).locator('[data-ml-samples]')).toHaveText('Samples 142 / 200 · human labels 12 · no model yet')
  await expect(trainNow(page)).toHaveText('Train now')
  await expect(trainNow(page)).toBeDisabled()
  await expect(trainNow(page)).toHaveAttribute('title', 'Need 200 labelled samples, have 142.')
  await expect(card(page).locator('[data-ml-cannot-train]')).toHaveText('Need 200 labelled samples, have 142.')
  await expect(card(page).locator('[data-ml-model-line]')).toHaveCount(0)
  await expect(card(page).locator('[data-ml-schedule]')).toHaveText(
    'A model is trained nightly at 03:10 UTC once enough labelled samples exist, or on demand.',
  )
  // The old banner's hard-coded "/ 200 labeled samples" is gone.
  await expect(page.getByText(/labeled samples\./)).toHaveCount(0)

  await networkQuiet(page, api)
  expect(fixture.state.writes).toEqual([])
  expect(requestsTo(api, ML_RETRAIN_PATH)).toEqual([])
  assertHermetic(api, errors)
})

test('Train now: one POST, "Training…", and the polls turn into the new model line', async ({ page }) => {
  const { api, errors, fixture } = await open(page, 642)

  await expect(card(page).locator('[data-ml-samples]')).toHaveText('Samples 642 / 200 · human labels 57 · no model yet')
  await expect(card(page).locator('[data-ml-cannot-train]')).toHaveCount(0)
  await expect(trainNow(page)).toBeEnabled()
  await trainNow(page).click()

  await expect(trainNow(page)).toHaveText('Training…')
  await expect(trainNow(page)).toBeDisabled()
  await expect(card(page)).toHaveAttribute('data-ml-training-state', 'queued')

  // queued -> running -> trained, one step per 3 s poll. The page clock is pinned (`freezeClock`), so the
  // spec moves its timers: 3.1 s per step proves the card polls at 3 s while a job runs, not at 60.
  const modelLine = card(page).locator('[data-ml-model-line]')
  await page.clock.runFor(3_100)
  await expect(card(page)).toHaveAttribute('data-ml-training-state', 'running')
  await expect(trainNow(page)).toHaveText('Training…')
  await expect(modelLine).toHaveCount(0)
  await page.clock.runFor(3_100)
  await expect(modelLine).toHaveText(`Model ${MODEL_VERSION} · accuracy 0.84 · trained just now`)
  await expect(card(page)).toHaveAttribute('data-ml-training-state', 'idle')
  await expect(trainNow(page)).toHaveText('Train now')
  await expect(card(page).locator('[data-ml-samples]')).toHaveText(
    `Samples 642 / 200 · human labels 57 · model ${MODEL_VERSION}`,
  )
  await expect(card(page).locator('[data-ml-last-run]')).toHaveCount(0)
  // The ML radio's badge follows the card, not the page-load snapshot.
  await expect(page.getByText('Ready (84% accuracy)')).toBeVisible()

  expect(fixture.state.writes.filter((w) => w.line === `POST ${ML_RETRAIN_PATH}`)).toHaveLength(1)
  expect(requestsTo(api, ML_STATUS_PATH).length).toBeGreaterThanOrEqual(4) // load + queued + running + trained
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a QA lead sees the samples and no Train now', async ({ page }) => {
  const { api, errors } = await open(page, 642, USER)

  await expect(card(page).locator('[data-ml-samples]')).toHaveText('Samples 642 / 200 · human labels 57 · no model yet')
  await expect(trainNow(page)).toHaveCount(0)
  await expect(card(page).locator('[data-ml-schedule]')).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
