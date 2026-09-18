# EXP-BUG-081 — Frozen clamps disclosed refused endpoint hostnames

Endpoint residency failures stored `base_url` in the clamp field and copied the
provider exception containing the refused hostname into the durable invocation
snapshot. The clamp now records a generic endpoint field and bounded policy
reason. A regression freezes a configuration with one refused and one usable
tier and proves no URL, hostname, API-key label or `base_url` survives.
Regression and mutation evidence is in M11.
