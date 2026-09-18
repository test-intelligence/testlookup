# EXP-BUG-015 — concurrent release overrides lost an audit entry

**Mission / requirement / severity:** M06, immutable override history, P1.

`apply_override()` read, appended, and reassigned a JSON audit list without
locking its `ReleaseDecision`. Two QA transactions could read the same list,
commit different verdicts, and silently retain only the last writer's audit
entry. The service now obtains `SELECT ... FOR UPDATE` before reading the row.

**Red evidence:** against exact deployed revision `d65eabdf`, the controlled
PostgreSQL overlap committed both override requests but retained one audit
entry. The unit SQL contract also failed without the lock.

**Green evidence:** commit `b7387dee2d7611fa66fbee9421d469854cf19942`
serialized both transactions. The final candidate
`5d8dd8a579d5dbfd047f71c683d25007f78d5a52` was deployed as
`build-20260917-013906`; its real PostgreSQL suite passed all five journeys,
including the two-override overlap. The final broad release, policy, council,
and workflow suite passed 811 tests.

**Mutation:** `scripts/mutation_check_exploratory_m06_override_race.py` asserts
that the lock removal applies exactly once, restores the source bytes, and the
PostgreSQL-aware regression kills it. One mutation was killed.

**Independent review:** APPROVE on `b7387dee`; reviewed tree
`2a0d5391a7ce3404d87f20f03ad2966be93685a6` and diff
`3cfd1800342849fa8ad41b2c31be342083f3dd8b`.
