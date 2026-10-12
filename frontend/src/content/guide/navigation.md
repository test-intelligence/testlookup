# Finding your way around

Where everything lives in the app: the sidebar, the top bar, the Help menu, the settings pages, and what happens to old links.

## The sidebar

The sidebar is grouped into sections. What you see depends on your role: a viewer, tester or QA engineer sees eleven items; a QA lead or administrator also sees **Releases** and **Admin**.

| Item | Opens | Tabs on the page | Who sees it |
|---|---|---|---|
| **Home** | `/overview` | | Everyone |
| **Inbox** | `/my-failures` | Assigned to me, Approvals | Everyone |
| **Runs** | `/runs` | History, Live, Compare | Everyone |
| **Failures** | `/failures` | Failures, Defects, Root cause (AI) | Everyone |
| **Flaky tests** | `/flaky` | Detected, Proposed, Quarantined, History | Everyone |
| **Trends** | `/trends` | Trends, Coverage, Explorer | Everyone |
| **Suites** | `/suites` | | Everyone |
| **Test cases** | `/test-management` | | Everyone |
| **Reports** | `/reports/summary` | Summary, Value | Everyone |
| **Release gate** | `/release-gate` | | Everyone |
| **Releases** | `/releases` | Releases, Gate policies | QA lead and administrator |
| **Ask AI** | `/chat` | | Everyone, when Ask AI is available (see below) |
| **Admin** | `/settings` | | QA lead and administrator |

The section labels above the items are **INVESTIGATE** (Runs, Failures, Flaky tests), **QUALITY** (Trends, Suites, Test cases, Reports), **RELEASE** (Release gate, Releases) and **ASSIST** (Ask AI). **Home** is titled **Dashboard** once open.

Two items carry a live badge: **Inbox** shows how many failures are assigned to you, and **Runs** shows how many runs are streaming right now.

**Ask AI** appears only when the `ask_ai_chat` feature flag is on (it is on by default) and analysis runs in **Auto** or **LLM** mode. See [Ask AI](/docs/ask-ai#when-ask-ai-is-available).

## The top bar

- **Project selector** — picks the project every page shows. Administrators also get **All Projects**, which aggregates across every project they can see; everyone else always has one project selected (the first one they belong to, until they pick another). Some pages stay empty in All Projects until you pick one; see [Getting started](/docs/getting-started#all-projects-mode).
- **Release picker** — **All releases**, one release, or **— Unattributed —** (runs not attached to any release). It narrows the report pages to that release and is kept in the page address (`?release=`), so a shared link opens the same view. It is disabled in All Projects, because a release belongs to one project.
- **Search** — type and press Enter to search tests, runs and defects; press `/` anywhere to jump to the box. See [Search and discovery](/docs/search).
- **Notifications bell** — your recent notifications with an unread count, **Mark all read**, and a link to your notification settings.
- **Help (?)** — see below.
- **Account menu** — your name and role, **My profile**, **My notifications**, the colour theme, **Presentation mode** (larger text and stronger contrast for a wall monitor or projector) and **Sign out**.

### The time window

There is no time-window control in the top bar: each page that needs one has its own window picker (24h, 7d, 14d, 30d, 90d). They all share one setting, so the window you choose on one page is the window the next page opens with. It starts at **30 days** and is remembered in your browser. A page that does not offer your current window (Live stops at 30 days) uses the nearest one it has.

## The Help menu

The **?** in the top bar opens:

- **Help for this page** — opens the section of this guide that covers the page you are on, in a side panel, without leaving the page.
- **Getting started** — the first-run checklist (`/getting-started`).
- **Documentation** — this guide (`/docs`).
- **Chart guide** — [Charts and visual analysis](/docs/charts).
- **Troubleshooting** — [Troubleshooting](/docs/troubleshooting).
- The build you are running, with a copy button for bug reports.

## Settings and admin pages

**Admin** in the sidebar opens the settings index (`/settings`). It lists every settings page you are allowed to open, in groups: **Account**, **Project**, **Release governance**, **Integrations**, **AI**, **Security & access** and **System**. Each settings page also shows the same groups as a side navigation.

Your own pages — **Profile**, **My notifications** and **My API keys** — are under **Account**. **My profile** and **My notifications** are also in the account menu, so every role can reach them. Every page in the index is described in [Settings and administration](/docs/administration).

## Old links still work

Pages that were merged in the October 2026 redesign keep their old addresses. Bookmarks, tickets and chat messages that use them land on the new place:

| Old address | Now opens |
|---|---|
| `/intelligence` | **Runs** (`/runs`) — the AI verdict is a column of the runs table |
| `/runs/<id>/intelligence` | the run's **Analysis** tab (`/runs/<id>?tab=analysis`) |
| `/deep-investigate/<run id>` | the run's **Analysis** tab |
| `/agents/run/<run id>` | the run's **Evidence** tab (`/runs/<id>?tab=evidence`) |
| `/flaky-coach` | **Flaky tests** (`/flaky`), Detected tab |
| `/quarantine` | **Flaky tests**, Quarantined tab (`/flaky?tab=quarantined`) |
| `/reviews` | **Inbox**, Approvals tab (`/my-failures?tab=approvals`) |
| `/coverage/suite?name=<suite>` | that suite's page under **Suites** |

## Related

- [Getting started](/docs/getting-started)
- [Settings and administration](/docs/administration)
