# EXP-BUG-057 — Projectless chat crossed tenant and budget scope

Non-admin sessions could omit `project_id`; downstream queries interpreted that
as all projects. The router and UI now require a project while Admin retains its
explicit unrestricted path. Regression and mutation evidence is in M10.

