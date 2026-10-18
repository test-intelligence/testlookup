# Ask AI

**Ask AI** (`/chat`) answers questions about one project's test results in plain language: what failed and why, whether a test is flaky or a regression, what changed between builds, whether the project is ready to release. You can ask follow-up questions in the same conversation.

> **Important.** An answer is a suggestion to verify, not a verdict. It is written by a language model from the data it looked up, and every answer shows which lookups it made and links to the builds and tests it names, so you can check it. See [How decisions are made](/docs/decisions).

## When Ask AI is available

Ask AI needs a language model. It appears in the sidebar (under **ASSIST**) only when both of these hold:

- the `ask_ai_chat` feature flag is on (it is on by default), and
- analysis runs in **Auto** or **LLM (AI Agent)** mode (**Settings → AI → AI configuration**).

In **Rules** or **Machine Learning** mode the entry is hidden; if you open `/chat` directly, the page says chat is unavailable in that mode instead of offering the question box. An administrator changes the mode. The model itself must also be reachable: a local model, or a hosted provider when offline mode is off.

You also need one project selected. In **All Projects** the page asks you to select a project first, so every answer stays inside one project.

## Asking a question

1. Open **Ask AI** in the sidebar.
2. Pick one of the starter questions under **Try asking** (what failed, what changed, flaky tests, release readiness, a standup summary), click **Ask about this run** next to a recent run, or type your own question and press Enter (Shift+Enter for a new line).
3. Watch the answer arrive. The assistant first says what it is looking up (for example "Reading this project's latest runs…"), then writes the answer as it goes. **Stop** ends it early.

From a run's page, **⋯ → Ask AI about this run** opens Ask AI on that run's project with the question "What failed in this build, and why? Are any of these failures new since the previous build?" already typed. Press Enter to send it, or edit it first.

Questions are limited to 4,000 characters.

## Reading an answer

Under every answer:

- **AI-suggested** — a reminder that the text is model-written. No confidence score is shown.
- **How I looked this up** — the lookups the assistant made and what each one found. Read this when an answer surprises you.
- **Links** to every build and test the answer names, so you can open the evidence.
- The model used, how long the first words took, and how long the whole answer took.

The assistant can look up, for the selected project only: the recent runs, a run's failures, one test's history across runs, a comparison between two builds, the flaky-test list and quarantine state, failure clusters, the release-gate verdict, what earlier analyses found, and counts of failure kinds. All lookups are read-only.

## Suggested actions

An answer may end with up to three suggested actions, such as **Propose quarantine: <test>** or **Create Jira issue: <test>**. They are built from what the lookups found, not from the model's wording. Clicking one opens the usual form inside that answer, filled in (one at a time per answer). **Nothing is changed until you submit that form yourself**, with the permissions your role already has.

## When an answer fails

- **Retry** asks again without retyping, when the failure was temporary (the model timed out or could not be reached). Partial text from a failed answer is not saved.
- After **Stop**, **Regenerate** asks for a new answer to the same question.
- A question that never got an answer shows **Answer it now**.
- "This project's AI budget for the month is used up" means the project's LLM cost budget is spent; see [Settings and administration](/docs/administration#llm-cost-budget).

Answers are bounded on purpose: a limited number of lookup rounds, a wait of up to 45 seconds for the first words, and up to two minutes for the whole answer. Administrators can change these limits (the `CHAT_*` settings).

## Conversations

The **Conversations** list on the left keeps your conversations in the current project, newest first. **New chat** starts another. A conversation is private to you (administrators can open any); other project members cannot see it. Hover a conversation and use the bin icon to delete it.

Each conversation is bound to the project it started in. Switching project shows that project's conversations.

## What it does not do

- It never reads outside the selected project, and it cannot be asked to: the project is fixed by the server, not by the question.
- It never changes anything on its own.
- It does not replace the evidence: open the linked builds and tests before acting on an answer.

The same project data is available to external AI assistants through the MCP server; see [API, CLI, SDKs and MCP](/docs/integrations#mcp-server).

## Related

- [AI agents and the pipeline](/docs/ai-agents)
- [Runs and run detail](/docs/runs)
- [Security and privacy](/docs/security#what-leaves-the-deployment)
