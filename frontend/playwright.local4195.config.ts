// LOCAL ONLY (temp, G-E W27b phase 2): the CI e2e config on :4195; deleted after use.
import base from './playwright.ci.config'
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  ...base,
  outputDir: 'C:/Users/anand/AppData/Local/Temp/claude/C--Users-anand-Downloads-Projects-testlookup-new/a16cc674-845c-4cba-b384-98d9271df065/scratchpad/w27bge-results',
  use: { ...base.use, baseURL: 'http://127.0.0.1:4195' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], channel: 'chrome' } }],
  webServer: {
    ...(base.webServer as object),
    command: 'npm run dev -- --host 127.0.0.1 --port 4195 --strictPort',
    url: 'http://127.0.0.1:4195/login',
    reuseExistingServer: false,
  },
})
