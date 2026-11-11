# Product screenshots

The images the root README shows. Every one comes from the same capture, so they agree with each other and with the website.

- **When and from what:** 11 November 2026, from the backend and frontend images built at commit `347f5abd`, the head of `main` at the time.
- **Data:** the demo seed plus one demo run a day, so every chart has a value on every day. Test-library activity went through the real API as the generic demo accounts ("QA Lead", "QA Engineer"). There is no personal data.
- **Known limits, shown as they are:** every demo run has the same 16 tests, so execution bars are the same height; one demo test fails in every run, so the 14-day failure strip is red throughout; no background worker ran, so the library panel says no flaky scores have been computed yet.

| File | Panel | SHA-256 |
|---|---|---|
| `trends-pass-rate.png` | Trends › Pass rate trend, 14 days | `f15bdbcf4e9a39b9779b5e08f5703eca43e17729088d710366bd138dff504ac9` |
| `failures-top-failing.png` | Failures › Top failing tests, 14 days | `80004f9aa6d2f028dfee3e6cb95c3d33c0b085bb0bfeaea52ef80db62c69a199` |
| `overview-kpis.png` | Overview › KPI tiles with 30-day sparklines | `20443ab31e9a0f7afa291454762fea9b97a9a38c6467df8bc7d4e4fed2f1883a` |
| `overview-pass-rate-trend.png` | Overview › Pass rate trend, 30 days | `4c9aa126ffff93aab14667444cee38408ffb792d76e6775bf6ab172b89464a35` |
| `suite-run-history.png` | Suite › Run history, 30 days | `92a255c8c2fbf1143de7ae6c9ebbd7d58040d62c139aeaab3b4990fa4bd31062` |
| `test-library-health.png` | Test library › Insights | `e3bc92c02c1929a5bb4d2c947da62327b68dd61eb7552b94e9782121667d02ee` |

`readme-hero.png` stacks `trends-pass-rate.png` over `failures-top-failing.png`. Its source is [`docs/maintainers/launch/source/readme-hero.html`](../../maintainers/launch/source/readme-hero.html); render it at 1040 px wide with any Chromium browser and crop the empty space below.

When the product UI changes, recapture all of them together and update this table, rather than mixing captures from different dates.
