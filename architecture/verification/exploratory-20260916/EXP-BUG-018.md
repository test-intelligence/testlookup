# EXP-BUG-018 — review settlement could escape its evidence and lock boundary

**Mission / requirement / severity:** M07, one durable settlement bound to the
exact reviewed evidence, P1.

Review creation read the live subject without a row lock and changed a pending
row's evidence hash in place. A stale browser could therefore accept bytes that
had replaced the evidence shown under the same review id. Settlement also took
the review lock before Finalize's pipeline-then-review order, allowing a
deadlock with concurrent supersession. After a lock wait, SQLAlchemy could keep
the preliminary row's stale state in its identity map and report the wrong
loser result.

Creation now locks the stable parent scope plus live and older subjects,
supersedes changed evidence, and mints a new pending review id. Ordinary and
Investigator finalizers take the parent before their child row, matching
retention/reset cascade order. Settlement takes the pipeline lock first, then
reselects the review with `populate_existing=True`.

**Red evidence:** `c3d66890` failed the evidence-identity and row-lock unit
contracts. The first real PostgreSQL accept/reject race then exposed the stale
identity-map response before `b8b46e71` added the forced refresh.

**Green evidence:** exact deployed `bbce4f5f` passed 15 real PostgreSQL race and
resume tests plus the focused review suite. Accept/reject produced one durable
settlement; accept/supersede preserved the winner and new subject; changed
evidence retained its old hash on the superseded id.

**Mutation:** six applicable asserted mutations removed parent-scope, subject,
or older-scope locking, reused the old review id, removed the pipeline-first
lock, or removed ORM refresh. Two additional mutations removed ordinary or
Investigator parent-first finalization. All were killed and the harness restored
each source file byte-for-byte.
