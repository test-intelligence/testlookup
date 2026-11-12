# Data and privacy

TestLookup is self-hosted: it runs on infrastructure you operate, and you are
the controller of the personal data in your instance. This page describes what
the software stores, what can leave your network and how you control both. It
describes how the software behaves; it is not legal advice.

## What TestLookup sends to the project

Nothing. TestLookup's own code has no telemetry, analytics, crash reporting or
update check. The web UI reports browser errors and page-timing metrics to your
own backend, which writes them to its log.

The bundled Ollama container runs with `OLLAMA_NO_CLOUD=1`. Without it, Ollama
calls ollama.com at startup and about every four hours (model recommendations
and the cloud model list) and serves cloud models. If you point TestLookup at an
Ollama server of your own, set the same variable there.

## What is stored

1. **Accounts:** e-mail, username, optional full name, bcrypt password hash,
   role, MFA state (the TOTP seed encrypted, recovery codes hashed), last
   sign-in, failed sign-in count and lockout time.
2. **Audit and security records:** who changed what and, for sign-in and
   identity events, the IP address.
3. **Content people create:** comments, reviews, mentions, Ask AI conversations
   and answers, test cases, release decisions, saved views and notes.
4. **Test results you send:** test names, error messages, stack traces, logs,
   attachments and CI metadata such as committer or CI actor names. This holds
   personal data whenever your tests or logs do.

Short-lived cache entries can hold e-mail addresses and usernames, and the
server logs hold request details.

**Where:** PostgreSQL, MongoDB, S3-compatible object storage (versitygw in the
bundled stack), Valkey (cache and task queue), ChromaDB if you enable search
embeddings, and your container logs.

**Redaction:** TestLookup removes common secret and personal-data patterns
(tokens, passwords in URLs, e-mail addresses, phone and card numbers, IP
addresses) from the test text it stores in its databases, but it cannot catch
every secret in free text. The original files you upload, with their
attachments, are kept unchanged in object storage until retention removes them.

## What can leave your network, and only if you configure it

- **AI:** prompts to a hosted model (OpenAI, Gemini, Anthropic, OpenRouter) or to
  a model server you run (Ollama, LM Studio, LocalAI, vLLM or another
  OpenAI-compatible server). OpenRouter also receives `OPENROUTER_SITE_URL` and
  the app name as attribution headers.
- **Integrations:** Jira, Confluence, GitHub, GitLab, Slack, Microsoft Teams,
  outbound webhooks, e-mail through your SMTP server, Splunk, OpenShift and
  Prometheus.
- **Infrastructure:** sign-in through your SAML or OpenID Connect provider,
  traces to an OpenTelemetry collector you name, and secrets read from a
  HashiCorp Vault you name.
- **Installation:** container images (ghcr.io and Docker Hub), the install script
  (GitHub) and any model you pull. The offline bundle avoids all three.
- **Search embeddings with offline mode off:** the bundled embedder downloads its
  model weights (about 79 MB) on first use. Put them in `CHROMA_ONNX_MODEL_DIR`
  to avoid the download.

### Offline mode

`AI_OFFLINE_MODE=true` is the default. While it is on:

- every hosted model is refused;
- a local model server must resolve to a loopback or private address
  (link-local and cloud-metadata addresses are refused);
- Jira, Confluence, GitHub, GitLab, outbound webhooks, Splunk, OpenShift and
  Prometheus look-ups are switched off entirely, even on a private address;
- Slack, Teams and e-mail reach only private addresses or the hosts you list in
  `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`, and e-mail goes only to the domains in
  `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS`;
- SSO sign-in, an OpenTelemetry collector and a Vault secret store are not
  affected.

A setting saved in the UI cannot switch offline mode off; only the deployment's
environment can.

## Your controls

- **Export:** anyone can download everything TestLookup holds about them from
  their profile, and an administrator can export it for any person (GDPR
  Articles 15 and 20).
- **Erasure:** people can ask to be erased, and an administrator can erase a
  person. Erasure pseudonymises the account everywhere except audit records and
  signed compliance packs, which keep the stored name (Article 17(3)).
- **Retention:** policies for raw events, runs, artifacts and some audit
  records, **off until you enable them**. Identity events (which hold sign-in IP
  addresses) and the settings audit log are never purged by TestLookup; prune
  them yourself if your policy requires it. AI analysis history is capped per run
  and per test, and agent memory expires after 365 days
  (`AGENT_MEMORY_RETENTION_DAYS`).
- **Audit:** administrators can export up to 365 days of audit records as CSV,
  with redaction applied, and compliance packs are signed.
- **Access:** roles and project membership limit who sees what.
- **Secrets:** stored credentials are encrypted with a key derived from
  `APP_SECRET_KEY`, or kept in Vault.
- **Outbound TLS:** `HTTP_CA_BUNDLE` and `HTTP_VERIFY_TLS`.

The [compliance guide](user-guide/compliance.md) covers each control in detail.

## Third parties

If you enable a hosted model, an issue tracker or a chat integration, that
provider processes the data you send it under its own terms, and you need your
own agreement with it. TestLookup is provided under the Apache License 2.0
without warranty and makes no claim of GDPR or any other certification.
