/**
 * W27a phase 2 (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.2-§2.4, §4.1): the geometry the in-page
 * specs measure on the CONVERTED pages (`tests/ci-e2e/in-page-*.spec.ts`). Everything is read from the live
 * layout after the fonts load (CI's DejaVu is wider than Windows' fonts: W06 lost two CI rounds to a 42 px
 * overflow), and every width the contract plans with 40 px of slack is PRINTED (`INPAGE …` lines) so a run on CI
 * reports the real slack.
 *
 * "Slack" is how much narrower an element's content could still get before it overflows: its width minus its
 * min-content width (measured by laying it out at `width: min-content` for one frame, then restoring its inline
 * style exactly).
 */
import { expect, type Locator, type Page } from '@playwright/test'
import { MAIN } from './rollout'

export interface Box {
  left: number
  right: number
  top: number
  width: number
}

/** An element's box, rounded (fails if it has none). */
export async function boxOf(target: Locator): Promise<Box> {
  const box = await target.boundingBox()
  expect(box, 'the element has a box').not.toBeNull()
  const b = box as { x: number; y: number; width: number }
  return { left: Math.round(b.x), right: Math.round(b.x + b.width), top: Math.round(b.y), width: Math.round(b.width) }
}

/** `#main-content`'s horizontal overflow (the shell scrolls there, never the window). */
export async function mainOverflow(page: Page): Promise<{ scrollWidth: number; clientWidth: number }> {
  await page.evaluate(() => document.fonts.ready)
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    return { scrollWidth: main.scrollWidth, clientWidth: main.clientWidth }
  }, MAIN)
}

/** `#main-content` does not scroll sideways (polled briefly: a late layout pass settles first). */
export async function expectMainFits(page: Page, where: string) {
  await expect
    .poll(async () => {
      const o = await mainOverflow(page)
      return o.scrollWidth - o.clientWidth
    }, { message: `${where}: ${MAIN} scrolls sideways`, timeout: 5_000 })
    .toBeLessThanOrEqual(0)
}

/** `target`'s width minus its min-content width, px (negative: its content is wider than it). */
export async function minContentSlack(target: Locator): Promise<number> {
  await target.evaluate(() => document.fonts.ready)
  return target.evaluate((el: HTMLElement) => {
    const width = el.getBoundingClientRect().width
    const had = el.hasAttribute('style')
    const before = el.style.width
    el.style.width = 'min-content'
    const min = el.getBoundingClientRect().width
    el.style.width = before
    if (!had && el.getAttribute('style') === '') el.removeAttribute('style')
    return Math.round(width - min)
  })
}

/** An element's own horizontal overflow (`scrollWidth - clientWidth`, px). */
export async function ownOverflow(target: Locator): Promise<number> {
  return target.evaluate((el: HTMLElement) => el.scrollWidth - el.clientWidth)
}

/**
 * Each table inside `scope` against the box that bounds it (its nearest ancestor that clips or scrolls sideways,
 * else `scope`): `fold-budget.spec.ts`'s `clippedTables`, for any scope. Returns the tables wider than their box.
 */
export async function clippedTables(scope: Locator): Promise<string[]> {
  return scope.evaluate((root: HTMLElement) => {
    const out: string[] = []
    for (const table of Array.from(root.querySelectorAll<HTMLElement>('table, [role="table"]'))) {
      let box: HTMLElement = root
      for (let el = table.parentElement; el && root.contains(el); el = el.parentElement) {
        if (getComputedStyle(el).overflowX !== 'visible') {
          box = el
          break
        }
      }
      if (box.clientWidth <= 1) continue
      const width = Math.round(table.getBoundingClientRect().width)
      if (width > box.clientWidth + 1) out.push(`${table.getAttribute('aria-label') ?? table.tagName.toLowerCase()}: ${width} px in a ${box.clientWidth} px box`)
    }
    return out
  })
}

/** The box that bounds a table: its nearest ancestor that clips or scrolls sideways (else its parent). */
export async function tableBox(table: Locator): Promise<{ table: number; box: number }> {
  return table.evaluate((el: HTMLElement) => {
    let box: HTMLElement = el.parentElement as HTMLElement
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== 'visible') {
        box = p
        break
      }
    }
    return { table: Math.round(el.getBoundingClientRect().width), box: box.clientWidth }
  })
}

/** `a` comes before `b` in document order. */
export async function precedes(a: Locator, b: Locator): Promise<boolean> {
  const handle = await b.elementHandle()
  return a.evaluate((first, second) => {
    if (!second) return false
    return Boolean(first.compareDocumentPosition(second as Node) & Node.DOCUMENT_POSITION_FOLLOWING)
  }, handle)
}

/**
 * W27b: `b` is the next thing after `a` in the page's flow: the element right after `a` (climbing out of the wrappers
 * `a` is the last child of) is `b` or wraps it. "Right after the cadence card", "right above the tests table".
 */
export async function followedBy(a: Locator, b: Locator): Promise<boolean> {
  const second = await b.elementHandle()
  return a.evaluate((first, target) => {
    if (!target) return false
    let el: Element | null = first
    while (el && !el.nextElementSibling) el = el.parentElement
    const next = el?.nextElementSibling ?? null
    return !!next && (next === target || next.contains(target as Node))
  }, second)
}

/** Print one `INPAGE` line (read by the orchestrator from CI's log). */
export function inPageLine(what: string, width: number, numbers: Record<string, unknown>) {
  const line = `INPAGE ${what} @${width} ${JSON.stringify(numbers)}`
  console.log(line)
  return line
}
