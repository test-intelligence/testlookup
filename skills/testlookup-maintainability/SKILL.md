---
name: testlookup-maintainability
description: Review TestLookup modularity, reuse, analysability, modifiability, and testability. Use for dependency changes, extension points, refactors, diagnostics, and long-term evolvability.
---

# TestLookup Maintainability

## Trace the expected change

- Read `architecture/DEVELOPER_GUIDE.md` and the owning component skill. Identify the expected maintenance task, current boundary and source of truth, users of the public contract, and affected data or deployments. Compare the actual modules and interfaces touched with the intended scope.
- Verify that the owner can make the change in a predictable place. For a new parser, integration, or report format, follow its documented extension points and check ownership of validation, persistence, and presentation. Reuse shared contracts and cover supported callers when moving shared logic.
- Check analysability by following a failing fixture through the relevant service, error category, and correlation evidence. Identify which state, configuration, and versions would allow another maintainer to reproduce the normal functional failure on a disposable fixture.

## Verify test seams and evolution

- Use deterministic fixtures at the narrowest stable boundary. Check that public behavior has direct assertions and that testing a generated or cross-client contract uses the current source. Use the applicable checks in `scripts/quality_gate.py`, `scripts/test_quality_gate.py`, and `docs/operations/testing.md`.
- Record the change scenario, affected modules and contracts, migration or version risks, relevant guards and test outcomes, and remaining maintenance work. Link the component and [Compatibility](../testlookup-compatibility/SKILL.md) skills for changes spanning supported clients.
