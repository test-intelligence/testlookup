"""The HTTP-exchange capture vocabulary (W23d AGT-14), in ONE place.

A reporter that opts in (``--testlookup-capture-http``) sends the HTTP
requests / responses each test made beside its result. They are
attacker-controlled input from an API key: every exchange is cut to the caps
below, then redacted INSIDE the Pydantic model (``schemas.HttpExchangeIn`` →
``http_exchange_redaction.redact_exchange``), so the object-storage copy of a
JSON batch, the Celery payload and the Mongo document only ever hold the
redacted form; the store re-validates (idempotent) and the SDK applies the
same rules first.

The caps, denylists and patterns ALSO live in ``sdk-vectors/http_exchange.json``
(the cross-language specification the Python SDK — and, later, the Java / JS /
Go SDKs — build against); ``tests/test_payload_metrics_vocab_parity.py`` pins
that file and ``frontend/src/types/httpExchanges.ts`` equal to this module.

Absence is not health: a test without exchanges reads "No HTTP exchanges were
captured for this test.", never an empty success.

Constants only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

EXCHANGE_DOC_VERSION = 1
EXCHANGES_PER_TEST_MAX = 20            # more in one result / one live post → the first 20 kept, the rest counted "dropped"
EXCHANGES_PER_RUN_MAX = 1_000          # JSON batch: enforced in IngestPayload (result order); live: counted in Mongo before insert
BODY_WIRE_MAX_BYTES = 65_536           # a body is parsed / fingerprinted up to this many UTF-8 bytes; beyond → cut first
BODY_STORED_MAX_BYTES = 16_384         # what is stored and shown, AFTER redaction; beyond → cut + marker
TRUNCATION_MARKER = "…[truncated: {omitted} bytes not stored]"
HEADERS_MAX = 50                       # per side; more → the first 50 (sorted by name), counted
HEADER_NAME_MAX, HEADER_VALUE_MAX = 128, 2_048
URL_MAX, ENDPOINT_MAX = 2_048, 300
METHOD_RE = r"^[A-Z]{1,16}$"           # upper-cased first; anything else → the exchange is dropped (never a 422)
SCHEMA_KEYS_MAX = 200                  # top-level keys fingerprinted per response
FIELD_NAME_RE = r"^[A-Za-z_$@][A-Za-z0-9_.$@:-]{0,119}$"   # a response key outside this is never stored as a NAME (it is
                                       # counted in "_unnamed_keys"): no sentence can travel to a stage summary as a field name
CAPTURE_SOURCES = ("json_batch", "live")
SCHEMA_STATES = ("parsed", "not_json", "empty", "too_large")   # too_large = the wire body exceeded BODY_WIRE_MAX_BYTES
HEADER_DENYLIST = (                    # compared lower-case; the VALUE becomes "[REDACTED]", the name is kept
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key", "x-auth-token",
    "x-access-token", "x-csrf-token", "x-xsrf-token", "x-amz-security-token", "x-goog-api-key",
    "x-session-token", "private-token", "x-gitlab-token", "x-hub-signature", "x-hub-signature-256",
)
HEADER_SECRET_SUFFIXES = ("token", "secret", "password", "api-key", "apikey", "signature", "credential")   # a name ENDING in one
QUERY_DENYLIST = (                     # query parameter names (lower-case) whose VALUE becomes "[REDACTED]"
    "token", "access_token", "id_token", "refresh_token", "api_key", "apikey", "key", "code", "password",
    "secret", "client_secret", "sig", "signature", "x-amz-signature", "x-amz-credential", "x-amz-security-token", "auth",
)
ENDPOINT_ID_SEGMENT_RES = (            # a path segment matching any of these becomes "{id}" when the SDK sent no template
    r"^\d+$", r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    r"^[0-9a-fA-F]{24}$", r"^[A-Za-z0-9_-]{16,}$",
)
DRIFT_BASELINE_RUNS = 5                # previous runs of the project that captured the same (method, endpoint)
DRIFT_BASELINE_DOCS_MAX = 100          # docs read for one baseline (newest first)
LIST_PAGE_MAX = 20                     # GET items per test (== EXCHANGES_PER_TEST_MAX)
REDACTED = "[REDACTED]"                # == redaction_service.REDACTED (parity test)
CONTRACT_SENTENCES = {                 # the contract stage's summary / violation text (UI copy, not prompt text)
    "no_capture": "No HTTP exchanges were captured for these failures; enable capture in the reporter to validate API contracts.",
    "no_baseline": "{endpoint}: no earlier run of this project captured it, so there is nothing to compare with yet.",
    "missing_field": "field `{field}` missing from {method} {endpoint}; drift since build {build}",
    "type_changed": "field `{field}` in {method} {endpoint} changed from {was} to {now} since build {build}",
    "body_not_checked": "{method} {endpoint}: the response was not JSON or not complete, so its fields were not compared.",
    "no_drift": "No API contract drift: {checked} responses match the previous runs' fields.",
}
EXCHANGE_PANEL_SENTENCES = {           # G-D renders these verbatim
    "none": "No HTTP exchanges were captured for this test.",
    "how": "Capture is opt-in per run: pytest --testlookup-capture-http (or TESTLOOKUP_CAPTURE_HTTP=1).",
    "redacted": "Secrets in headers, query strings and bodies are replaced with [REDACTED] before they are stored.",
    "truncated": "Body cut to 16 KB when stored.",
    "dropped": "{dropped} more exchanges of this test were not stored (limit 20 per test, 1 000 per run).",
}
