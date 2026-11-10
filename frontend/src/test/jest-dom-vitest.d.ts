/**
 * jest-dom's matcher types for Vitest 5 (W45).
 *
 * `src/test/setup.ts` imports `@testing-library/jest-dom`, whose types add the matchers (`toBeInTheDocument`,
 * `toHaveTextContent`, …) to the global `jest.Matchers<R, T>`. Vitest 4's assertion type extended that global
 * interface, so the matchers type-checked; Vitest 5's `Assertion<R, T>` extends its own `Matchers<R, T>` instead, and
 * jest-dom 7.0.1 (the newest) still augments the old one-parameter `Assertion<T>` — 7,345 errors without this file.
 * Delete it once jest-dom ships Vitest 5 typings. Types only: the matchers themselves are registered at run time by the
 * setup import, as before.
 */
import 'vitest'

declare module 'vitest' {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- an interface merge is how Vitest takes matchers
  interface Matchers<R extends void | Promise<void> = void | Promise<void>, T = unknown> extends jest.Matchers<R, T> {}
}
