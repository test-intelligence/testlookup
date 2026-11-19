// CI-context detection for the JS SDK (GAP-E04): the Go matrix
// (client/go/testlookup/cicontext_test.go) ported, plus the cases the Go
// suite leaves out (pull_request_target, trailing-garbage PR numbers,
// whitespace). The Python SDK, CLI, Go and Java SDKs each test their copy;
// the JS copy (src/ci-context.ts) had no test, and no shared vector file
// would catch drift. detectCiContext/resolveCiContext take the env as an
// argument, so the matrix runs without touching process.env; the last test
// checks the session-create payload a real reporter sends.
import assert from 'node:assert/strict'
import test from 'node:test'

import { detectCiContext, resolveCiContext } from '../dist/ci-context.js'
import { TestLookupReporter } from '../dist/testlookup-reporter.js'
import { clearTestLookupEnv, installFakeServer } from './fake-server.mjs'

const GITHUB_PR = {
  GITHUB_ACTIONS: 'true',
  GITHUB_REPOSITORY: 'acme/webapp',
  GITHUB_EVENT_NAME: 'pull_request',
  GITHUB_REF: 'refs/pull/421/merge',
  GITHUB_ACTOR: 'octocat',
  GITHUB_SERVER_URL: 'https://github.com',
  GITHUB_RUN_ID: '99',
}

const MATRIX = [
  {
    name: 'github actions pull request',
    env: GITHUB_PR,
    want: {
      ci_provider: 'github_actions',
      ci_repo: 'acme/webapp',
      pr_number: 421,
      ci_actor: 'octocat',
      ci_run_url: 'https://github.com/acme/webapp/actions/runs/99',
    },
  },
  {
    name: 'github actions pull_request_target reads the PR number too',
    env: { ...GITHUB_PR, GITHUB_EVENT_NAME: 'pull_request_target', GITHUB_REF: 'refs/pull/422/merge' },
    want: {
      ci_provider: 'github_actions',
      ci_repo: 'acme/webapp',
      pr_number: 422,
      ci_actor: 'octocat',
      ci_run_url: 'https://github.com/acme/webapp/actions/runs/99',
    },
  },
  {
    name: 'github actions push has no pr number',
    env: {
      GITHUB_ACTIONS: 'true',
      GITHUB_REPOSITORY: 'acme/webapp',
      GITHUB_EVENT_NAME: 'push',
      GITHUB_REF: 'refs/heads/main',
      GITHUB_ACTOR: 'octocat',
    },
    want: { ci_provider: 'github_actions', ci_repo: 'acme/webapp', ci_actor: 'octocat' },
  },
  {
    name: 'github actions push to a refs/pull-looking ref still has no pr number',
    env: { ...GITHUB_PR, GITHUB_EVENT_NAME: 'push' },
    want: {
      ci_provider: 'github_actions',
      ci_repo: 'acme/webapp',
      ci_actor: 'octocat',
      ci_run_url: 'https://github.com/acme/webapp/actions/runs/99',
    },
  },
  {
    name: 'github actions run url needs all three parts',
    env: { GITHUB_ACTIONS: 'true', GITHUB_REPOSITORY: 'acme/webapp', GITHUB_RUN_ID: '99' },
    want: { ci_provider: 'github_actions', ci_repo: 'acme/webapp' },
  },
  {
    name: 'github actions server url trailing slash is not doubled',
    env: { ...GITHUB_PR, GITHUB_SERVER_URL: 'https://github.example.test/' },
    want: {
      ci_provider: 'github_actions',
      ci_repo: 'acme/webapp',
      pr_number: 421,
      ci_actor: 'octocat',
      ci_run_url: 'https://github.example.test/acme/webapp/actions/runs/99',
    },
  },
  {
    name: 'gitlab merge request',
    env: {
      GITLAB_CI: 'true',
      CI_PROJECT_PATH: 'group/project',
      CI_MERGE_REQUEST_IID: '17',
      GITLAB_USER_LOGIN: 'jdoe',
      CI_JOB_URL: 'https://gitlab.com/group/project/-/jobs/123',
    },
    want: {
      ci_provider: 'gitlab_ci',
      ci_repo: 'group/project',
      pr_number: 17,
      ci_actor: 'jdoe',
      ci_run_url: 'https://gitlab.com/group/project/-/jobs/123',
    },
  },
  {
    name: 'gitlab fallbacks (user name, pipeline url)',
    env: {
      GITLAB_CI: 'true',
      CI_PROJECT_PATH: 'group/project',
      GITLAB_USER_NAME: 'Jane Doe',
      CI_PIPELINE_URL: 'https://gitlab.com/group/project/-/pipelines/9',
    },
    want: {
      ci_provider: 'gitlab_ci',
      ci_repo: 'group/project',
      ci_actor: 'Jane Doe',
      ci_run_url: 'https://gitlab.com/group/project/-/pipelines/9',
    },
  },
  {
    name: 'jenkins multibranch pr with https git url',
    env: {
      JENKINS_URL: 'https://ci.example.com/',
      GIT_URL: 'https://github.com/acme/webapp.git',
      CHANGE_ID: '55',
      CHANGE_AUTHOR: 'jdoe',
      BUILD_URL: 'https://ci.example.com/job/webapp/55/',
    },
    want: {
      ci_provider: 'jenkins',
      ci_repo: 'acme/webapp',
      pr_number: 55,
      ci_actor: 'jdoe',
      ci_run_url: 'https://ci.example.com/job/webapp/55/',
    },
  },
  {
    name: 'jenkins ssh git url and build user fallback',
    env: {
      JENKINS_URL: 'https://ci.example.com/',
      GIT_URL: 'git@github.com:acme/webapp.git',
      BUILD_USER_ID: 'release-bot',
      BUILD_URL: 'https://ci.example.com/job/webapp/56/',
    },
    want: {
      ci_provider: 'jenkins',
      ci_repo: 'acme/webapp',
      ci_actor: 'release-bot',
      ci_run_url: 'https://ci.example.com/job/webapp/56/',
    },
  },
  {
    name: 'jenkins unparseable git url leaves repo empty',
    env: { JENKINS_URL: 'https://ci.example.com/', GIT_URL: 'webapp' },
    want: { ci_provider: 'jenkins' },
  },
  {
    name: 'azure devops pull request',
    env: {
      TF_BUILD: 'True',
      BUILD_REPOSITORY_NAME: 'acme/webapp',
      SYSTEM_PULLREQUEST_PULLREQUESTNUMBER: '88',
      BUILD_REQUESTEDFOR: 'Jane Doe',
      SYSTEM_TEAMFOUNDATIONCOLLECTIONURI: 'https://dev.azure.com/acme/',
      SYSTEM_TEAMPROJECT: 'WebApp',
      BUILD_BUILDID: '1234',
    },
    want: {
      ci_provider: 'azure_devops',
      ci_repo: 'acme/webapp',
      pr_number: 88,
      ci_actor: 'Jane Doe',
      ci_run_url: 'https://dev.azure.com/acme/WebApp/_build/results?buildId=1234',
    },
  },
  {
    name: 'azure devops pr id fallback and a collection uri without a slash',
    env: {
      TF_BUILD: 'True',
      BUILD_REPOSITORY_NAME: 'acme/webapp',
      SYSTEM_PULLREQUEST_PULLREQUESTID: '89',
      SYSTEM_TEAMFOUNDATIONCOLLECTIONURI: 'https://dev.azure.com/acme',
      SYSTEM_TEAMPROJECT: 'WebApp',
      BUILD_BUILDID: '1235',
    },
    want: {
      ci_provider: 'azure_devops',
      ci_repo: 'acme/webapp',
      pr_number: 89,
      ci_run_url: 'https://dev.azure.com/acme/WebApp/_build/results?buildId=1235',
    },
  },
  {
    name: 'circleci pull request',
    env: {
      CIRCLECI: 'true',
      CIRCLE_PROJECT_USERNAME: 'acme',
      CIRCLE_PROJECT_REPONAME: 'webapp',
      CIRCLE_PULL_REQUEST: 'https://github.com/acme/webapp/pull/33',
      CIRCLE_USERNAME: 'jdoe',
      CIRCLE_BUILD_URL: 'https://circleci.com/gh/acme/webapp/77',
    },
    want: {
      ci_provider: 'circleci',
      ci_repo: 'acme/webapp',
      pr_number: 33,
      ci_actor: 'jdoe',
      ci_run_url: 'https://circleci.com/gh/acme/webapp/77',
    },
  },
  {
    name: 'no ci detected',
    env: {},
    want: {},
  },
  {
    name: 'a provider flag that is not "true" is not a match',
    env: { GITHUB_ACTIONS: 'false', GITHUB_REPOSITORY: 'acme/webapp', CIRCLECI: '1' },
    want: {},
  },
  {
    name: 'provider precedence: github wins over gitlab',
    env: {
      GITHUB_ACTIONS: 'true',
      GITHUB_REPOSITORY: 'acme/webapp',
      GITLAB_CI: 'true',
      CI_PROJECT_PATH: 'group/project',
    },
    want: { ci_provider: 'github_actions', ci_repo: 'acme/webapp' },
  },
  {
    name: 'provider precedence: jenkins wins over azure and circleci',
    env: { JENKINS_URL: 'https://ci.example.com/', TF_BUILD: 'True', CIRCLECI: 'true' },
    want: { ci_provider: 'jenkins' },
  },
  {
    name: 'malformed pr numbers are dropped, never thrown',
    env: { GITLAB_CI: 'true', CI_PROJECT_PATH: 'group/project', CI_MERGE_REQUEST_IID: 'not-a-number' },
    want: { ci_provider: 'gitlab_ci', ci_repo: 'group/project' },
  },
  {
    name: 'a pr number with trailing garbage is dropped',
    env: { GITLAB_CI: 'true', CI_PROJECT_PATH: 'group/project', CI_MERGE_REQUEST_IID: '12abc' },
    want: { ci_provider: 'gitlab_ci', ci_repo: 'group/project' },
  },
  {
    name: 'zero and negative pr numbers are dropped',
    env: { JENKINS_URL: 'https://ci.example.com/', CHANGE_ID: '0' },
    want: { ci_provider: 'jenkins' },
  },
  {
    name: 'a negative pr number is dropped',
    env: { TF_BUILD: 'True', SYSTEM_PULLREQUEST_PULLREQUESTNUMBER: '-3' },
    want: { ci_provider: 'azure_devops' },
  },
  {
    name: 'whitespace-only values are treated as unset and values are trimmed',
    env: { GITLAB_CI: ' true ', CI_PROJECT_PATH: '  group/project  ', GITLAB_USER_LOGIN: '   ', GITLAB_USER_NAME: 'Jane' },
    want: { ci_provider: 'gitlab_ci', ci_repo: 'group/project', ci_actor: 'Jane' },
  },
]

for (const { name, env, want } of MATRIX) {
  test(`detectCiContext: ${name}`, () => {
    assert.deepStrictEqual(detectCiContext(env), want)
  })
}

test('detectCiContext truncates to the backend caps', () => {
  const got = detectCiContext({
    GITLAB_CI: 'true',
    CI_PROJECT_PATH: 'r'.repeat(400),
    GITLAB_USER_LOGIN: 'a'.repeat(200),
    CI_JOB_URL: 'https://gitlab.example.test/' + 'u'.repeat(1200),
  })
  assert.equal(got.ci_repo.length, 300)
  assert.equal(got.ci_actor.length, 120)
  assert.equal(got.ci_run_url.length, 1000)
  assert.ok(got.ci_run_url.startsWith('https://gitlab.example.test/'))
})

test('resolveCiContext precedence: detection < TESTLOOKUP_CI_* env < explicit', () => {
  const got = resolveCiContext(
    { ci_actor: 'explicit-actor' },
    {
      GITHUB_ACTIONS: 'true',
      GITHUB_REPOSITORY: 'acme/webapp',
      GITHUB_ACTOR: 'octocat',
      TESTLOOKUP_CI_REPO: 'acme/override-repo',
      TESTLOOKUP_PR_NUMBER: '7',
    },
  )
  assert.deepStrictEqual(got, {
    ci_provider: 'github_actions',
    ci_repo: 'acme/override-repo',
    ci_actor: 'explicit-actor',
    pr_number: 7,
  })
})

test('resolveCiContext: an explicit value beats an env override for the same field', () => {
  const got = resolveCiContext(
    { ci_repo: 'acme/explicit', pr_number: 9 },
    { TESTLOOKUP_CI_REPO: 'acme/env', TESTLOOKUP_PR_NUMBER: '7', TESTLOOKUP_CI_PROVIDER: 'buildkite' },
  )
  assert.deepStrictEqual(got, { ci_provider: 'buildkite', ci_repo: 'acme/explicit', pr_number: 9 })
})

test('resolveCiContext: a malformed override is ignored and detection survives', () => {
  const got = resolveCiContext({ pr_number: 0 }, {
    GITLAB_CI: 'true',
    CI_PROJECT_PATH: 'group/project',
    CI_MERGE_REQUEST_IID: '17',
    TESTLOOKUP_PR_NUMBER: 'abc',
  })
  assert.deepStrictEqual(got, { ci_provider: 'gitlab_ci', ci_repo: 'group/project', pr_number: 17 })
})

test('resolveCiContext clips env overrides to the caps and drops blank ones', () => {
  const got = resolveCiContext(undefined, { TESTLOOKUP_CI_ACTOR: 'x'.repeat(500), TESTLOOKUP_CI_REPO: '   ' })
  assert.deepStrictEqual(got, { ci_actor: 'x'.repeat(120) })
})

// Every env name the detection reads; blanked for the reporter test so the
// runner's own CI variables (this suite runs on GitHub Actions) cannot leak in.
const CI_ENV_NAMES = [
  'GITHUB_ACTIONS', 'GITHUB_REPOSITORY', 'GITHUB_EVENT_NAME', 'GITHUB_REF', 'GITHUB_ACTOR',
  'GITHUB_SERVER_URL', 'GITHUB_RUN_ID', 'GITLAB_CI', 'CI_PROJECT_PATH', 'CI_MERGE_REQUEST_IID',
  'GITLAB_USER_LOGIN', 'GITLAB_USER_NAME', 'CI_JOB_URL', 'CI_PIPELINE_URL', 'JENKINS_URL', 'GIT_URL',
  'CHANGE_ID', 'CHANGE_AUTHOR', 'BUILD_USER_ID', 'BUILD_URL', 'TF_BUILD', 'BUILD_REPOSITORY_NAME',
  'SYSTEM_PULLREQUEST_PULLREQUESTNUMBER', 'SYSTEM_PULLREQUEST_PULLREQUESTID', 'BUILD_REQUESTEDFOR',
  'SYSTEM_TEAMFOUNDATIONCOLLECTIONURI', 'SYSTEM_TEAMPROJECT', 'BUILD_BUILDID', 'CIRCLECI',
  'CIRCLE_PROJECT_USERNAME', 'CIRCLE_PROJECT_REPONAME', 'CIRCLE_PULL_REQUEST', 'CIRCLE_USERNAME',
  'CIRCLE_BUILD_URL',
]

test('startSession sends the detected CI context, with explicit options winning', async () => {
  const saved = { ...process.env }
  const server = installFakeServer()
  try {
    for (const key of CI_ENV_NAMES) delete process.env[key]
    clearTestLookupEnv()
    Object.assign(process.env, { ...GITHUB_PR, GITHUB_EVENT_NAME: 'pull_request_target' })
    process.env.TESTLOOKUP_CI_ACTOR = 'env-actor'

    const reporter = new TestLookupReporter({ baseUrl: 'http://testlookup.invalid', projectId: 'p', token: 'ci-token-test-only' })
    const session = await reporter.startSession({ buildNumber: 'b-1', ciRepo: 'acme/explicit-repo' })
    await session.close()

    const body = server.sessionCreate().body
    assert.equal(body.ci_provider, 'github_actions')
    assert.equal(body.pr_number, 421)
    assert.equal(body.ci_run_url, 'https://github.com/acme/webapp/actions/runs/99')
    assert.equal(body.ci_actor, 'env-actor')
    assert.equal(body.ci_repo, 'acme/explicit-repo')
  } finally {
    server.restore()
    for (const key of Object.keys(process.env)) {
      if (!(key in saved)) delete process.env[key]
    }
    Object.assign(process.env, saved)
  }
})
