import { expect, test } from '@playwright/test'

const sum = (left: number, right: number): number => left + right

test('release_pass', () => expect(sum(2, 2)).toBe(4))
test('release_fail', () => expect(sum(2, 2)).toBe(5))
