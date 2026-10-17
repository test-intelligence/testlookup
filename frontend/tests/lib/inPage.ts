/**
 * W27a "in-page panels" (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §4.4): the role-agnostic
 * locators the characterization specs (`tests/ci-e2e/characterize-*.spec.ts`) drive every overlay flow with,
 * so the SAME spec passes, unedited, before the conversion (a modal dialog, a modal or edge `SidePanel`, a
 * native `confirm()`) and after it (an in-page `<section>` / `<aside>`, an inline confirmation strip).
 *
 *  - `opened(scope, name)`: the container a flow opened, by the accessible name it KEEPS across the
 *    conversion (§1.4): today's `dialog`, or tomorrow's `region` (an `InlineFormSection`) or `complementary`
 *    (a `DetailPane`, an `InlineFormSection as="aside"`). A string name is matched EXACTLY: the union must
 *    never pick up a longer name that merely contains it. `openedByHeading` is the same for the two dialogs
 *    that have no name today and get their heading as their name (§0.23).
 *  - `answerConfirm(page, trigger, 'accept' | 'dismiss', options)`: click `trigger` and answer the question it
 *    asks, whether the page asks it with a native `confirm()` (today) or with an `InlineConfirm` strip
 *    (`[data-inline-confirm]`, tomorrow). Returns which it was and the sentence, so a spec pins the words.
 *  - `expectNoOverlay(page)` (the in-page specs only: it FAILS on today's tree by design): no
 *    `[aria-modal="true"]`, no `role="dialog"` outside the OR-03 popovers, and no native dialog since
 *    `watchNativeDialogs(page)`.
 *
 * One `dialog` listener per page, owned here: Playwright requires a listener to answer every dialog it is
 * given, so two listeners (a spec's own and this module's) would answer the same dialog twice. A spec that
 * uses `answerConfirm` therefore registers no `page.on('dialog')` of its own.
 */
import { expect, type Locator, type Page } from '@playwright/test'

type Scope = Page | Locator

/** Today's overlay OR tomorrow's in-page container, by its kept accessible name. */
export function opened(scope: Scope, name: string | RegExp): Locator {
  const exact = typeof name === 'string' ? true : undefined
  return scope
    .getByRole('dialog', { name, exact })
    .or(scope.getByRole('region', { name, exact }))
    .or(scope.getByRole('complementary', { name, exact }))
}

/**
 * `opened` for a container that has NO accessible name today (contract §0.23: My failures' Reassign and Triage
 * dialogs carry `role="dialog"` and nothing names them) and is named by its heading after the conversion:
 * today's dialog that contains `heading`, or tomorrow's container named `heading`.
 */
export function openedByHeading(scope: Scope, heading: string): Locator {
  const root = 'page' in scope && typeof scope.page === 'function' ? scope.page() : (scope as Page)
  const titled = root.getByRole('heading', { name: heading, exact: true })
  return opened(scope, heading).or(scope.getByRole('dialog').filter({ has: titled }))
}

// ── Native dialogs: one listener per page ──────────────────────────────────

interface NativeDialogs {
  /** Every native dialog the page opened since the listener was installed, as `<type>: <message>`. */
  fired: string[]
  /** The answer for the next `confirm()` (set by `answerConfirm` around its click), else null. */
  next: 'accept' | 'dismiss' | null
  /** Resolves the wait of an `answerConfirm` in flight with the dialog's message. */
  onConfirm: ((message: string) => void) | null
}

const listeners = new WeakMap<Page, NativeDialogs>()

function nativeDialogs(page: Page): NativeDialogs {
  let state = listeners.get(page)
  if (state) return state
  const created: NativeDialogs = { fired: [], next: null, onConfirm: null }
  page.on('dialog', (dialog) => {
    created.fired.push(`${dialog.type()}: ${dialog.message()}`)
    const answer = dialog.type() === 'confirm' ? created.next : null
    created.next = null
    const notify = created.onConfirm
    created.onConfirm = null
    // An unexpected dialog (no answer pending) is dismissed: what Playwright does with no listener at all.
    void (answer === 'accept' ? dialog.accept() : dialog.dismiss()).catch(() => undefined)
    if (answer && notify) notify(dialog.message())
  })
  listeners.set(page, created)
  state = created
  return state
}

/** Record every native dialog from now on (call before `goto` in a spec that ends with `expectNoOverlay`). */
export function watchNativeDialogs(page: Page): void {
  nativeDialogs(page)
}

export interface ConfirmAnswer {
  /** How the page asked: a native `confirm()` or the in-page strip. */
  kind: 'native' | 'inline'
  /** The question, whitespace collapsed (the strip's text content, or the native dialog's message). */
  message: string
}

export interface AnswerConfirmOptions {
  /** The strip's confirm button label (`InlineConfirm.confirmLabel`), checked when the strip answers. */
  confirmLabel?: string
  /** The strip's cancel button label (default `Cancel`). */
  cancelLabel?: string
  /** Where the strip appears (default: the page). */
  scope?: Scope
  /** How long to wait for the strip when no native dialog fired (default 10 s). */
  timeout?: number
}

const collapse = (text: string) => text.replace(/\s+/g, ' ').trim()

/**
 * Click `trigger`, then answer its confirmation: the native `confirm()` it opens (today), or, when none
 * fired, the inline strip (`[data-inline-confirm]`): its `[data-inline-confirm-action]` to accept, its
 * Cancel to dismiss. Sends nothing itself: the request (if any) is the page's.
 */
export async function answerConfirm(
  page: Page,
  trigger: Locator,
  answer: 'accept' | 'dismiss',
  options: AnswerConfirmOptions = {},
): Promise<ConfirmAnswer> {
  const state = nativeDialogs(page)
  const native: { message: string | null } = { message: null }
  const fired = new Promise<string>((resolve) => {
    state.onConfirm = (message) => {
      native.message = message
      resolve(message)
    }
  })
  state.next = answer
  try {
    await trigger.click()
    // A native confirm() blocks the click until it is answered, so it has fired by now; the race only
    // guards a dialog reported a moment after the click resolved.
    const strip = (options.scope ?? page).locator('[data-inline-confirm]')
    if (native.message === null) {
      await Promise.race([fired, strip.first().waitFor({ state: 'visible', timeout: options.timeout ?? 10_000 })])
    }
    if (native.message !== null) return { kind: 'native', message: collapse(native.message) }

    await expect(strip, 'one inline confirmation strip').toHaveCount(1)
    const message = collapse((await strip.locator('p').first().textContent()) ?? '')
    if (answer === 'accept') {
      const confirm = strip.locator('[data-inline-confirm-action]')
      if (options.confirmLabel) await expect(confirm).toHaveText(options.confirmLabel)
      await confirm.click()
    } else {
      await strip.getByRole('button', { name: options.cancelLabel ?? 'Cancel', exact: true }).click()
    }
    await expect(strip, 'the strip closes once answered').toHaveCount(0)
    return { kind: 'inline', message }
  } finally {
    // Never leave an answer armed for a later, unrelated dialog.
    state.next = null
    state.onConfirm = null
  }
}

/**
 * No page modal, no dialog, and no native dialog since `watchNativeDialogs` (in-page specs only). The OR-03
 * overlays that keep `role="dialog"` (popovers and pickers anchored to their control, the app-wide drawers:
 * `_NO_PAGE_MODALS_ALLOWED` in `scripts/quality_gate.py`) render only while open, so a spec closes any it
 * opened before calling this.
 */
export async function expectNoOverlay(page: Page): Promise<void> {
  await expect(page.locator('[aria-modal="true"]'), 'no aria-modal element').toHaveCount(0)
  await expect(page.locator('[role="dialog"]'), 'no role="dialog" element').toHaveCount(0)
  expect(listeners.get(page)?.fired ?? [], 'no native dialog fired').toEqual([])
}
