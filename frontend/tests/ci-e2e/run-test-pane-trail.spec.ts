/**
 * The AI analysis's Investigation Trail inside the run page's test pane, at 1280 and 1440 px. The steps were a
 * viewport grid (`sm:grid-cols-2 xl:grid-cols-4`), so at xl the ~450 px pane still made four columns of ~70 px and
 * "evidence" / "generated" ran out of their boxes (seen in the W29d `run-test-pane` baselines). The steps now take as
 * many columns as the panel holds (`repeat(auto-fit, minmax(11rem, 1fr))`) and their labels wrap inside the box.
 *
 *  - in the pane: every label's text ends inside its box's padding, and no word is broken across lines;
 *  - "1 evidence item", not "1 evidence items";
 *  - expanded to the full width: the three steps share one row (the wide layout keeps its columns).
 *
 * Prints `INPAGE /runs/:id test pane trail @W {pane, trail, columns, escape, wordSlack}`. Fixtures:
 * `runAnalysisWorld()` (`fixtures-in-page.ts`; the stored analysis used one tool and found one evidence item).
 * Hermetic and fail closed (`tests/lib/rollout.ts`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { boxOf, inPageLine } from '../lib/inPageGeometry'
import { assertHermetic, expectNoHorizontalOverflow, networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { runAnalysisWorld } from '../visual/production/fixtures-in-page'

const STEPS = ['Fetched stack trace', '1 evidence item', 'AI summary generated']

/** Opens the first failing test of the Tests tab in its pane and waits for the stored analysis's trail. */
async function openTrail(page: Page) {
  const world = runAnalysisWorld()
  const opened = await openRollout(page, `/runs/${RUN_ID}?tab=tests`, {
    handlers: world.handlers,
    ready: (p) => p.locator('[data-primary] table'),
  })
  const first = world.tests.find((t) => t.status === 'BROKEN')
  await page.locator(`[data-test-row="${first?.id}"]`).click()
  const pane = page.getByRole('complementary', { name: String(first?.test_name), exact: true })
  const trail = pane.getByTestId('investigation-trail-steps')
  await expect(trail.locator('[data-trail-step]')).toHaveText(STEPS)
  await networkQuiet(page, opened.api)
  await page.evaluate(() => document.fonts.ready)
  return { ...opened, pane, trail }
}

/**
 * Per step: how far the label's text runs past the box's content edge (px, > 0 = out of the box), the words drawn
 * on more than one line, and the room beside the tick minus the widest word (px: before a word would have to break).
 */
async function stepGeometry(trail: Locator) {
  return trail.locator('[data-trail-step]').evaluateAll((boxes: HTMLElement[]) =>
    boxes.map((box) => {
      const style = getComputedStyle(box)
      const inner = box.getBoundingClientRect().right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth)
      const label = box.querySelector('p') as HTMLElement
      const text = label.firstChild as Text
      const range = document.createRange()
      range.selectNodeContents(label)
      const textRight = Math.max(...[...range.getClientRects()].map((r) => r.right))
      const words: { word: string; width: number; lines: number }[] = []
      let at = 0
      for (const word of (text.textContent ?? '').split(' ')) {
        range.setStart(text, at)
        range.setEnd(text, at + word.length)
        words.push({ word, width: range.getBoundingClientRect().width, lines: range.getClientRects().length })
        at += word.length + 1
      }
      return {
        label: label.textContent,
        escape: Math.round((textRight - inner) * 10) / 10,
        broken: words.filter((w) => w.lines > 1).map((w) => w.word),
        wordSlack: Math.round(inner - label.getBoundingClientRect().left - Math.max(...words.map((w) => w.width))),
      }
    }),
  )
}

for (const width of [1280, 1440]) {
  test(`at ${width} px the Investigation Trail's steps fit their boxes in the run page's test pane`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { api, errors, pane, trail } = await openTrail(page)

    const steps = await stepGeometry(trail)
    const columns = await trail.evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length)
    inPageLine('/runs/:id test pane trail', width, {
      pane: (await boxOf(pane)).width,
      trail: (await boxOf(trail)).width,
      columns,
      escape: Math.max(...steps.map((s) => s.escape)),
      wordSlack: Math.min(...steps.map((s) => s.wordSlack)),
    })
    for (const step of steps) {
      expect(step.escape, `"${step.label}" ends inside its box`).toBeLessThanOrEqual(0.5)
      expect(step.broken, `"${step.label}" breaks no word across lines`).toEqual([])
    }
    // Exact: a loose match finds "1 evidence item" + the next chip's "Stack trace" ("…itemS").
    await expect(pane.getByText('1 evidence items', { exact: true })).toHaveCount(0)
    await expect(pane.getByText('1 evidence item', { exact: true })).toHaveCount(3)
    await expectNoHorizontalOverflow(page, `/runs/:id test pane at ${width} px`)
    assertHermetic(api, errors)
  })
}

test('expanded to the full width at 1280 px, the trail keeps its steps in one row', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const { api, errors, pane, trail } = await openTrail(page)
  await pane.getByRole('button', { name: 'Expand to full width' }).click()
  await expect(pane.getByRole('button', { name: 'Show the list beside it' })).toBeVisible()

  const tops = await trail.locator('[data-trail-step]').evaluateAll((boxes) => boxes.map((b) => Math.round(b.getBoundingClientRect().top)))
  expect(new Set(tops).size, `one row (tops ${tops.join(', ')})`).toBe(1)
  for (const step of await stepGeometry(trail)) {
    expect(step.escape, `"${step.label}" ends inside its box`).toBeLessThanOrEqual(0.5)
    expect(step.broken, `"${step.label}" breaks no word across lines`).toEqual([])
  }
  assertHermetic(api, errors)
})
