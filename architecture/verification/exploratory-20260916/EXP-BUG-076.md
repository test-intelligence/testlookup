# EXP-BUG-076 — MCP lost configuration override and snapshot evidence

MCP invocation did not carry the additive tighten-only override and its status
view omitted the sanitized frozen configuration. Both are now preserved and
rendered without credentials or base URLs. Regression and mutation evidence is
in M11.
