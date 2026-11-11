import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

const output = process.env.TL_RESULTS
if (!output) throw new Error('TL_RESULTS must name a fresh run directory')

export default defineConfig({
  testDir: './tests/release',
  forbidOnly: true,
  retries: 1,
  workers: 1,
  timeout: 120_000,
  reporter: [
    ['json', { outputFile: resolve(output, 'playwright.json') }],
    ['junit', { outputFile: resolve(output, 'junit.xml') }],
    ['html', { outputFolder: resolve(output, 'playwright-html'), open: 'never' }],
  ],
  outputDir: resolve(output, 'artifacts'),
  use: { baseURL: process.env.TL_WEB, browserName: 'chromium', trace: 'off', screenshot: 'off' },
})
