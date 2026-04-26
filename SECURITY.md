# Security Policy

TestLookup is pre-1.0 open-source software. We take security reports seriously and coordinate with researchers in good faith. This policy describes how to report a vulnerability, what to expect in response, and what's in scope.

## Supported versions

Only the `main` branch and the most recent tagged release receive security fixes during the pre-1.0 period. Once v1.0.0 ships, this policy will be updated to list long-term support branches.

| Version   | Supported                        |
| --------- | -------------------------------- |
| `main`    | :white_check_mark: (best effort) |
| `v0.1.x`  | :white_check_mark: (best effort) |
| `< v0.1`  | :x:                              |

"Best effort" means the maintainers will triage and fix security issues as time permits; there is no contractual SLA while the project is pre-1.0. We do commit to the response targets below.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

Use one of:

1. **Private vulnerability reporting on GitHub.** From the repository's `Security` tab, click `Report a vulnerability`. This creates a private advisory only the maintainers can see.
2. **Email.** Send details to `security@testlookup.app`.

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce (proof-of-concept preferred)
- The affected version or commit SHA
- Any mitigations you've already identified

We will acknowledge receipt within **72 hours**. We aim to provide a status update within **7 days** and, for confirmed critical vulnerabilities, a fix or mitigation within **30 days** of confirmation.

## Scope

In scope:

- Authentication and authorization bypass (JWT, API keys, project scope enforcement)
- Data exfiltration through the REST API, CLI, or MCP server
- PII leakage through log output, LLM prompts, or report rendering
- Secret leakage from `secret_service`-stored values
- Injection vulnerabilities (SQL, command, prompt injection affecting other users' data)
- CSRF, XSS, or clickjacking on the React frontend
- Supply-chain issues in the declared dependencies

Out of scope:

- Denial of service through resource exhaustion of a self-hosted instance (you control the hardware)
- Vulnerabilities in third-party dependencies that have not been exploited via TestLookup-specific code
- Social engineering or phishing of maintainers
- Vulnerabilities requiring physical access to the host machine
- Anything requiring a user to explicitly enable a feature flag that is off by default

## Disclosure

We practice **coordinated disclosure**. When a vulnerability is confirmed, we will work with the reporter to agree on a disclosure timeline. Our default embargo is **90 days from confirmation** or until a fix ships, whichever comes first. We will credit reporters in the release notes and the GitHub advisory unless they prefer to remain anonymous.

## Safe harbour

We will not pursue legal action against researchers who:

- Make a good-faith effort to avoid privacy violations, data destruction, and service interruption during their research
- Report vulnerabilities only through the channels above
- Give us reasonable time to investigate and mitigate before disclosing publicly
- Do not exploit the vulnerability beyond what's necessary to confirm it

This safe harbour applies regardless of whether the researcher is affiliated with an organisation or working independently.
