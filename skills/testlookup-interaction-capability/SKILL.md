---
name: testlookup-interaction-capability
description: Review TestLookup usability, accessibility, and interaction quality. Use for task discovery, learnability, controls, error recovery, feedback, inclusivity, assistance, and self-explanatory browser or CLI workflows.
---

# TestLookup Interaction Capability

## Follow the user task

- Define the role, start state, user intent, and expected durable outcome. Use the relevant workflow in `user-guide` and read the active controls, help text, and error paths in the candidate. Include the normal state and a documented refusal, invalid input, or slow operation when applicable.
- Check whether labels and route context make the next action discoverable. Verify learning and task completion for a new user, feedback and useful progress during asynchronous work, and recovery through clear messages or inline help. For high-impact actions, check that the user can understand the scope and outcome before confirming.
- Check inclusivity using the supported interaction contexts. In the browser, inspect accessible names and roles, focus order and restoration, keyboard-only operation, and text or layout when zoomed. Verify that important state has a readable label as well as any visual encoding. For CLI workflows, check help output and actionable errors.

## Retain task evidence

- Use `frontend/tests/ci-e2e/accessibility-keyboard.spec.ts` for examples of keyboard checks and [Frontend quality](../testlookup-frontend-quality/SKILL.md) for the appropriate test lane. Use a real API readback when the task promises persistence or delivery.
- Record the workflow, role, environment, interaction method, observed completion, usability blocker or observable error, and fixture evidence. Tie any timing or accessibility acceptance criteria to the selected profile and check.
