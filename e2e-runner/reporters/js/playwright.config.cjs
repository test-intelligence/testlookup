module.exports = {
  testMatch: 'release.playwright.spec.ts',
  workers: 1,
  retries: 0,
  reporter: [['testlookup-reporter/playwright-reporter']],
}
