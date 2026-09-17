# EXP-BUG-065 — Retrieved instructions remained active prompt text

Retrieved chunks were inserted verbatim. They are now sanitized and enclosed in
explicit untrusted-evidence boundaries that deny instruction or tool authority.
Regression and mutation evidence is in M10.

