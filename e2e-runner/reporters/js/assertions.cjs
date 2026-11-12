const assert = require('node:assert/strict')

exports.passing = () => assert.equal(2 + 2, 4)
exports.failing = () => assert.equal(2 + 2, 5, 'deliberate reporter fixture failure')
