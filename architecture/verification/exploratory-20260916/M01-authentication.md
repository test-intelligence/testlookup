# M01 authentication

**State:** RUNNING.
**Stable environment:** homelab tag `build-20260912-012628`, schema `0172`,
source revision unreported.
**Contract oracle:** `architecture/testing/EXPLORATORY_MISSIONS.md` M01 and the
current authentication routes/tests.

The existing live Playwright authentication specifications are being audited
before execution so mocked route tests are not counted as deployed-system
evidence.

Two predeployment diagnostics were run against the older stable deployment:
the real happy-path login passed in Chromium, Firefox, and WebKit, and the
mixed session-flow file reported 15 passes. The first attempt used the ingress
IP without the required host name and timed out in global setup; the corrected
host-name run passed. The failed-login case in the mixed file intercepts the
login endpoint and therefore remains component evidence only.

These results are **invalid for candidate completion** because they preceded a
deployment of this branch and the stable backend is at schema `0172`. Per the
owner's 2026-09-16 direction, M01 stays RUNNING and every qualifying mission
test will be rerun only after the exact candidate commit is serving on the
homelab with schema `0189`.
