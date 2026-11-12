const register = require('testlookup-reporter/cypress-plugin')
module.exports = {
  video: false,
  screenshotOnRunFailure: false,
  e2e: {
    supportFile: false,
    specPattern: 'release.cy.js',
    setupNodeEvents(on, config) { return register(on, config) },
  },
}
