# EXP-BUG-068 — Notification HTML trusted stored markup and URLs

Title, body, project/build values and dashboard URL were interpolated into HTML.
Values are escaped and dashboard links require absolute HTTP(S). Regression and
mutation evidence is in M10.

