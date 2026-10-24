# Checkout v2 — product requirements

Owner: Payments squad · Target release: 2026.11

## Overview

Checkout v2 replaces the three-step checkout with a single page, moves card
capture to the new payment SDK and adds Apple Pay. This document describes the
change and how it is tested before it ships.

## Goals

- One-page checkout for every market.
- No drop in conversion during the rollout.

## Requirements

1. Card capture goes through the payment SDK's hosted fields.
2. Apple Pay is offered on Safari and iOS.
3. A failed authorisation keeps the basket.

## Smoke testing

Run the **checkout-smoke** suite against every release candidate build, on
staging data. A red smoke run stops the candidate.

- Exit criteria: all smoke tests pass on two consecutive builds

## Regression (5 days)

The full `checkout-e2e` and `payments-api` suites, plus the
[refund regression pack](https://wiki.example.test/refunds), on the release
candidate. Flaky tests are re-run once.

## UAT

Finance and customer support walk the new flows with real test cards in the
sandbox, over 2 weeks.

Exit criteria:
- Finance signs off the reconciliation report
- No open P1 or P2 defects
- Support has the new refund macro

## Production cutover

Flip the `checkout_v2` flag for 5% of traffic, then 50%, then 100% over three
working days, watching the conversion dashboard.

## Risks

- The payment SDK's hosted fields may slow the first paint.
- Apple Pay merchant validation needs the production certificate.

## Open questions

- Do we keep the old checkout behind a flag for one more release?
