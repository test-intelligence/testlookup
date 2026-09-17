# EXP-BUG-065 — Retrieved instructions remained active prompt text

Retrieved chunks were inserted verbatim. They are now sanitized and serialized
as JSON untrusted-evidence objects that deny instruction or tool authority.
Quotes, newlines, forged tags and section headings remain inside JSON strings.
Regression and mutation evidence is in M10.
