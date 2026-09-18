# EXP-BUG-116 — API-key outage appeared as an empty key list

**Mission / severity:** M20, P1.

The API Keys page discarded its SWR error after a toast and rendered the
ordinary “No API keys yet” state. It now renders the retryable unavailable
state and does not make a false claim about stored credentials.
