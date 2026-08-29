# Action Register — 2026-08-28-01

| ID | Severity | Status | Owner area | Finding | Evidence | Recommended action | Validation expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| QA-001 | P1 | Resolved | Build/deploy | No container engine or usable remote build credentials on this workstation | Homelab preflight | Build cumulative branch image and run homelab deploy script | New image tag, all workloads Ready, health/readiness/version match commit | Completed with Podman and homelab registry |
| QA-002 | P2 | Deferred | Test infrastructure | Windows Bash subprocess harness fails independently of product code | Full backend baseline | Run CI-faithful Linux suite or repair Bash/WSL selection | Full suite has no unexplained failures | CI or host tooling change |
| QA-003 | P2 | Mitigated / follow-up | Release engineering | Deployed image reports unknown provenance | `/health/version` after `build-20260829-015431` | Add BUILD_REVISION/BUILD_DATE injection in a follow-up release-engineering change; retain immutable tag/digest evidence for this run | Revision equals merged commit SHA | Follow-up build-pipeline change |
| QA-004 | P2 | Deferred | Frontend tests | Docs copy-control test times out only in full suite | Full frontend baseline vs isolated 36/36 | Reproduce and stabilize test timing/isolation | Full frontend suite green | CI resources/test cleanup |
| QA-005 | P1 | Resolved / in progress | GitHub/release | GitHub CLI token was invalid and Git had no usable HTTPS credential | Elevated `gh auth status` confirms `anandtopu` with `repo` and `workflow` scopes | Publish the reviewed branch and open the review/merge path | Remote branch/PR exists and required checks pass | Remote checks |
