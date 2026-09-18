# M19 — Accessibility, UX and browser lifecycle

## Result

**PARTIAL — two keyboard-accessibility defects are fixed.** Exact deployment
authority and final verification results are recorded after candidate testing.

## What was proved

- Release creation/editing, release run linking, claim correction, and evidence
  inspection dialogs take focus, wrap Tab and Shift+Tab, close on Escape, and
  restore the invoking control.
- Icon-only release dialog close controls have accessible names.
- The shared critical-route browser checks exercise skip navigation, main
  landmark focus, modal focus containment, and serious/critical axe findings on
  Projects and Reviews.

## Verification

Pending exact-candidate deployment and final checks.

## Defects fixed

EXP-BUG-114 and EXP-BUG-115.

## Deviations and remaining gaps

The available browser runtime covers Chromium only. Firefox, WebKit, a real
screen reader, authenticated end-to-end keyboard journeys, duplicate-tab edit
conflicts, cached-chunk replacement, RTL content, theme contrast, and the full
375px/768px/200% zoom matrix remain unproved. Automated axe checks are limited
to serious and critical findings and do not establish WCAG conformance. Other
lower-priority hand-written dialogs still need migration to the shared focus
contract. These gaps keep M19 partial.
