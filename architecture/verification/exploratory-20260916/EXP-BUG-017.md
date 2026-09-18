# EXP-BUG-017 — release source regression inspected its own prose

**Mission / requirement / severity:** M06, reliable release regression suite,
P2.

The read-axis test helper attempted to remove a production function docstring
by replacing `fn.__doc__` inside `inspect.getsource(fn)`. Python dedents the
former while the latter retains source indentation, so replacement was a
no-op. Correct code then failed because explanatory prose named the two wrong
SQL shapes that the assertions reject.

**Red evidence:** the broadened release suite on exact deployed `3dc242cb`
passed 809 tests and failed the two predicate-source regressions on docstring
text, despite the executable predicate using the indexed denormalized column.

**Green evidence:** commit `5d8dd8a579d5dbfd047f71c683d25007f78d5a52`
locates only a real leading function docstring through the AST and removes its
source line span. After exact homelab deployment, the same broad suite passed
811 tests.

**Mutation:** restoring the old `__doc__` string-replacement helper was killed
by both original regressions. The harness asserts one replacement, a 60-second
child bound, and byte restoration.

**Independent review:** APPROVE on `5d8dd8a5`; reviewed tree
`563e7c11e7f1ff22668bda0c71b577968204c668` and diff
`e662fc07c8122564ceaa4321099101c186a5c216`.
