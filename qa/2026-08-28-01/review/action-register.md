# Action Register — 2026-08-28-01

| ID | Severity | Status | Owner area | Finding | Evidence | Recommended action | Validation expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| QA-001 | P1 | Blocked | Build/deploy | No container engine or usable remote build credentials on this workstation | Homelab preflight | Build cumulative branch image and run homelab deploy script | New image tag, all workloads Ready, health/readiness/version match commit | Container engine or authenticated CI/build host |
| QA-002 | P2 | Deferred | Test infrastructure | Windows Bash subprocess harness fails independently of product code | Full backend baseline | Run CI-faithful Linux suite or repair Bash/WSL selection | Full suite has no unexplained failures | CI or host tooling change |
| QA-003 | P2 | Open | Release engineering | Deployed image reports unknown provenance | `/health/version` | Inject revision/date in image build and verify | Revision equals merged commit SHA | QA-001 |
| QA-004 | P2 | Deferred | Frontend tests | Docs copy-control test times out only in full suite | Full frontend baseline vs isolated 36/36 | Reproduce and stabilize test timing/isolation | Full frontend suite green | CI resources/test cleanup |
