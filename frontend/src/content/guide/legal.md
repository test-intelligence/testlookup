# Legal notice

TestLookup is free, open-source software under the Apache License, Version 2.0. This page restates what that license already says, in plain words, and adds what it means for a tool you run yourself. The license is the binding text: where this page and the license differ, the license wins.

## Free, open-source software

- **License.** TestLookup is licensed under the [Apache License 2.0](https://github.com/test-intelligence/testlookup/blob/main/LICENSE). You may use, copy, change and redistribute it under that license's terms.
- **Copyright.** Copyright 2026 TestLookup Contributors.
- **Who maintains it.** One maintainer and volunteer contributors, in public. There is no support contract and no service-level agreement. Help is best effort, through the channels in [SUPPORT.md](https://github.com/test-intelligence/testlookup/blob/main/SUPPORT.md).
- **Where the license travels.** Every TestLookup container image carries `LICENSE`, `NOTICE` and `THIRD_PARTY_NOTICES.md` under `/licenses`, and the web app serves the licenses of its bundled JavaScript at `/THIRD_PARTY_LICENSES.txt`. An air-gapped instance has its copy without reaching GitHub.

## No warranty

TestLookup is provided **"as is"**. The maintainer and contributors make no promise that it works, that it is free of defects or security flaws, that its results are correct, or that it suits your purpose. You decide whether it is fit for your use, and you carry the risks of using it.

Section 7 of the license says, word for word:

> 7. Disclaimer of Warranty. Unless required by applicable law or agreed to in writing, Licensor provides the Work (and each Contributor provides its Contributions) on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied, including, without limitation, any warranties or conditions of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. You are solely responsible for determining the appropriateness of using or redistributing the Work and assume any risks associated with Your exercise of permissions under this License.

If you redistribute TestLookup and choose to offer your own support, warranty or indemnity, Section 9 of the license says you do so on your own behalf and on your sole responsibility, not on behalf of the maintainer or any other contributor.

## No liability

The maintainer and contributors are not liable for damages of any kind that come from using TestLookup, or from being unable to use it. That covers, for example:

- **Failures:** crashes, outages, failed upgrades, or a pipeline blocked or let through by a gate.
- **Wrong results:** a wrong verdict, root cause, flaky score, classification or AI-written explanation, and any decision made on it.
- **Data loss:** test results, settings or reports lost, corrupted or deleted, including by retention, purge or restore.
- **Data leaks:** data exposed through a vulnerability, a misconfiguration, or an integration you turned on.
- **Misuse:** use by you, your users, or anyone who gains access to your instance, for any purpose.
- **Anything else:** direct, indirect, special, incidental or consequential damages, including lost work, lost time, computer failure and commercial losses.

Section 8 of the license says, word for word:

> 8. Limitation of Liability. In no event and under no legal theory, whether in tort (including negligence), contract, or otherwise, unless required by applicable law (such as deliberate and grossly negligent acts) or agreed to in writing, shall any Contributor be liable to You for damages, including any direct, indirect, special, incidental, or consequential damages of any character arising as a result of this License or out of the use or inability to use the Work (including but not limited to damages for loss of goodwill, work stoppage, computer failure or malfunction, or any and all other commercial damages or losses), even if such Contributor has been advised of the possibility of such damages.

Both sections apply "unless required by applicable law". Some laws do not allow every warranty or liability to be excluded, for example liability for deliberate or grossly negligent acts, or rights that consumer law grants. Where such a law applies, the exclusions above apply as far as that law allows.

## Your deployment and your data

TestLookup is self-hosted. The project does not run it for you, has no hosted service, and has no access to your instance or its data. Whoever installs and runs an instance is responsible for it, including:

- **Security:** network exposure, TLS, secrets, sign-in, roles and who gets an account.
- **Data:** what your test results contain. Error messages, stack traces and logs often carry hostnames, internal URLs, credentials or personal data; see [Security and privacy](/docs/security).
- **Backups and recovery:** taking backups and testing that they restore.
- **Updates:** applying upgrades and security fixes.
- **Integrations:** an AI provider, issue tracker, chat tool or webhook you connect receives the data you send it, under that service's own terms. Offline mode, on by default, blocks calls to services outside your network until an administrator turns it off.
- **The law:** meeting the privacy, data-protection and industry rules that apply to the data you put in.

## Results support decisions; you make them

Verdicts (GO, CONDITIONAL_GO, NO_GO), risk scores, failure categories, flaky scores and AI-written explanations are aids to your judgement. Rules can be misconfigured, models can be wrong, and AI text can be confidently wrong. A person on your team decides whether to ship, and owns that decision. [How decisions are made](/docs/decisions) shows the evidence behind each result, so you can check it.

## No certification

TestLookup is not certified, audited or attested against SOC 2, ISO 27001, GDPR, HIPAA or any other standard. Its compliance packs and control mappings help you collect evidence about your own processes. They do not make your deployment compliant.

## The public demo

The live demo at `https://demo.testlookup.app` is a shared, public instance with sample data, and it is reset every night. Do not enter real data, personal data or secrets there. It is provided as is, like the software, and it may be offline, slow or changed at any time.

## Third-party software and trademarks

- **Third-party components.** TestLookup includes open-source components under their own licenses; `NOTICE` and [THIRD_PARTY_NOTICES.md](https://github.com/test-intelligence/testlookup/blob/main/THIRD_PARTY_NOTICES.md) list them. Services it runs alongside, such as PostgreSQL, MongoDB, Redis, MinIO and Ollama, keep their own licenses and terms.
- **Other names.** JUnit, pytest, TestNG, Allure, Cypress, Playwright, Jira, GitHub, GitLab, Slack and the other product names in this guide are trademarks of their owners. They appear only to say what TestLookup works with, and imply no endorsement or affiliation.
- **The TestLookup name and logo.** The license grants no right to use them, except for reasonable and customary use in describing where the software comes from (Section 6 of the license).

## Reporting a security problem

Report a vulnerability privately, through GitHub private vulnerability reporting, as the [security policy](https://github.com/test-intelligence/testlookup/blob/main/SECURITY.md) describes. Do not open a public issue for it.

## Contributions

A contribution you submit for inclusion is licensed under the Apache License 2.0 (Section 5 of the license). Contributors sign off each commit under the Developer Certificate of Origin (`git commit -s`); there is no separate contributor license agreement. [CONTRIBUTING.md](https://github.com/test-intelligence/testlookup/blob/main/CONTRIBUTING.md) has the details.

## Not legal advice

This page is a plain-language summary for users of TestLookup. It is not legal advice and creates no contract. For a question about your own situation, ask a qualified lawyer.
