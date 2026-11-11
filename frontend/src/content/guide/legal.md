# Legal notice

TestLookup is free, open-source software under the Apache License, Version 2.0. This page points to what that license says about warranty and liability. The license is the binding text: where this page and the license differ, the license wins.

## Free, open-source software

- **License.** TestLookup is licensed under the [Apache License 2.0](https://github.com/test-intelligence/testlookup/blob/main/LICENSE). You may use, copy, change and redistribute it under that license's terms.
- **Copyright.** Copyright 2026 TestLookup Contributors.
- **Who maintains it.** One maintainer and volunteer contributors, in public. There is no support contract and no service-level agreement. Help is best effort, through the channels in [SUPPORT.md](https://github.com/test-intelligence/testlookup/blob/main/SUPPORT.md).
- **Third-party software.** TestLookup includes open-source components under their own licenses. `NOTICE` and [THIRD_PARTY_NOTICES.md](https://github.com/test-intelligence/testlookup/blob/main/THIRD_PARTY_NOTICES.md) list them.
- **Where the license travels.** Every TestLookup container image carries `LICENSE`, `NOTICE` and `THIRD_PARTY_NOTICES.md` under `/licenses`, and the web app serves the licenses of its bundled JavaScript at `/THIRD_PARTY_LICENSES.txt`. An air-gapped instance has its copy without reaching GitHub.

## No warranty

TestLookup is provided **"as is"**, without warranties or conditions of any kind. Section 7 of the license says, word for word:

> 7. Disclaimer of Warranty. Unless required by applicable law or agreed to in writing, Licensor provides the Work (and each Contributor provides its Contributions) on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied, including, without limitation, any warranties or conditions of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. You are solely responsible for determining the appropriateness of using or redistributing the Work and assume any risks associated with Your exercise of permissions under this License.

## No liability

The maintainer and contributors are not liable for damages from using TestLookup or from being unable to use it, such as failures, data loss, data leaks or misuse, unless required by applicable law. Section 8 of the license says, word for word:

> 8. Limitation of Liability. In no event and under no legal theory, whether in tort (including negligence), contract, or otherwise, unless required by applicable law (such as deliberate and grossly negligent acts) or agreed to in writing, shall any Contributor be liable to You for damages, including any direct, indirect, special, incidental, or consequential damages of any character arising as a result of this License or out of the use or inability to use the Work (including but not limited to damages for loss of goodwill, work stoppage, computer failure or malfunction, or any and all other commercial damages or losses), even if such Contributor has been advised of the possibility of such damages.

## Your instance

TestLookup is self-hosted. The project has no hosted service and no access to your instance or its data. Your team installs and runs it, including its security, backups and updates.

- **What your data holds.** Test results often contain hostnames, internal URLs, credentials or personal data. [Security and privacy](/docs/security) covers what TestLookup stores and what can leave your network.
- **Results are aids, not decisions.** Verdicts (GO, CONDITIONAL_GO, NO_GO), risk scores, failure categories, flaky scores and AI-written explanations can be wrong. A person on your team decides whether to ship. [How decisions are made](/docs/decisions) shows the evidence behind each result.
- **No certification.** TestLookup is not certified, audited or attested against any security, privacy or industry standard.

## The public demo

The live demo at `https://demo.testlookup.app` is a shared, public instance with sample data, and it is reset every night. Do not enter real data, personal data or secrets there. It can be slow, offline, reset or changed at any time.

## Reporting a security problem

Report a vulnerability privately, through GitHub private vulnerability reporting, as the [security policy](https://github.com/test-intelligence/testlookup/blob/main/SECURITY.md) describes. Do not open a public issue for it.

## Not legal advice

This page is a plain-language pointer to the license for users of TestLookup. It is not legal advice. For a question about your own situation, ask a qualified lawyer.
