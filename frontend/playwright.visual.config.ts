/**
 * Visual-regression baselines for the DEV-only chart gallery (`/__charts`).
 *
 *   npm run test:visual          compare against the committed baselines
 *   npm run test:visual:update   re-baseline (deliberately, after a review)
 *
 * NOT part of `test:e2e:ci` yet. Baselines are rendered by a separate CI job
 * on Linux; the `{platform}` segment in the snapshot path keeps a Windows or
 * macOS workstation's pixels (different font rasteriser, different
 * anti-aliasing) from ever being compared with — or committed beside — them.
 * The repo's ignore rules admit only `__screenshots__/linux/`.
 *
 * Everything about the server is the CI e2e config's, reused rather than
 * copied, so the two suites can never drift to different ports or flags.
 */
import { defineConfig, devices } from '@playwright/test'
import ciConfig from './playwright.ci.config'

export default defineConfig({
  ...ciConfig,
  testDir: './tests/visual',
  // Anything that can move a pixel is pinned: one viewport, no animation, no
  // caret, no hover (the mouse starts off-page), fixed data on the page itself.
  snapshotPathTemplate: '{testDir}/__screenshots__/{platform}/{arg}{ext}',
  expect: {
    ...ciConfig.expect,
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      scale: 'css',
    },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
  ],
})
