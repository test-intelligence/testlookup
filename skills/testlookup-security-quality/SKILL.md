---
name: testlookup-security-quality
description: Review and improve TestLookup security architecture and defensive regression coverage. Use for trust boundaries, authentication and project authorization, data handling, integration egress, and release supply-chain controls.
---

# TestLookup Security Quality

## Map the trust boundary

- Read `THREAT_MODEL.md`, `docs/architecture/security.md`, `DATA_AND_PRIVACY.md` and `SECURITY.md` at the candidate ref. Map actor and credential type, action, project and resource, data sensitivity, expected outcome and enforcement point. Include the configured deployment profile and any asynchronous or external effect.
- Inspect `backend/app/bootstrap.py`, `backend/app/core/deps.py` and domain guards for REST. Trace authorization through live producers and subscribers, MCP caller identity in `mcp/token_verifier.py`, and worker authority at execution and delivery. Check membership and resource ownership, API-key scope, token lifecycle and the treatment of stale scope or policy.
- Follow uploads and external data through parser bounds, validation, storage, rendering and export. Check secret handling, redaction where an artifact is created, offline and destination policy at delivery, and least privilege for runtime identities. Read rendered defaults alongside the startup checks and the enabled feature set.
- When a review needs a standard control mapping, record the applicable requirement ID and version alongside the TestLookup control and its evidence. [OWASP ASVS](https://asvs.owasp.org/)

## Verify remediation

- Use source review, static scan evidence and focused defensive unit or integration tests. In an authorized disposable setting, verify the corrected policy with synthetic users, projects and data. Check a permitted operation, the expected structured refusal, and durable state or an isolated sink for the promised side effect.
- Check supply-chain evidence for the intended SHA and digest. Inspect `.github/workflows/secret-scan.yml`, `scripts/test_ci_security.py`, `scripts/release/scan_release_images.py` and the release workflow. Record which control ran, what artifact it covered and how any open dependency or deployment finding was evaluated.
- Record the affected boundary, source or sanitized evidence, affected actors and data, impact, severity, confidence and current status. Identify the corrective change, owning component, regression test and acceptance condition. Mark deployment-dependent conclusions as unverified until the rendered profile or its observed evidence confirms them.
