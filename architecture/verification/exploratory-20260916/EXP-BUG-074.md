# EXP-BUG-074 — Agent configuration writes lost concurrent updates

HTTP and automatic reviewer-quality writes could overwrite a newer document.
The API requires a version entity tag, the PostgreSQL write is conditional, and
the internal writer locks then uses the same expected-version path. Regression
and mutation evidence is in M11.
