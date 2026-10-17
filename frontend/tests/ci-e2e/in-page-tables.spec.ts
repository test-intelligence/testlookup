/**
 * W27a PAN-04 IN-PAGE SHAPE, the row rule (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.4, §4.1),
 * hermetic, on the CONVERTED tables at 1280 AND 1440: a row's form or detail opens in a full-width row RIGHT UNDER
 * that row (`[data-in-page-row]`, one cell spanning every column), never in an overlay, and never widens the table.
 *
 * For each form: open it under a row that is not the first, then: no overlay (`expectNoOverlay`); the form's row is
 * the row's next sibling and its cell spans the table's columns; the table is exactly as wide as with nothing open
 * (± 1 px); `#main-content` does not scroll sideways; no table is wider than the box that clips it; the form stays
 * inside the table's box; and one `INPAGE <route> <form> @<w> {"table","box","slack"}` line is printed (slack: how
 * much narrower the table or the form could get before its content overflows; < 40 px at 1280 is a finding for the
 * page's group).
 *
 *  - `/failures`: Mute, Jira, Suspects (6 columns);
 *  - `/defects`: Change status, Assign, Link a Jira issue (8);
 *  - `/flaky` Detected: the details, Propose quarantine (7); the details ride `?test=<fingerprint>`: a refresh
 *    reopens them, Back closes them, an unknown fingerprint is dropped, a malformed one rejected;
 *  - `/flaky` Quarantined: File Jira (8);
 *  - `/my-failures`: Reassign, Update triage status (7).
 *
 * The flows themselves are the `characterize-*.spec.ts` specs. Harness: `tests/lib/rollout.ts` (fail closed, pinned
 * clock). Fixtures: `fixtures-in-page.ts` (`failuresInPageWorld`, `flakyJiraWorld`, `inboxInPageWorld`),
 * `defectsWorld`, `flakyHandlers`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { boxOf, clippedTables, expectMainFits, inPageLine, minContentSlack, ownOverflow, tableBox } from '../lib/inPageGeometry'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { defectsWorld, QA_ENGINEER } from '../visual/production/fixtures-defects'
import { failuresInPageWorld, flakyJiraWorld, inboxInPageWorld, LONG_PREVIEW_LINE } from '../visual/production/fixtures-in-page'
import { QUARANTINE_IDS } from '../visual/production/fixtures-notify-flaky'
import { flakyHandlers } from '../visual/production/fixtures-pages'

const QUARANTINE_ON = { flaky_auto_quarantine: true }
const headerReady = (p: Page) => p.locator('[data-page-header] h1')
/** A table's own rows (never a form row under one). */
const ownRows = (table: Locator) => table.locator('tbody > tr:not([data-in-page-row])')

interface RowForm {
  /** What the INPAGE line calls it. */
  name: string
  /** Open the form from the row. */
  open: (row: Locator) => Promise<void>
  /** The opened container. */
  container: (page: Page) => Locator
  /** Close it again. */
  close: (container: Locator) => Promise<void>
  /** What the form shows once its own reads have landed (measured only then). */
  loaded: (container: Locator) => Promise<void>
}

/** Open `form` under `row` of `table` and check the row rule; returns the INPAGE numbers. */
async function expectUnderRow(page: Page, route: string, width: number, table: Locator, row: Locator, columns: number, form: RowForm) {
  const closed = await tableBox(table)
  await form.open(row)
  const container = form.container(page)
  await expect(container, `${route} ${form.name}: opened`).toBeVisible()
  await form.loaded(container)
  await expectNoOverlay(page)

  // The form's row is the next sibling of its row, one cell spanning every column.
  const next = row.locator('xpath=following-sibling::tr[1]')
  await expect(next, `${route} ${form.name}: the next row is the in-page row`).toHaveAttribute('data-in-page-row', '')
  await expect(next.locator(':scope > td'), `${route} ${form.name}: one cell`).toHaveCount(1)
  await expect(next.locator(':scope > td'), `${route} ${form.name}: spanning every column`).toHaveAttribute('colspan', String(columns))
  await expect(next.locator(':scope > td').locator(container.first()), `${route} ${form.name}: the form is in that row`).toHaveCount(1)

  // Nothing grows: the table keeps its width, the scroller does not scroll sideways, nothing is clipped.
  const open = await tableBox(table)
  expect(Math.abs(open.table - closed.table), `${route} ${form.name} @${width}: the table keeps its width`).toBeLessThanOrEqual(1)
  await expectMainFits(page, `${route} ${form.name} @${width}`)
  expect(await clippedTables(page.locator('[data-primary]')), `${route} ${form.name} @${width}: a clipped table`).toEqual([])
  const [formBox, tableEdge] = [await boxOf(container), await boxOf(table)]
  expect(formBox.right, `${route} ${form.name} @${width}: the form stays inside the table`).toBeLessThanOrEqual(tableEdge.right + 1)
  expect(await ownOverflow(container), `${route} ${form.name} @${width}: the form scrolls sideways`).toBeLessThanOrEqual(1)
  const [tableSlack, formSlack] = [await minContentSlack(table), await minContentSlack(container)]
  inPageLine(`${route} ${form.name}`, width, { table: open.table, box: open.box, form: formBox.width, tableSlack, formSlack, slack: Math.min(tableSlack, formSlack) })

  await form.close(container)
  await expect(container, `${route} ${form.name}: closed`).toHaveCount(0)
}

const cancel = async (container: Locator) => container.getByRole('button', { name: 'Cancel' }).click()

for (const width of [1280, 1440]) {
  test.describe(`${width} x 900`, () => {
    test.use({ viewport: { width, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test(`/failures @${width}: Mute, Jira and Suspects open under their row; the table keeps its width`, async ({ page }) => {
      watchNativeDialogs(page)
      // The Jira preview ends with a 1,000-character unbreakable line: only the row's zero-width cell keeps it
      // from widening the table (a cell that sized to its content would push the table past its box).
      const world = failuresInPageWorld({ longJiraLine: true })
      const { api, errors } = await openRollout(page, '/failures', { handlers: world.handlers, ready: (p) => landmark(p, 'Failure verdict'), flags: QUARANTINE_ON })
      const table = page.locator('[data-primary]').getByRole('table', { name: 'Top failing tests' })
      const row = ownRows(table).filter({ hasText: 'checkout total includes tax' })
      await expect(row).toHaveCount(1)
      await networkQuiet(page, api)
      const forms: RowForm[] = [
        {
          name: 'mute',
          open: (r) => r.getByRole('button', { name: 'Mute test checkout total includes tax' }).click(),
          container: (p) => opened(p, 'Mute test (propose quarantine)'),
          close: cancel,
          loaded: (c) => expect(c.getByRole('textbox')).toBeVisible(),
        },
        {
          name: 'jira',
          open: (r) => r.getByRole('button', { name: 'Create Jira issue for checkout total includes tax' }).click(),
          container: (p) => opened(p, 'Create Jira issue'),
          close: cancel,
          loaded: (c) => expect(c.getByLabel('Prefilled description')).toContainText(LONG_PREVIEW_LINE),
        },
        {
          name: 'suspects',
          open: (r) => r.getByRole('button', { name: 'Suspects for checkout total includes tax' }).click(),
          container: (p) => p.getByRole('complementary', { name: 'Suspects: checkout total includes tax' }),
          close: (c) => c.getByRole('button', { name: 'Close suspects' }).click(),
          loaded: (c) => expect(c.getByTestId('suspects-run')).toBeVisible(),
        },
      ]
      for (const form of forms) await expectUnderRow(page, '/failures', width, table, row, 6, form)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/defects @${width}: Change status, Assign and Link a Jira issue open under their row`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = defectsWorld()
      const { api, errors } = await openRollout(page, '/defects', { handlers: world.handlers, ready: (p) => landmark(p, 'Defect KPIs'), user: QA_ENGINEER })
      const table = page.getByRole('table', { name: 'Defects' })
      const row = ownRows(table).filter({ hasText: 'search paginates' })
      await expect(row).toHaveCount(1)
      await networkQuiet(page, api)
      const viaMenu = (item: string) => async (r: Locator) => {
        await r.getByRole('button', { name: 'Defect actions' }).click()
        await page.getByRole('menuitem', { name: item }).click()
      }
      const forms: RowForm[] = [
        {
          name: 'status',
          open: viaMenu('Change status…'),
          container: (p) => opened(p, 'Change status'),
          close: cancel,
          loaded: (c) => expect(c.getByRole('radio', { name: 'Open', exact: true })).toBeChecked(),
        },
        {
          name: 'assign',
          open: viaMenu('Assign…'),
          container: (p) => opened(p, 'Assign defect'),
          close: cancel,
          loaded: (c) => expect(c.getByLabel('Assignee').locator('option', { hasText: 'Priya Nair' })).toHaveCount(1),
        },
        {
          name: 'jira',
          open: viaMenu('Link Jira issue…'),
          container: (p) => opened(p, 'Link a Jira issue'),
          close: cancel,
          loaded: (c) => expect(c.getByText('Jira is not reachable from here (offline mode), so the key is saved without a check.')).toBeVisible(),
        },
      ]
      for (const form of forms) await expectUnderRow(page, '/defects', width, table, row, 8, form)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/flaky @${width}: the details and Propose quarantine open under their row`, async ({ page }) => {
      watchNativeDialogs(page)
      const proposals: unknown[] = []
      const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(proposals), ready: headerReady, flags: QUARANTINE_ON })
      const table = page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })
      const row = table.locator('tr[data-flaky-row="fp-04"]')
      await expect(row).toHaveCount(1)
      await networkQuiet(page, api)
      const forms: RowForm[] = [
        {
          name: 'details',
          open: (r) => r.getByRole('button', { name: 'test_checkout_flow_4', exact: true }).click(),
          container: (p) => p.getByRole('complementary', { name: 'test_checkout_flow_4', exact: true }),
          close: (c) => c.getByRole('button', { name: 'Close panel' }).click(),
          // The details come from the loaded entry (no read of their own): its title is enough.
          loaded: (c) => expect(c.getByRole('heading', { name: 'test_checkout_flow_4', exact: true })).toBeVisible(),
        },
        {
          name: 'propose',
          open: (r) => r.getByRole('button', { name: 'Propose quarantine' }).click(),
          container: (p) => opened(p, 'Propose quarantine'),
          close: cancel,
          loaded: (c) => expect(c.getByRole('textbox')).toBeVisible(),
        },
      ]
      for (const form of forms) await expectUnderRow(page, '/flaky', width, table, row, 7, form)
      await networkQuiet(page, api)
      expect(proposals).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/flaky Quarantined @${width}: File Jira opens under its row`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = flakyJiraWorld()
      const { api, errors } = await openRollout(page, '/flaky?tab=quarantined', { handlers: world.handlers, ready: headerReady, flags: QUARANTINE_ON })
      const row = page.locator('[data-primary]').locator(`tr[data-quarantine-id="${QUARANTINE_IDS.stale}"]`)
      await expect(row).toHaveCount(1)
      const table = row.locator('xpath=ancestor::table[1]')
      await networkQuiet(page, api)
      await expectUnderRow(page, '/flaky?tab=quarantined', width, table, row, 8, {
        name: 'jira',
        open: (r) => r.getByRole('button', { name: 'File Jira' }).click(),
        container: (p) => opened(p, 'Create Jira issue'),
        close: cancel,
        loaded: (c) => expect(c.getByLabel('Prefilled description')).toContainText('Failure: fp-q3'),
      })
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/my-failures @${width}: Reassign and Update triage status open under their row`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = inboxInPageWorld()
      const { api, errors } = await openRollout(page, '/my-failures', { handlers: world.handlers, ready: headerReady })
      const table = page.getByRole('table', { name: 'Assigned failures' })
      const row = ownRows(table).filter({ has: page.getByText('test_checkout_step_1', { exact: true }) })
      await expect(row).toHaveCount(1)
      await networkQuiet(page, api)
      const forms: RowForm[] = [
        {
          name: 'reassign',
          open: (r) => r.getByRole('button', { name: 'Reassign' }).click(),
          container: (p) => opened(p, 'Reassign failure'),
          close: cancel,
          loaded: (c) => expect(c.getByRole('radio', { name: /Dana Whitfield/ })).toBeChecked(),
        },
        {
          name: 'triage',
          open: (r) => r.getByRole('button', { name: 'Update status' }).click(),
          container: (p) => opened(p, 'Update triage status'),
          close: cancel,
          loaded: (c) => expect(c.getByRole('radio', { name: /^Reviewed & approved/ })).toBeChecked(),
        },
      ]
      for (const form of forms) await expectUnderRow(page, '/my-failures', width, table, row, 7, form)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })
  })
}

test.describe('/flaky details in the address', () => {
  test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  /** The whole URL is `<origin><pathAndQuery>`. */
  async function expectUrl(page: Page, pathAndQuery: string) {
    const origin = new URL(page.url()).origin
    await expect(page).toHaveURL(`${origin}${pathAndQuery}`)
  }

  test('?test=<fingerprint> is pushed on open; a refresh reopens it; Back closes it; an unknown or malformed one is dropped', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready: headerReady })
    const details = (name: string) => page.getByRole('complementary', { name, exact: true })
    await page.getByRole('button', { name: 'test_checkout_flow_0', exact: true }).click()
    await expectUrl(page, '/flaky?test=fp-00')
    await expect(details('test_checkout_flow_0')).toBeVisible()
    await expect(details('test_checkout_flow_0').getByText('Timing: a wait races the response')).toBeVisible()
    await expectNoOverlay(page)
    await page.reload()
    await expect(details('test_checkout_flow_0'), 'a refresh reopens it once the entries load').toBeVisible()
    await page.goBack()
    await expectUrl(page, '/flaky')
    await expect(details('test_checkout_flow_0')).toHaveCount(0)

    // A fingerprint no entry has: nothing opens and the key is dropped once the entries have loaded.
    await page.goto('/flaky?test=fp-nobody')
    await expect(page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })).toBeVisible()
    await expectUrl(page, '/flaky')
    await expect(page.locator('[data-in-page-row]')).toHaveCount(0)
    // A malformed one (a `/`): rejected, nothing opens.
    await page.goto('/flaky?test=a%2Fb')
    await expect(page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })).toBeVisible()
    await expect(page.locator('[data-in-page-row]')).toHaveCount(0)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
})
