const { Given } = require('@cucumber/cucumber')
Given('a passing check', function () {})
Given('a failing check', function () { throw Error('release fixture expected failure') })
Given('a skipped check', function () { return 'skipped' })
