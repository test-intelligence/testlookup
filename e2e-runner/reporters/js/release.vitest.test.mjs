import { test } from 'vitest'
import assertions from './assertions.cjs'
test('release_pass', assertions.passing)
test('release_fail', assertions.failing)
