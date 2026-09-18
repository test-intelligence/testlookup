# EXP-BUG-111 — Feature-flag scopes could not be cleared

**Mission / severity:** M18, P1.

The settings UI documented an empty allow-list as unrestricted and sent null,
but the service interpreted null as no update. Explicit null or an empty list
now clears project and role restrictions; omission remains the keep operation.
