# EXP-BUG-063 — Large source selections bypassed source filtering

Selecting more than ten sources silently searched every active project source.
Every selected source is now queried explicitly before bounded merge. Regression
and mutation evidence is in M10.

