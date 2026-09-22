/**
 * The DEV-only UI-primitives gallery (`/__primitives`, VIZ-109) in a real
 * browser: what jsdom cannot see.
 *
 *   - the route is reachable WITHOUT a backend and FAILS (never skips) when it
 *     is missing — a 404 or an auth bounce lands somewhere else;
 *   - axe finds no serious/critical violation in a dark and a light theme,
 *     with the MultiSelect popover closed and open;
 *   - the whole MultiSelect keyboard path: open, type-ahead, Space as a
 *     character, toggle, the cap, Escape, focus back on the trigger;
 *   - removing a chip moves focus to the next chip, then to the fallback;
 *   - the modal SidePanel traps Tab and both shapes return focus;
 *   - interactive targets are at least 24×24 px by BOUNDING BOX (a class name
 *     can say h-6 and still render smaller);
 *   - no horizontal scroll at 375 px, popover open or closed.
 *
 * Nothing is mocked: the page fetches nothing. The URL is relative, so it runs
 * under `playwright.ci.config.ts`'s baseURL or any other.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import {
  CHIP_LIMIT,
  HOSTILE_FLAG,
  INITIAL_CHIPS,
  LONG_LABEL,
  RELEASE_CAP,
  SUITE_OPTION_COUNT,
} from '../../src/pages/dev/primitivesFixtures'

const ROUTE = '/__primitives'
/** Every theme in `src/store/themeStore.ts`. */
const THEME_IDS = ['signal', 'console', 'slate', 'ember', 'lab', 'midnight']

function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console.error: ${message.text()}`)
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

async function openPrimitives(page: Page, search = '') {
  await page.goto(`${ROUTE}${search}`)
  // Fail, never skip: a missing route redirects (→ /overview → /login).
  expect(new URL(page.url()).pathname, 'the primitives route redirected').toBe(ROUTE)
  await expect(page.getByTestId('primitives-gallery')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: 'UI primitives (dev only)' })).toBeVisible()
  await expect(page.locator('[data-primitive]')).toHaveCount(7)
}

const releaseTrigger = (page: Page) => page.getByTestId('ms-release').getByRole('button').first()
const releaseCombobox = (page: Page) => page.getByRole('combobox', { name: 'Filter Release' })
const releaseListbox = (page: Page) => page.getByRole('listbox', { name: 'Release' })
const popover = (page: Page) => page.locator('[data-multiselect-popover]')

async function activeOption(page: Page, combobox: Locator): Promise<Locator> {
  const id = await combobox.getAttribute('aria-activedescendant')
  expect(id, 'no active option').toBeTruthy()
  return page.locator(`[id="${id}"]`)
}

async function expectFocusInside(container: Locator) {
  expect(await container.evaluate((node) => node.contains(document.activeElement))).toBe(true)
}

async function boxOf(locator: Locator) {
  const box = await locator.boundingBox()
  expect(box, 'element has no box').not.toBeNull()
  return box ?? { x: 0, y: 0, width: 0, height: 0 }
}

async function expectTarget24(locator: Locator, what: string) {
  const box = await boxOf(locator)
  expect(box.width, `${what}: width ${box.width}`).toBeGreaterThanOrEqual(24)
  expect(box.height, `${what}: height ${box.height}`).toBeGreaterThanOrEqual(24)
}

test.describe('UI primitives (/__primitives)', () => {
  test('is reachable without a session and renders with no errors', async ({ page }) => {
    const errors = watchErrors(page)
    await openPrimitives(page)
    await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toBeVisible()
    await expect(page.locator('[data-skeleton]')).toHaveCount(3)
    expect(errors).toEqual([])
  })

  /**
   * Pre-existing violations this spec may not fail on, by theme, rule AND
   * node, exactly as chart-gallery.spec.ts does it: nothing else can shelter
   * behind an entry, and the ratchet below fails the moment an entry stops
   * firing, so the list only ever shrinks. "Green" here means "no NEW
   * violation", never "none".
   */
  const KNOWN_VIOLATIONS: { theme: string; rule: string; nodeHtmlIncludes: string }[] = []

  for (const theme of THEME_IDS) {
    test(`has no serious or critical automated accessibility violations (${theme})`, async ({ page }) => {
      await openPrimitives(page, `?theme=${theme}`)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)

      const known = KNOWN_VIOLATIONS.filter((entry) => entry.theme === theme)
      const isKnown = (rule: string, html: string) =>
        known.some((entry) => entry.rule === rule && html.includes(entry.nodeHtmlIncludes))
      const seen: { rule: string; html: string }[] = []

      const blocking = async () => {
        const result = await new AxeBuilder({ page }).analyze()
        for (const violation of result.violations) {
          for (const node of violation.nodes) seen.push({ rule: violation.id, html: node.html })
        }
        return result.violations
          .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
          .map((violation) => ({
            id: violation.id,
            nodes: violation.nodes.filter((node) => !isKnown(violation.id, node.html)).map((node) => node.html),
          }))
          .filter((violation) => violation.nodes.length > 0)
      }

      expect(await blocking(), 'popover closed').toEqual([])

      await releaseTrigger(page).click()
      await expect(releaseListbox(page)).toBeVisible()
      await releaseCombobox(page).press('ArrowDown')
      expect(await blocking(), 'popover open').toEqual([])

      // A windowed (scrolling) listbox, the cap reached, and both panels.
      await page.keyboard.press('Escape')
      await page.getByTestId('ms-suite').getByRole('button').first().click()
      await page.getByRole('button', { name: /^Show more/ }).click()
      expect(await blocking(), 'windowed listbox open').toEqual([])
      await page.keyboard.press('Escape')

      await releaseTrigger(page).click()
      for (let i = 0; i < RELEASE_CAP; i += 1) {
        await page.keyboard.press('ArrowDown')
        await page.keyboard.press('Space')
      }
      await expect(popover(page).getByRole('status')).toContainText(`Limit reached (${RELEASE_CAP})`)
      expect(await blocking(), 'cap reached').toEqual([])
      await page.keyboard.press('Escape')

      await page.getByRole('button', { name: 'Open side panel', exact: true }).click()
      await expect(page.getByRole('complementary', { name: 'Run details' })).toBeVisible()
      expect(await blocking(), 'side panel open').toEqual([])
      await page.keyboard.press('Escape')

      await page.getByRole('button', { name: 'Open modal side panel' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
      expect(await blocking(), 'modal side panel open').toEqual([])

      // The ratchet: an allowlisted violation that no longer occurs is a fix,
      // and the allowlist must shrink with it.
      for (const entry of known) {
        const stillPresent = seen.some((v) => v.rule === entry.rule && v.html.includes(entry.nodeHtmlIncludes))
        expect(
          stillPresent,
          `${entry.rule} on "${entry.nodeHtmlIncludes}" (${theme}) no longer fires — remove it from KNOWN_VIOLATIONS`,
        ).toBe(true)
      }
    })
  }

  test('MultiSelect: each option is named by its full label, then its count, then why it is disabled', async ({
    page,
  }) => {
    await openPrimitives(page)
    await releaseTrigger(page).click()
    const listbox = releaseListbox(page)
    await expect(listbox.getByRole('option', { name: 'Release 1, 37', exact: true })).toBeVisible()
    await releaseCombobox(page).fill('archived')
    await expect(listbox.getByRole('option', { name: 'Release archived, 0, Archived', exact: true })).toBeVisible()
    // Truncated on screen, but named by the whole label — never the "…" form.
    await releaseCombobox(page).fill('payments-spec')
    const long = listbox.getByRole('option', { name: `${LONG_LABEL}, 1,234,567`, exact: true })
    await expect(long).toBeVisible()
    await expect(long).toContainText('…')
  })

  for (const viewport of [
    { width: 320, height: 256 }, // 1280×1024 at 400 %
    { width: 640, height: 450 }, // ~1280×900 at 200 %
  ]) {
    test(`MultiSelect: every control in the popover is reachable at ${viewport.width}×${viewport.height} (reflow)`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport)
      await openPrimitives(page)
      const trigger = releaseTrigger(page)
      await trigger.scrollIntoViewIfNeeded()
      await trigger.click()
      const pop = popover(page)
      await expect(pop).toBeVisible()

      const popBox = await boxOf(pop)
      expect(popBox.y, 'popover top').toBeGreaterThanOrEqual(0)
      expect(popBox.y + popBox.height, 'popover bottom').toBeLessThanOrEqual(viewport.height)

      const controls = pop.locator('input, button:not([disabled]), [role="option"]')
      const names: string[] = []
      for (const name of ['Select all', 'Select none', /^Show more/]) {
        await expect(pop.getByRole('button', { name })).toHaveCount(1)
      }
      const n = await controls.count()
      expect(n).toBeGreaterThan(10)
      for (let i = 0; i < n; i += 1) {
        const control = controls.nth(i)
        await control.scrollIntoViewIfNeeded()
        const box = await boxOf(control)
        const what = (await control.getAttribute('aria-label')) ?? (await control.textContent()) ?? `#${i}`
        names.push(what)
        expect(box.y, `${what}: top ${box.y}`).toBeGreaterThanOrEqual(0)
        expect(box.y + box.height, `${what}: bottom`).toBeLessThanOrEqual(viewport.height + 0.5)
        expect(box.x, `${what}: left`).toBeGreaterThanOrEqual(0)
        expect(box.x + box.width, `${what}: right`).toBeLessThanOrEqual(viewport.width + 0.5)
      }
      // "Show more" really works from down there.
      await pop.getByRole('button', { name: /^Show more/ }).click()
      await expect(releaseListbox(page).getByRole('option')).toHaveCount(60)
    })
  }

  test('MultiSelect: a disabled control shows its reason on screen', async ({ page }) => {
    await openPrimitives(page)
    const reason = page.getByTestId('ms-disabled').getByText('Pick a project first')
    const box = await boxOf(reason)
    // Not a 1×1 px screen-reader-only clip.
    expect(box.width).toBeGreaterThan(40)
    expect(box.height).toBeGreaterThan(8)
    await expect(page.getByTestId('ms-disabled').getByRole('button').first()).toHaveAccessibleDescription(
      'Pick a project first',
    )
  })

  test('MultiSelect: Tab past the popover moves on to the next control after the trigger', async ({ page }) => {
    await openPrimitives(page)
    await releaseTrigger(page).click()
    await popover(page).getByRole('button', { name: /^Show more/ }).focus()
    await page.keyboard.press('Tab')
    await expect(popover(page)).toHaveCount(0)
    await expect(page.getByTestId('ms-suite').getByRole('button').first()).toBeFocused()
  })

  for (const viewport of [
    { width: 375, height: 812 },
    { width: 1280, height: 720 },
  ]) {
    test(`SidePanel: with the panel open no focused control is hidden behind it (${viewport.width} px)`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport)
      await openPrimitives(page)
      await page.getByRole('button', { name: 'Open side panel', exact: true }).click()
      const phone = viewport.width < 640
      await expect(page.locator('[data-side-panel]')).toBeVisible()

      let outside = 0
      const obscured: string[] = []
      for (let i = 0; i < 30; i += 1) {
        await page.keyboard.press('Tab')
        const r = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null
          const panel = document.querySelector('[data-side-panel]')
          if (!el || el === document.body || !panel) return null
          if (panel.contains(el)) return { inside: true, ok: true, what: '' }
          const b = el.getBoundingClientRect()
          const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)
          const what = `${el.tagName} ${(el.getAttribute('aria-label') ?? el.textContent ?? '').slice(0, 40)}`
          return { inside: false, ok: !!hit && (hit === el || el.contains(hit)), what }
        })
        if (!r || r.inside) continue
        outside += 1
        if (!r.ok) obscured.push(r.what)
      }
      expect(obscured, 'focused controls hidden behind the panel').toEqual([])
      // Not vacuous: a phone traps focus in the (modal) panel; a wide screen
      // really did walk the page with the panel open.
      if (phone) {
        expect(outside).toBe(0)
        await expect(page.getByRole('dialog', { name: 'Run details' })).toHaveAttribute('aria-modal', 'true')
      } else {
        expect(outside).toBeGreaterThan(5)
        const panel = page.getByRole('complementary', { name: 'Run details' })
        await expect(panel).toBeVisible()
        // The page reflowed beside the panel: no section runs underneath it,
        // not just the controls this walk happened to reach.
        const panelLeft = (await boxOf(panel)).x
        const sections = page.locator('[data-primitive]')
        for (let i = 0; i < (await sections.count()); i += 1) {
          const box = await boxOf(sections.nth(i))
          expect(box.x + box.width, `section ${i} runs under the panel`).toBeLessThanOrEqual(panelLeft + 0.5)
        }
      }
    })
  }

  test('SidePanel: the modal shape locks the page scroll without a layout shift, and restores it', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 600 })
    await openPrimitives(page)
    const opener = page.getByRole('button', { name: 'Open modal side panel' })
    await opener.scrollIntoViewIfNeeded()
    const styleBefore = await page.evaluate(() => document.body.getAttribute('style'))
    const boxBefore = await boxOf(opener)
    const scrollBefore = await page.evaluate(() => window.scrollY)
    expect(await page.evaluate(() => document.documentElement.scrollHeight > window.innerHeight)).toBe(true)

    await opener.click()
    await expect(page.getByRole('dialog')).toBeVisible()
    const boxOpen = await boxOf(opener)
    expect(boxOpen.x, 'no horizontal shift').toBe(boxBefore.x)
    expect(boxOpen.width, 'no width change').toBe(boxBefore.width)

    await page.mouse.move(100, 300) // the backdrop
    await page.mouse.wheel(0, 600)
    await page.waitForTimeout(300) // a wheel scroll is async; nothing to poll for when it must NOT move
    expect(await page.evaluate(() => window.scrollY), 'the page behind scrolled').toBe(scrollBefore)

    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expect(await page.evaluate(() => document.body.getAttribute('style'))).toBe(styleBefore)
    await page.mouse.wheel(0, 200)
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(scrollBefore)
  })

  test('MultiSelect: the full keyboard path, cap included', async ({ page }) => {
    const errors = watchErrors(page)
    await openPrimitives(page)
    const trigger = releaseTrigger(page)
    const value = page.getByTestId('ms-release-value')

    await trigger.focus()
    await page.keyboard.press('Enter')
    const combobox = releaseCombobox(page)
    await expect(combobox).toBeFocused()
    await expect(trigger).toHaveAttribute('aria-expanded', 'true')
    await expect(releaseListbox(page).getByRole('option')).toHaveCount(10)

    // Type-ahead filters. Space is a character until an option is active.
    await page.keyboard.type('Release 1')
    await page.keyboard.press('Space')
    await expect(combobox).toHaveValue('Release 1 ')
    await expect(value).toHaveText('Selected releases: none')
    await expect(releaseListbox(page).getByRole('option').first()).toHaveAttribute('data-value', 'r1')

    // Arrow to an option; Space toggles it.
    await page.keyboard.press('ArrowDown')
    await expect(await activeOption(page, combobox)).toHaveAttribute('data-value', 'r1')
    await page.keyboard.press('Space')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Space')
    await expect(value).toHaveText('Selected releases: r1, r10, r11')

    // Each toggle is announced once; the cap is announced, the rest are
    // aria-disabled, and a fourth toggle is refused.
    const status = popover(page).getByRole('status')
    await expect(status).toHaveText(`Release 11 selected, ${RELEASE_CAP} of ${RELEASE_CAP}. Limit reached (${RELEASE_CAP})`)
    await page.keyboard.press('ArrowDown')
    const blocked = await activeOption(page, combobox)
    await expect(blocked).toHaveAttribute('aria-disabled', 'true')
    await page.keyboard.press('Space')
    await expect(value).toHaveText('Selected releases: r1, r10, r11')

    // Escape closes and hands focus back to the trigger, which shows the count.
    await page.keyboard.press('Escape')
    await expect(popover(page)).toHaveCount(0)
    await expect(trigger).toBeFocused()
    await expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await expect(trigger).toHaveAttribute('data-active', 'true')
    await expect(trigger).toHaveAccessibleName(`Release, ${RELEASE_CAP} selected`)
    expect(errors).toEqual([])
  })

  test('MultiSelect: a hostile label stays text in a real browser', async ({ page }) => {
    await openPrimitives(page)
    await releaseTrigger(page).click()
    await releaseCombobox(page).fill('bold')
    const option = releaseListbox(page).getByRole('option')
    await expect(option).toHaveCount(1)
    await expect(option.locator('img, b')).toHaveCount(0)
    expect(await page.evaluate((flag) => (window as unknown as Record<string, unknown>)[flag], HOSTILE_FLAG)).toBeUndefined()
  })

  test('MultiSelect: 500 options are windowed and End reaches the last', async ({ page }) => {
    await openPrimitives(page)
    await page.getByTestId('ms-suite').getByRole('button').first().click()
    await page.getByRole('button', { name: `Show more (${SUITE_OPTION_COUNT - 10})` }).click()
    const combobox = page.getByRole('combobox', { name: 'Filter Suite' })
    await expect(combobox).toBeFocused()
    const options = page.getByRole('listbox', { name: 'Suite' }).getByRole('option')
    expect(await options.count()).toBeLessThan(60)
    await expect(options.first()).toHaveAttribute('aria-setsize', String(SUITE_OPTION_COUNT))

    // The scroller stays ten rows tall however many rows it stands in for.
    const scroller = popover(page).locator('[data-multiselect-scroller]')
    expect((await boxOf(scroller)).height).toBeLessThanOrEqual(321)

    await page.keyboard.press('End')
    const last = await activeOption(page, combobox)
    await expect(last).toBeVisible()
    await expect(last).toHaveAttribute('aria-posinset', String(SUITE_OPTION_COUNT))
    await expect(last).toHaveAttribute('data-value', `suite-${SUITE_OPTION_COUNT}`)
    expect(await options.count()).toBeLessThan(60)

    // Scrolling with the wheel/scrollbar moves the window too.
    await scroller.evaluate((node) => {
      node.scrollTop = 100 * 32
    })
    await expect(page.getByRole('option', { name: /^suite-101\b/ })).toBeVisible()
    expect(await options.count()).toBeLessThan(60)
    // ...and the active option follows it: aria-activedescendant never names
    // an unrendered row, and Space toggles a row the user can see.
    const pulled = await activeOption(page, combobox)
    await expect(pulled).toBeVisible()
    const pulledValue = await pulled.getAttribute('data-value')
    await page.keyboard.press('Space')
    await expect(pulled).toHaveAttribute('aria-selected', 'true')
    expect(pulledValue).not.toBe(`suite-${SUITE_OPTION_COUNT}`)
  })

  test('MultiSelect: opened inside a scroll box, the popover is not clipped by it', async ({ page }) => {
    await openPrimitives(page)
    const trigger = page.getByTestId('ms-scroll').getByRole('button').first()
    await trigger.scrollIntoViewIfNeeded()
    await trigger.click()
    const pop = popover(page)
    await expect(pop).toBeVisible()
    const box = await boxOf(pop)
    const viewport = page.viewportSize() ?? { width: 0, height: 0 }
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width)
    // Taller than the 96 px scroll box it was opened from: not clipped by it.
    expect(box.height).toBeGreaterThan(96)
    await expect(page.getByRole('option', { name: /feature\/viz/ })).toBeVisible()
  })

  test('ChipList: removing a chip moves focus to the next one, then to the fallback', async ({ page }) => {
    await openPrimitives(page)
    const list = page.getByRole('list', { name: 'Active filters' })
    await expect(list.getByRole('listitem')).toHaveCount(CHIP_LIMIT)
    await expect(page.getByRole('button', { name: `+${INITIAL_CHIPS.length - CHIP_LIMIT} more` })).toBeVisible()

    await page.getByRole('button', { name: 'Remove filter Release R3' }).focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Remove filter Release R3' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Remove filter Suite checkout' })).toBeFocused()

    // Keep removing with the keyboard until nothing is left.
    for (let i = 1; i < INITIAL_CHIPS.length; i += 1) {
      await page.keyboard.press('Enter')
    }
    await expect(list).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Filters', exact: true })).toBeFocused()
  })

  test('SidePanel: the modal shape traps Tab; both shapes return focus', async ({ page }) => {
    await openPrimitives(page)

    const openModal = page.getByRole('button', { name: 'Open modal side panel' })
    await openModal.focus()
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog', { name: 'Modal run details' })
    await expect(dialog).toHaveAttribute('aria-modal', 'true')
    await expectFocusInside(dialog)
    for (let i = 0; i < 6; i += 1) {
      await page.keyboard.press('Tab')
      await expectFocusInside(dialog)
    }
    for (let i = 0; i < 6; i += 1) {
      await page.keyboard.press('Shift+Tab')
      await expectFocusInside(dialog)
    }
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(openModal).toBeFocused()

    const openPanel = page.getByRole('button', { name: 'Open side panel', exact: true })
    await openPanel.focus()
    await page.keyboard.press('Enter')
    const panel = page.getByRole('complementary', { name: 'Run details' })
    await expect(panel).toBeVisible()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expectFocusInside(panel)
    await page.keyboard.press('Escape')
    await expect(panel).toHaveCount(0)
    await expect(openPanel).toBeFocused()
  })

  test('interactive targets are at least 24×24 px', async ({ page }) => {
    await openPrimitives(page)
    const removes = page.getByRole('button', { name: /^Remove filter / })
    expect(await removes.count()).toBe(CHIP_LIMIT)
    for (let i = 0; i < CHIP_LIMIT; i += 1) await expectTarget24(removes.nth(i), `chip remove ${i}`)
    await expectTarget24(page.getByRole('button', { name: /more$/ }), '+N more')
    await expectTarget24(releaseTrigger(page), 'MultiSelect trigger')

    const links = page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link')
    for (let i = 0; i < (await links.count()); i += 1) {
      expect((await boxOf(links.nth(i))).height, `breadcrumb link ${i}`).toBeGreaterThanOrEqual(24)
    }

    await releaseTrigger(page).click()
    for (const name of ['Select all', 'Select none', /^Show more/]) {
      const button = popover(page).getByRole('button', { name })
      const box = await boxOf(button)
      expect(box.height, `${String(name)} height`).toBeGreaterThanOrEqual(24)
    }
    expect((await boxOf(releaseListbox(page).getByRole('option').first())).height).toBeGreaterThanOrEqual(24)
  })

  test('does not scroll sideways at phone width, popover closed or open', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await openPrimitives(page)

    const overflow = () =>
      page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        bodyScrollWidth: document.body.scrollWidth,
      }))
    let o = await overflow()
    expect(o.scrollWidth, JSON.stringify(o)).toBeLessThanOrEqual(o.clientWidth)
    expect(o.bodyScrollWidth, JSON.stringify(o)).toBeLessThanOrEqual(o.clientWidth)

    await releaseTrigger(page).click()
    const box = await boxOf(popover(page))
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(375)
    o = await overflow()
    expect(o.scrollWidth, JSON.stringify(o)).toBeLessThanOrEqual(o.clientWidth)

    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Open side panel', exact: true }).click()
    // No room beside the page at 375 px: the non-modal panel renders modal.
    const panel = page.getByRole('dialog', { name: 'Run details' })
    expect((await boxOf(panel)).width).toBeLessThanOrEqual(375)
    o = await overflow()
    expect(o.scrollWidth, JSON.stringify(o)).toBeLessThanOrEqual(o.clientWidth)
  })
})
