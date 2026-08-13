import { ReactNode } from 'react'

/** One numbered onboarding step, optionally with a copy-paste command. */
export interface Step {
  n: number
  title: string
  body: ReactNode
  command?: string
}

/**
 * The CLI `upload` command's `-p` flag takes the project **ID** (see
 * `cli/testlookup_cli/commands/upload.py`). When the guide is scoped to a
 * concrete project we splice that id in so the copy button yields a
 * runnable command; in "All Projects" mode (no single id) we keep the
 * `<project-id>` placeholder. `-b <build>` stays a placeholder — the build
 * label is per-run and only the user knows it.
 */
export function uploadCommand(projectId?: string): string {
  const p = projectId && projectId.trim() ? projectId : '<project-id>'
  return `testlookup upload file results.xml -p ${p} -b <build>`
}

/** Build the three first-run steps, scoping the upload command to `projectId`. */
export function buildSteps(projectId?: string): Step[] {
  return [
    {
      n: 1,
      title: 'Load the demo dataset',
      body: 'Spin up a fully populated instance — sample runs, failures, flaky tests, trends, and a release gate — in a few minutes.',
      command: 'make quickstart',
    },
    {
      n: 2,
      title: 'Or ingest your own test results',
      body: 'Point your CI at the ingest API (JUnit / TestNG / Allure / Cypress / Playwright / pytest), or upload a file from the CLI.',
      command: uploadCommand(projectId),
    },
    {
      n: 3,
      title: 'Then explore the intelligence',
      body: 'Once a run lands, dig into clustered failures, the flaky coach, and the release-risk gate.',
    },
  ]
}
