---
name: testlookup-flexibility
description: Assess TestLookup adaptability, installability, portability, configurability, and replaceability. Use for deployment profiles, feature flags, upgrades, dependency changes, and capacity adaptation.
---

# TestLookup Flexibility and Portability

## Define the supported profile

- Read `docs/operations/deployment.md`, `docs/operations/configuration.md`, and `docs/operations/feature-flags.md`. Identify the supported profile, relevant configuration sources and precedence, dependencies, and data carried from an existing deployment. Name the particular configuration, environment, or provider that changes.
- Compare the default and selected variant at the candidate revision. Verify effective settings, migration or state assumptions, startup and readiness, then check one task end to end. Check the documented response to an unsupported or incomplete configuration using fixture values.

## Verify installation and substitution

- For install or portability work, choose representative source and target profiles. Record prerequisites from the profile docs, deploy with a disposable fixture, and compare the same contract and artifacts across profiles. Include a supported upgrade or rollback when the change affects persisted state.
- For a dependency or provider replacement, identify the boundary contract and verify state compatibility, configuration migration, fallback, and rollback. Use [Deployment and operations](../testlookup-deployment-quality/SKILL.md) for the profile-specific rollout and [Scalability](../testlookup-scalability/SKILL.md) for capacity adaptation.
- Record the profile and version matrix, configuration or dependency changes, expected invariants, verified outcomes, and any remaining portability or compatibility constraint.
