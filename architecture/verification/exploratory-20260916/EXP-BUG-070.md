# EXP-BUG-070 — Mutation accepted an unrelated review subject

The action ledger treated any accepted review with a matching broad context as
authority. It now requires the exact project, kind, report, subject type and
pipeline proposed by the mutating agent, plus the agent's current `act` mode.
Regression and mutation evidence is in M11.
