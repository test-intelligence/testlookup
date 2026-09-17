# EXP-BUG-020 — separation of duties followed mutable live agent mode

**Mission / requirement / severity:** M07, producer/reviewer separation for
act-mode proposals, P1.

Review settlement resolved an ordinary workflow's proposing mode from the live
project config. Changing an agent from `act` to `suggest` after it proposed a
mutation let the requester approve its own output; the inverse change could
block a report that was proposal-time suggest mode.

Settlement now resolves ordinary workflow and invocation proposals only from
the agent configs frozen in pipeline execution metadata. A missing proposer or
unidentifiable legacy snapshot fails closed.

**Red evidence:** `c3d66890` accepted producer self-review after a frozen act
proposal was paired with mutable suggest mode.

**Green evidence:** exact deployed `bbce4f5f` passed the focused separation,
review API, and action-ledger suites.

**Mutation:** treating frozen act mode as non-mutating, skipping canonical id
mapping, ignoring the invocation snapshot, and dropping it at finalization were
all killed. The harness baseline-runs every selector and restores source bytes.
