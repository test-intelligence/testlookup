# Upload caller recovery

The CLI and report-upload form preserve a 503 with a receipt as an uncertain
original upload. They poll its task identity through the configured API and
never fetch the supplied `Location` as an arbitrary URL. A valid receipt needs
both UUID headers, `X-Upload-Task-ID` and `X-Upload-Run-ID`. Incomplete marked
receipts fail visibly and cannot fall back to automatic multipart replay.

This corrects a concrete cross-file gap: the original server fix retained a
broker-accepted payload after a lost acknowledgement and exposed its original
receipt, while the CLI still treated every 503 as pre-admission shedding. A
synthetic first 503 followed by 202 previously produced two POSTs and discarded the
first task ID. The file endpoint intentionally allocates independent identities
for independent requests. The browser similarly surfaced that 503 as a generic
upload error and offered another upload without preserving its receipt.

The revised regression exercises one POST followed by original-ID GETs, including
transient poll failures, timeout and terminal failure. A poll response that names
a different task/run is refused. HTTP distinguishes safe retry behavior from
an ambiguous POST outcome. [RFC 9110's idempotent-method guidance](https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods)
supports retaining identity and reconciling an uncertain non-idempotent request.

## CLI

`upload file` creates one UUID `Idempotency-Key` before its retry loop. Existing
429/receiptless 503 backoff keeps that key and the form metadata. A known receipt
switches to bounded polling even without `--wait`, using the original IDs.
Success requires a succeeded status, terminal failure remains nonzero, and
pending confirmation expires nonzero with the original IDs in JSON/YAML.
The confirmation budget is the remaining publication retry budget, capped at
five minutes and reduced by a shorter `--wait-timeout` when selected.

A completely lost HTTP response produces a diagnostic with the retained key and
no automatic repost. Explicit `--idempotency-key` permits an intentional rerun
with identical file, options, account and project on a server implementing this
deployment's optional upload receipt contract. Keys use
`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`. `upload dir` generates a distinct key per
file invocation. Intentional recovery of a particular file uses `upload file`
with its retained key.

Default ordinary 202 output and `--wait` behavior stay compatible. The CLI does
not automatically invoke the server's explicit receipt retry endpoint.

## Browser

The service recognizes the same 503 receipt and starts the existing processing
poll flow. The form keeps an explicitly unconfirmed state until progress is
observed. At its bounded poll timeout it shows **Upload confirmation pending**
and **Check status**. That action resumes GET polling without another POST or a
success toast.

While the form stays open, an uncertain network response retains the selected
file objects, exact bundled archive bytes, generated blank build label and
idempotency key. This matters because rebundling can change ZIP timestamps and
generating another blank build label changes accepted metadata. A changed file
selection, target project or upload fields starts a new key. A terminal
success/failure ends the attempt. Closing/reloading the form ends this local
state. Recovery across a browser restart is a separate concern.

Requests continue through the existing Axios API instance and its authentication
and origin policy. UUIDs construct the polling path. The HTTP `Location` value
is never followed. The upload form owns its inline diagnostics, and transient
status polling does not raise repetitive global error toasts.

## SDK and deployment boundaries

The bundled Python, JavaScript, Java and Go reporters use the streaming session
or streaming-ingest protocols. Their batches already create `batch_id` once
before retry, preserve event order and reuse that identity. Their existing
protocol regressions cover this distinct contract. This file-upload change does
not modify SDK delivery behavior or introduce a client disk spool.

Deploy the receipt-aware server contract and CORS allowance for
`Idempotency-Key` with these clients. A browser using an explicitly cross-origin
API needs that header allowed and its receipt headers exposed. The usual
same-origin proxy path remains supported. Older servers may ignore the optional
key, so this client does not infer safe automatic lost-response replay from
merely sending it. On current servers a key is scoped to project, actor and
route kind, and changed content conflicts. The server's terminal receipt replay
horizon is 30 days. Pending/unknown receipts are not automatically expired.
Retention expiry ends that deduplication guarantee.

The local regressions use synthetic responses and files. They establish caller
behavior and preserve ordinary shedding retries. Database-backed server replay,
cross-worker single processing and deployed proxy/CORS behavior require their
separate integration acceptance.
