# Trying TestLookup in a codespace

This codespace runs the whole app with demo data: three sample projects with
30 days of test history, failure clusters, flaky tests and release decisions.

1. **Wait for the first build.** It takes a few minutes. Progress shows in the
   terminal (`postStartCommand`). Check on it with
   `docker compose -f docker-compose.dev-lite.yml ps`.
2. **Open port 3000.** Codespaces opens it for you when it is ready. You can
   also open it from the **Ports** tab.
3. **Sign in with one click.** The sign-in page offers quick sign-in as Admin,
   QA Lead, QA Engineer, Tester or Viewer. No password is needed in this
   development setup.

This copy is yours: you can change anything, upload your own results
(`POST /api/v1/ingest`) and try every feature.

**Keep port 3000 private.** Codespaces forwards it as private (only you,
signed in to GitHub, can open it), which is the default. Do not switch it to
**Public** in the Ports tab: the one-click sign-in needs no password, so anyone
with the link could sign in as Admin.

**Cost:** the codespace runs on your GitHub account's Codespaces allowance.
Free accounts get a monthly quota, and a 4-core machine uses it faster. Stop
the codespace when you are done, or delete it from
<https://github.com/codespaces>.

For a quick look without running anything, the
[guided tours](https://testlookup.app/demo/first-run/) replay real screens in
your browser.
