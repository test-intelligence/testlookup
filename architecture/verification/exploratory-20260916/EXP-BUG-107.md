# EXP-BUG-107 — Analytics outages appeared as valid empty data

**Mission / severity:** M17, P1.

Value Metrics and Billing could render ordinary empty or zero states when their
requests failed. Both pages now render the common retryable unavailable state;
the routed-page error-state ratchet includes Value Metrics.
