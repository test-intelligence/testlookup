# Getting started

The shortest path from an empty account to a run you can explore. About ten minutes. Every other page assumes you have done this once.

> **Before you start.** You need a TestLookup URL and an account. Your administrator provides both — this guide never contains credentials. If you are the administrator setting up a fresh deployment, see [Administration](/docs/administration).

## 1. Sign in

Open your TestLookup URL and sign in with the username and password you were given. On success you land on **Home** (the page is titled **Dashboard**).

- **First sign-in with a temporary password.** An account an administrator created for you (or a self-registered one, where registration is enabled) must set its own password first: the **Set your password** page asks for a new password (at least 8 characters), then sends you back to sign in with it.
- **Two-factor authentication.** If your account uses it, a second step (**One more step**) asks for the code from your authenticator app, or **Use a recovery code instead**. If your organisation requires two-factor sign-in and you have not set it up, you are asked to **Set up two-factor authentication** before you can continue. You can turn it on yourself any time under **My profile**.
- **Single sign-on.** When your organisation uses SSO, the sign-in page shows **Sign in with SSO**. If SSO is required, password sign-in is only available to administrator accounts.
- **No account?** Ask an administrator to create one under **Users**. If your organisation has turned self-registration on, the sign-in page also shows **Register**; such an account starts as a Viewer in no project, so it sees no data until an administrator adds it to a project and sets its role.

After 10 failed attempts in a row (the default), an account is locked for 15 minutes. If sign-in fails, check with your administrator that the account is active — see [Troubleshooting](/docs/troubleshooting#permission-and-access-problems).

## The first-run checklist

**? → Getting started** (`/getting-started`) is a setup checklist for the selected project. Each step has a **Set Up** button that opens the page where you do it, and is ticked when the app sees it done, not when you click through:

| Step | Ticked when |
|---|---|
| **Create a Project** | As soon as the project exists |
| **Upload Test Results** | The project has at least one run |
| **Connect Jira** | The Jira integration is enabled |
| **Connect Telemetry** | Splunk, OpenShift or Slack is enabled |
| **View Run Intelligence** | You have opened a run's **Analysis** tab (the step keeps the page's old name) |

Steps you do not need can be skipped (**Skip this step**, undone with **Restore**); skipped steps count as done. The checklist stays available from the Help menu after it is complete.

A project with no runs at all also shows a **Welcome to TestLookup** guide on **Home**, with the same first steps and copy-paste upload commands. Dismissing it hides it for that project only.

## 2. Create or pick a project

A **project** is the isolation boundary. Runs, tests, failures, defects and search results belong to exactly one project, and users only see projects they are a member of.

- **Admin → Project → Projects → New Project** creates one (`QA_LEAD` or higher). Give it a name and a slug (the slug cannot be changed later); a Jira project key and an OpenShift namespace are optional. You become a member, and the project gets a default QA-lead account that unassigned failures fall back to.
- Already have projects? Pick one from the project selector in the header. Below `QA_LEAD`, ask a QA lead or administrator to create a project or add you to one.

### "All Projects" mode

The selector also offers **All Projects**, which aggregates across every project you can see. Two things to know:

- Some pages are **project-scoped** and stay empty in All Projects mode until you pick one. If a page looks blank, check the selector first — it is the most common cause.
- Aggregate numbers in All Projects mode span projects with different suites and cadences. They are useful for spotting outliers, less useful as a single "quality score".

> **Note.** A project that has been deleted is *soft-deleted* — its rows remain but it disappears from listings, and its runs stop appearing in run lists. If historical runs vanished, ask an administrator whether the project was deleted.

## 3. Send your first test run

There are three routes in. Pick one. Sending results needs the `QA_ENGINEER` role or higher.

### Option A — upload a file (fastest to try)

Open **Runs** and click **Upload report**, then drop a report your suite already produces (JUnit XML, TestNG, Allure, Cypress, Playwright, pytest, Robot, Cucumber, NUnit, TRX, xUnit, or a zipped archive, up to 50 MB). TestLookup detects the format from the content. In **All Projects**, the dialog asks which project the run belongs to. See [Getting results in](/docs/ingestion#upload-a-report-from-the-app).

Upload is on for a new install. If there is no **Upload report** button, an administrator has turned it off (the `manual_upload` feature flag); use option B or C, or ask them to turn it back on.

### Option B — POST a JSON batch (good for a first API test)

`POST /api/v1/ingest` accepts a batch directly. `project_id`, `build_number` and `results` are required; each result needs `test_name` and `status`.

```bash
curl -X POST "$TESTLOOKUP_URL/api/v1/ingest" \
  -H "X-API-Key: $TESTLOOKUP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<your-project-uuid>",
    "build_number": "local-1",
    "framework": "junit",
    "results": [
      {"test_name": "checkout_completes", "status": "PASSED", "duration_ms": 120, "suite_name": "Checkout"},
      {"test_name": "checkout_declines_expired_card", "status": "FAILED", "duration_ms": 90,
       "suite_name": "Checkout", "error_message": "AssertionError: expected 402, got 500"}
    ]
  }'
```

A successful call returns **202 Accepted** — the work is queued, not finished. See step 4.

### Option C — upload a report file over the API

```bash
curl -X POST "$TESTLOOKUP_URL/api/v1/ingest/file" \
  -H "X-API-Key: $TESTLOOKUP_API_KEY" \
  -F "project_id=<your-project-uuid>" \
  -F "build_number=local-1" \
  -F "file=@target/surefire-reports/TEST-CheckoutTest.xml"
```

### Getting an API key

**My API keys** (open **My profile** from the account menu, then **My API keys** in the side navigation), or `POST /api/v1/keys`; any `QA_ENGINEER` or higher can create one. An administrator can also issue a project-bound key for CI under **Admin → Project → Streaming API keys**. The key is shown **once** — store it in your CI secret store immediately. Ingestion endpoints accept either an `X-API-Key` header or a normal signed-in session, so you can experiment from the browser before wiring CI. See [Getting results in](/docs/ingestion#api-keys).

> **Warning.** Never commit an API key, paste it into a ticket, or put it in a build log. Treat it like a password. See [Security and privacy](/docs/security).

## 4. Confirm it arrived

Ingestion is asynchronous — `202` means *accepted for processing*, not *stored*. Verify:

1. Open **Runs**. Your `build_number` should appear within a few seconds.
2. Open the run. You should see per-test rows with statuses and durations.
3. If you sent a failure, it should appear in the failure views for that project.

Via the API:

```bash
curl -s -H "X-API-Key: $TESTLOOKUP_API_KEY" \
  "$TESTLOOKUP_URL/api/v1/runs?project_id=<your-project-uuid>&size=10"
```

The response is a page object: `{"items": [...], "total": N, "page": 1, "size": 10, "pages": N}`.

> **Tip.** Run listings default to the **last 30 days**. If you back-fill older history and cannot see it, pass `days=0` for all time.

## 5. Look around

With one run in, the useful next stops are:

- **Runs → your run** — per-test outcomes, durations, error text.
- A failed test — its category and the evidence behind it. See [Investigating failures](/docs/failure-analysis).
- **Dashboards** — mostly empty after one run; they need history. See [Dashboards and metrics](/docs/dashboards).

> **Important.** Most of TestLookup's value is comparative — flakiness, trends and regression detection all need history. One run tells it very little. Flakiness scoring, for example, reports nothing at all below **5 observations** of a test.

## Common first-run problems

| Symptom | Most likely cause | What to do |
|---|---|---|
| `202` but no run appears | Still queued, or the file parsed to zero results | Wait a few seconds, refresh Runs; check the format is supported |
| Upload rejected as empty | Zero-byte file (a wrong path in `-F file=@…`) | Check the file has content locally |
| Run appears, no test rows | Report parsed but contained no cases | Open the report and confirm it has test entries |
| Page is blank, no error | Project selector on **All Projects**, or a time window with no data | Pick a project; widen the window |
| `401` from the API | Missing/expired key or session | Re-issue the key; confirm the header is `X-API-Key` |
| Suite names look wrong | Suite absent from the report and inferred | See [Getting results in](/docs/ingestion#suite-name-resolution) |

More in [Troubleshooting](/docs/troubleshooting).

## The minimal end-to-end loop

```mermaid
flowchart LR
  A[Sign in] --> B[Create or pick a project]
  B --> C[Send a run<br/>upload, API or CI]
  C --> D{Run visible<br/>in Runs?}
  D -->|no| E[Check format,<br/>window, project scope]
  E --> C
  D -->|yes| F[Open a failure]
  F --> G[Read category + evidence]
  G --> H[Confirm or correct it]
```

**In words:** sign in, choose a project, send a run, and check it appears under Runs. If it does not, the usual causes are an unsupported format, a time window that excludes it, or the wrong project selected. Once the run is visible, open a failure, read the category and the evidence attached to it, and confirm or correct the classification.

## Where to go next

- **Wire it into CI** — [Getting results in](/docs/ingestion)
- **Learn the vocabulary** — [Concepts and terminology](/docs/concepts)
- **Understand the outputs** — [How decisions are made](/docs/decisions)
- **Task recipes** — [Step-by-step workflows](/docs/workflows)
