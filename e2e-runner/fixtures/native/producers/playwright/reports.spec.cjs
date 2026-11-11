const { test, expect } = require('@playwright/test')
test('passes', () => expect(2 + 2).toBe(4))
test('fails', () => expect(1).toBe(2))
test.skip('skips', () => {})
