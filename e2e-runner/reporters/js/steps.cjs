const { Then } = require('@cucumber/cucumber')
const { passing, failing } = require('./assertions.cjs')
Then('the passing assertion holds', passing)
Then('the failing assertion holds', failing)
