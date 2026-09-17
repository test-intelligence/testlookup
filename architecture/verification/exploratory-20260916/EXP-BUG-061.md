# EXP-BUG-061 — Provider failures produced complete stub generations

Empty output, import errors, timeouts and exceptions returned persistable stub
cases. These paths now raise an explicit unavailable error and the API returns
503 without a committed batch. Regression and mutation evidence is in M10.

