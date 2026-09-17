# EXP-BUG-029 — release webhook retry froze stale review authority

**Mission / severity:** M08, P0.

`release.decided` stored a one-time projection, did not retain its run identity,
used run-wide review lookup, ignored the project draft setting and did not audit
successful unreviewed delivery. It now rechecks the exact pipeline subject on
every attempt, labels permitted drafts, redacts terminal content, and commits
the distribution audit with the token-fenced success transition.

**Green evidence:** exact deployed `77fc9bd2`, build
`build-20260917-062449`, backend digest
`sha256:eff868dbc27852f1518059a470d3764ea730ae2f6ddb743935377341250f053e`;
47 focused tests and six asserted webhook mutations passed. Fixes: `7bdbb5c0`,
`dd89636c`, `77fc9bd2`.
