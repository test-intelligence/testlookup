# M20 — Keys, MFA/SSO/SCIM and permission changes

## Result

**PARTIAL — two API-key UI defects are fixed.** Exact deployment authority and
final verification results are recorded after candidate testing.

## What was proved

- API-key listing outages are visibly distinct from a valid empty list and
  expose an explicit retry.
- A one-time raw key and its generated setup snippets are removed when project
  authority changes; the original project label remains bound to the secret
  until removal.
- The one-time key dialog takes and contains focus, closes on Escape, and
  restores the invoking control.
- Existing focused identity suites cover project-bound key confinement, review
  mutation refusal, key mint/revoke races, MFA enrollment/challenge/recovery,
  SAML validation/replay controls, SCIM scope/patch/deprovision behavior, role
  and membership authority, session refresh, and audit events.

## Verification

Pending exact-candidate deployment and final checks.

## Defects fixed

EXP-BUG-116 and EXP-BUG-117.

## Deviations and remaining gaps

The shared homelab has no dedicated IdP/test tenant, disposable identity
accounts, or notification sink. Live MFA enrollment/recovery, SAML callback,
SCIM provisioning/deprovision, invitation delivery, concurrent key revoke/use,
and old-tab permission-change exercises are therefore blocked. Safe local tests
exercise their contracts without contacting real recipients. These gaps keep
M20 partial.
