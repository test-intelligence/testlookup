# EXP-BUG-004 — MCP pods lacked their Deployment component label

**Mission / requirement / severity:** M26, exact candidate deployment
authority, P1.

The second candidate rollout passed backend authority verification and then
failed closed for MCP. The MCP Deployment object declared
`app.kubernetes.io/component: mcp-server`, but its pod template did not. The
verifier therefore could not select pods using the Deployment-derived identity.

The pod template now propagates the same component label. This preserves the
strict verifier and makes MCP pods unambiguously attributable to the workload.

**Regression test:**
`backend/tests/regression/test_homelab_build_authority.py::test_mcp_deployment_carries_component_label_into_its_pods`
failed with a missing label before the fix and now requires the Deployment and
pod-template values to agree.

**Mutation:** the shared M26 harness removes the pod-template label and proves
the regression fails. All 18 mutations must be killed.

**Independent review:** APPROVE. The reviewer confirmed the immutable selector
remains `app=testlookup-mcp`, the Recreate strategy is unchanged, the manifest
passes a Kubernetes client-side dry run, focused tests and Ruff pass, and all
18 mutations are killed. The reviewed tracked diff hash was
`14f135eed037b61e990a5458c9d6c8a9be5dd3f4`. Deployed E2E remains pending.
