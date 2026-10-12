# Governance

TestLookup is an Apache-2.0 project run by its maintainers in the open. This
document says who decides what, and how to become a maintainer. It is
deliberately small for a young project and will grow with the contributor base.

## Roles

- **Users** run TestLookup and report bugs, ask questions and suggest features.
- **Contributors** submit pull requests, reviews, documentation or triage. Every
  commit is signed off under the Developer Certificate of Origin
  ([CONTRIBUTING.md](CONTRIBUTING.md#licensing-dco)).
- **Maintainers** ([MAINTAINERS.md](MAINTAINERS.md)) review and merge pull
  requests, triage issues, cut releases ([RELEASING.md](RELEASING.md)), handle
  security reports ([SECURITY.md](SECURITY.md)) and enforce the
  [Code of Conduct](CODE_OF_CONDUCT.md).
- The **project lead** is a maintainer who breaks ties and owns the project's
  name, repository settings and release credentials. Today that is the founding
  maintainer listed in MAINTAINERS.md.

## Decisions

- **Day-to-day changes** (bug fixes, docs, refactors, tests) are decided in the
  pull request: one maintainer's approval and a green CI run are enough to
  merge. A maintainer does not merge their own non-trivial change without
  another maintainer's review once there is more than one maintainer.
- **Significant changes** — a new integration, a schema migration, a change to
  authentication or tenant isolation, a new dependency, a change to the MCP
  tool surface or to a public API — start as an issue describing the problem
  and the proposed approach. The decision and its reasons are recorded in the
  issue before the implementation is merged.
- **Disagreements** are resolved by discussion aiming at consensus among the
  maintainers. If consensus cannot be reached in reasonable time, the project
  lead decides and records why.
- **License and governance changes** need the agreement of every active
  maintainer and are announced in the CHANGELOG.

## Becoming a maintainer

Contributors who have made sustained, high-quality contributions — code,
reviews, documentation or triage — over several months can be nominated by any
maintainer. The nomination is discussed by the maintainers and accepted when no
maintainer objects. New maintainers are added to MAINTAINERS.md in a pull
request.

A maintainer who is inactive for six months, or who asks to step down, moves to
the emeritus list in MAINTAINERS.md and can return by asking.

## Code of Conduct

Everyone in a project space follows the [Code of Conduct](CODE_OF_CONDUCT.md).
Maintainers who are party to a report take no part in deciding it.
