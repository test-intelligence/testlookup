/**
 * W04 AIC-01 / AIC-02 on `/settings/ai`, hermetic (`docs/deferred-scope-2026-10-13/work/W04-contract.md` §2.2,
 * §2.4, §4.2): the runtime now obeys what this page stores, and the page says so.
 *
 *  - nothing stored: every value reads "Server default", each with when it applies;
 *  - untick Deep Investigation + Save: the PUT carries `deep_investigation_enabled: false`, the hint reads
 *    "Set here", and the mode every role reads (`/settings/ai/mode`, what the deep CTAs follow) is re-read and
 *    answers the toggle off;
 *  - Timeout 5 s (the floor was 30) + Save: the PUT carries 5; the hint says it applies to every LLM call;
 *  - STRAT-10 / D37 (W34): no fine-tuning control on the page (an operator sets it with the admin API,
 *    docs/operations/fine-tuning.md); the other toggles still save, and the PUT carries `finetune_enabled` exactly
 *    as GET answered it — a stored true and a stored false both survive the save.
 *
 * The CTAs themselves are G-E's spec (`deep-cta-follows-toggle.spec.ts`). Harness: `tests/lib/rollout.ts` (fail
 * closed, pinned clock). Fixtures: `fixtures-ai-config.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { ADMIN, AI_CONFIG_PATH, AI_MODE_PATH, aiConfigHandlers } from '../visual/production/fixtures-ai-config'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const hint = (page: Page, key: string) => page.locator(`[data-config-source-key="${key}"]`)

async function open(page: Page, fixture = aiConfigHandlers()) {
  const opened = await openRollout(page, '/settings/ai', {
    handlers: fixture.handlers,
    user: ADMIN,
    ready: (p) => p.getByRole('heading', { level: 1, name: 'AI Configuration' }),
  })
  await expect(page.getByRole('checkbox', { name: 'Deep Investigation' })).toBeVisible()
  return { ...opened, fixture }
}

async function save(page: Page) {
  await page.getByRole('button', { name: 'Save Configuration' }).click()
  await expect(page.getByText('AI configuration saved')).toBeVisible()
}

test('nothing stored: every value says "Server default" and when it applies', async ({ page }) => {
  const { api, errors } = await open(page)

  await expect(hint(page, 'deep_investigation_enabled')).toHaveText('Server default · Applies within a minute.')
  // STRAT-10: fine-tuning has no control here, so no hint either.
  await expect(hint(page, 'finetune_enabled')).toHaveCount(0)
  await expect(hint(page, 'ai_confidence_threshold')).toHaveText('Server default · Applies to new pipelines.')
  await expect(page.getByTestId('timeout-hint')).toHaveText(
    'Server default · Applies to every LLM call; an agent may set a lower limit. Applies to new pipelines; a running pipeline keeps its value.',
  )
  await expect(hint(page, 'analysis_mode')).toHaveText('Server default')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('untick Deep Investigation + Save: the PUT carries false, the hint reads "Set here", the mode follows', async ({ page }) => {
  const { api, errors, fixture } = await open(page)
  const deep = page.getByRole('checkbox', { name: 'Deep Investigation' })
  await expect(deep).toBeChecked()
  await networkQuiet(page, api)
  const modeReadsBefore = requestsTo(api, AI_MODE_PATH).length

  await deep.uncheck()
  await save(page)

  const puts = fixture.state.writes.filter((w) => w.line === `PUT ${AI_CONFIG_PATH}`)
  expect(puts).toHaveLength(1)
  expect(puts[0].body).toMatchObject({ deep_investigation_enabled: false })
  await expect(hint(page, 'deep_investigation_enabled')).toHaveText('Set here · Applies within a minute.')
  await expect(hint(page, 'deep_investigation_enabled')).toHaveAttribute('data-source', 'ai_config')
  await expect(deep).not.toBeChecked()

  // The read every role makes (the sidebar, the deep CTAs) is refreshed and now says "off".
  await networkQuiet(page, api)
  expect(requestsTo(api, AI_MODE_PATH).length).toBeGreaterThan(modeReadsBefore)
  const mode = await page.evaluate(async (path) => (await fetch(path)).json(), AI_MODE_PATH)
  expect(mode).toMatchObject({ deep_investigation_enabled: false })
  assertHermetic(api, errors)
})

test('Timeout 5 s is accepted (the floor was 30), and the hint says it applies to every LLM call', async ({ page }) => {
  const { api, errors, fixture } = await open(page)
  const timeout = page.getByRole('spinbutton', { name: 'Timeout (seconds)' })
  await expect(timeout).toHaveAttribute('min', '5')
  await expect(timeout).toHaveAttribute('max', '1800')

  await timeout.fill('5')
  // The browser's own constraint validation passes at the floor (it refused 5 at min=30).
  expect(await timeout.evaluate((el) => (el as HTMLInputElement).checkValidity())).toBe(true)
  await save(page)

  const puts = fixture.state.writes.filter((w) => w.line === `PUT ${AI_CONFIG_PATH}`)
  expect(puts).toHaveLength(1)
  expect(puts[0].body).toMatchObject({ ai_timeout_seconds: 5 })
  await expect(timeout).toHaveValue('5')
  await expect(page.getByTestId('timeout-hint')).toHaveText(
    'Set here · Applies to every LLM call; an agent may set a lower limit. Applies to new pipelines; a running pipeline keeps its value.',
  )
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

for (const stored of [true, false]) {
  test(`no fine-tuning control; the other toggles save and a stored finetune_enabled=${stored} survives`, async ({ page }) => {
    const { api, errors, fixture } = await open(
      page,
      aiConfigHandlers({ config: { finetune_enabled: stored, finetune_enabled_source: 'ai_config' } }),
    )
    // An operator's stored value is not shown in any form: no checkbox, no hint, no copy.
    await expect(page.getByRole('checkbox', { name: /fine[- ]?tun/i })).toHaveCount(0)
    await expect(hint(page, 'finetune_enabled')).toHaveCount(0)
    await expect(page.locator('body')).not.toContainText(/fine[- ]?tun/i)

    const offline = page.getByRole('checkbox', { name: /Offline Mode/ })
    const deep = page.getByRole('checkbox', { name: 'Deep Investigation' })
    await expect(offline).toBeChecked()
    await expect(deep).toBeChecked()
    await offline.uncheck()
    await deep.uncheck()
    await save(page)

    const puts = fixture.state.writes.filter((w) => w.line === `PUT ${AI_CONFIG_PATH}`)
    expect(puts).toHaveLength(1)
    expect(puts[0].body).toMatchObject({ ai_offline_mode: false, deep_investigation_enabled: false })
    expect(puts[0].body).toHaveProperty('finetune_enabled', stored)
    expect(fixture.state.config).toMatchObject({ finetune_enabled: stored, finetune_enabled_source: 'ai_config' })
    await expect(offline).not.toBeChecked()
    await expect(deep).not.toBeChecked()
    await expect(hint(page, 'deep_investigation_enabled')).toHaveText('Set here · Applies within a minute.')
    await expect(page.getByRole('checkbox', { name: /fine[- ]?tun/i })).toHaveCount(0)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
}
