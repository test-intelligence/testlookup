const { test } = require('@playwright/test')
const { passing, failing } = require('./assertions.cjs')
test('release_pass', passing)
test('release_fail', failing)
