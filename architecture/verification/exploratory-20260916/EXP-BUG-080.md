# EXP-BUG-080 — Fixer compatibility saves omitted the entity tag

The Fixer web adapter called the canonical AgentConfig writer without its
quoted version tag, causing unsafe or rejected writes after concurrency became
mandatory. It now sends the same `If-Match` contract as the general and
Investigator adapters. Regression and mutation evidence is in M11.
