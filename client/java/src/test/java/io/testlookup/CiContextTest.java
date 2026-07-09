package io.testlookup;

import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Tests the CI-context auto-detection matrix (US-4.3b). Mirrors the
 * Python/Go/JS SDK tests so all four clients honour the same contract:
 * provider precedence, per-provider field mapping, malformed-int
 * tolerance, backend length caps, and the
 * detection &lt; TESTLOOKUP_CI_* env &lt; explicit precedence chain.
 *
 * {@code CiContext.detect(Map)} takes the environment as a parameter
 * because {@code System.getenv()} is effectively unmockable.
 */
public class CiContextTest {

    private static Map<String, String> env(String... kv) {
        Map<String, String> m = new HashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put(kv[i], kv[i + 1]);
        return m;
    }

    // ── GitHub Actions ────────────────────────────────────────────────────────

    @Test
    void github_actions_pull_request() {
        CiContext ci = CiContext.detect(env(
            "GITHUB_ACTIONS", "true",
            "GITHUB_REPOSITORY", "acme/webapp",
            "GITHUB_EVENT_NAME", "pull_request",
            "GITHUB_REF", "refs/pull/421/merge",
            "GITHUB_ACTOR", "octocat",
            "GITHUB_SERVER_URL", "https://github.com",
            "GITHUB_RUN_ID", "99"
        ));
        assertEquals("github_actions", ci.provider);
        assertEquals("acme/webapp", ci.repo);
        assertEquals(421, ci.prNumber);
        assertEquals("octocat", ci.actor);
        assertEquals("https://github.com/acme/webapp/actions/runs/99", ci.runUrl);
    }

    @Test
    void github_actions_push_has_no_pr_number() {
        CiContext ci = CiContext.detect(env(
            "GITHUB_ACTIONS", "true",
            "GITHUB_REPOSITORY", "acme/webapp",
            "GITHUB_EVENT_NAME", "push",
            "GITHUB_REF", "refs/heads/main"
        ));
        assertEquals("github_actions", ci.provider);
        assertNull(ci.prNumber);
    }

    @Test
    void github_actions_run_url_requires_all_three_parts() {
        CiContext ci = CiContext.detect(env(
            "GITHUB_ACTIONS", "true",
            "GITHUB_REPOSITORY", "acme/webapp",
            "GITHUB_RUN_ID", "99"
            // GITHUB_SERVER_URL missing
        ));
        assertNull(ci.runUrl);
    }

    // ── GitLab CI ─────────────────────────────────────────────────────────────

    @Test
    void gitlab_merge_request() {
        CiContext ci = CiContext.detect(env(
            "GITLAB_CI", "true",
            "CI_PROJECT_PATH", "group/project",
            "CI_MERGE_REQUEST_IID", "17",
            "GITLAB_USER_LOGIN", "jdoe",
            "CI_JOB_URL", "https://gitlab.com/group/project/-/jobs/123"
        ));
        assertEquals("gitlab_ci", ci.provider);
        assertEquals("group/project", ci.repo);
        assertEquals(17, ci.prNumber);
        assertEquals("jdoe", ci.actor);
        assertEquals("https://gitlab.com/group/project/-/jobs/123", ci.runUrl);
    }

    @Test
    void gitlab_fallbacks_user_name_and_pipeline_url() {
        CiContext ci = CiContext.detect(env(
            "GITLAB_CI", "true",
            "GITLAB_USER_NAME", "Jane Doe",
            "CI_PIPELINE_URL", "https://gitlab.com/g/p/-/pipelines/9"
        ));
        assertEquals("Jane Doe", ci.actor);
        assertEquals("https://gitlab.com/g/p/-/pipelines/9", ci.runUrl);
    }

    // ── Jenkins ───────────────────────────────────────────────────────────────

    @Test
    void jenkins_multibranch_pr_with_https_git_url() {
        CiContext ci = CiContext.detect(env(
            "JENKINS_URL", "https://ci.example.com/",
            "GIT_URL", "https://github.com/acme/webapp.git",
            "CHANGE_ID", "55",
            "CHANGE_AUTHOR", "jdoe",
            "BUILD_URL", "https://ci.example.com/job/webapp/55/"
        ));
        assertEquals("jenkins", ci.provider);
        assertEquals("acme/webapp", ci.repo);
        assertEquals(55, ci.prNumber);
        assertEquals("jdoe", ci.actor);
        assertEquals("https://ci.example.com/job/webapp/55/", ci.runUrl);
    }

    @Test
    void jenkins_ssh_git_url_and_build_user_fallback() {
        CiContext ci = CiContext.detect(env(
            "JENKINS_URL", "https://ci.example.com/",
            "GIT_URL", "git@github.com:acme/webapp.git",
            "BUILD_USER_ID", "release-bot"
        ));
        assertEquals("acme/webapp", ci.repo);
        assertEquals("release-bot", ci.actor);
    }

    @Test
    void jenkins_unparseable_git_url_leaves_repo_null() {
        CiContext ci = CiContext.detect(env(
            "JENKINS_URL", "https://ci.example.com/",
            "GIT_URL", "webapp"
        ));
        assertEquals("jenkins", ci.provider);
        assertNull(ci.repo);
    }

    // ── Azure DevOps ──────────────────────────────────────────────────────────

    @Test
    void azure_devops_pull_request() {
        CiContext ci = CiContext.detect(env(
            "TF_BUILD", "True",
            "BUILD_REPOSITORY_NAME", "acme/webapp",
            "SYSTEM_PULLREQUEST_PULLREQUESTNUMBER", "88",
            "BUILD_REQUESTEDFOR", "Jane Doe",
            "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", "https://dev.azure.com/acme/",
            "SYSTEM_TEAMPROJECT", "WebApp",
            "BUILD_BUILDID", "1234"
        ));
        assertEquals("azure_devops", ci.provider);
        assertEquals("acme/webapp", ci.repo);
        assertEquals(88, ci.prNumber);
        assertEquals("Jane Doe", ci.actor);
        assertEquals("https://dev.azure.com/acme/WebApp/_build/results?buildId=1234", ci.runUrl);
    }

    @Test
    void azure_devops_pr_id_fallback() {
        CiContext ci = CiContext.detect(env(
            "TF_BUILD", "True",
            "SYSTEM_PULLREQUEST_PULLREQUESTID", "89"
        ));
        assertEquals(89, ci.prNumber);
    }

    // ── CircleCI ──────────────────────────────────────────────────────────────

    @Test
    void circleci_pull_request() {
        CiContext ci = CiContext.detect(env(
            "CIRCLECI", "true",
            "CIRCLE_PROJECT_USERNAME", "acme",
            "CIRCLE_PROJECT_REPONAME", "webapp",
            "CIRCLE_PULL_REQUEST", "https://github.com/acme/webapp/pull/33",
            "CIRCLE_USERNAME", "jdoe",
            "CIRCLE_BUILD_URL", "https://circleci.com/gh/acme/webapp/77"
        ));
        assertEquals("circleci", ci.provider);
        assertEquals("acme/webapp", ci.repo);
        assertEquals(33, ci.prNumber);
        assertEquals("jdoe", ci.actor);
        assertEquals("https://circleci.com/gh/acme/webapp/77", ci.runUrl);
    }

    // ── Cross-provider rules ──────────────────────────────────────────────────

    @Test
    void no_ci_detected_yields_all_nulls() {
        CiContext ci = CiContext.detect(env());
        assertNull(ci.provider);
        assertNull(ci.repo);
        assertNull(ci.prNumber);
        assertNull(ci.actor);
        assertNull(ci.runUrl);
    }

    @Test
    void provider_precedence_github_wins_over_gitlab() {
        CiContext ci = CiContext.detect(env(
            "GITHUB_ACTIONS", "true",
            "GITHUB_REPOSITORY", "acme/webapp",
            "GITLAB_CI", "true",
            "CI_PROJECT_PATH", "group/project"
        ));
        assertEquals("github_actions", ci.provider);
        assertEquals("acme/webapp", ci.repo);
    }

    @Test
    void malformed_pr_numbers_are_null_never_throw() {
        CiContext gitlab = CiContext.detect(env(
            "GITLAB_CI", "true", "CI_MERGE_REQUEST_IID", "not-a-number"));
        assertNull(gitlab.prNumber);

        CiContext jenkins = CiContext.detect(env(
            "JENKINS_URL", "https://ci.example.com/", "CHANGE_ID", "0"));
        assertNull(jenkins.prNumber);

        assertNull(CiContext.toPrNumber("-3"));
        assertNull(CiContext.toPrNumber(""));
        assertNull(CiContext.toPrNumber(null));
        assertEquals(1, CiContext.toPrNumber(" 1 "));
    }

    @Test
    void string_fields_are_truncated_to_backend_caps() {
        StringBuilder longRepo = new StringBuilder();
        for (int i = 0; i < 40; i++) longRepo.append("0123456789");  // 400 chars
        CiContext ci = CiContext.detect(env(
            "GITLAB_CI", "true", "CI_PROJECT_PATH", longRepo.toString()));
        assertEquals(CiContext.MAX_REPO_LEN, ci.repo.length());
    }

    @Test
    void resolve_applies_testlookup_env_overrides_over_detection() {
        CiContext ci = CiContext.resolve(env(
            "GITHUB_ACTIONS", "true",
            "GITHUB_REPOSITORY", "acme/webapp",
            "GITHUB_ACTOR", "octocat",
            "TESTLOOKUP_CI_REPO", "acme/override-repo",
            "TESTLOOKUP_PR_NUMBER", "7"
        ));
        assertEquals("github_actions", ci.provider);
        assertEquals("acme/override-repo", ci.repo);
        assertEquals(7, ci.prNumber);
        assertEquals("octocat", ci.actor);
    }

    @Test
    void resolve_ignores_malformed_env_override_keeping_detection() {
        CiContext ci = CiContext.resolve(env(
            "GITLAB_CI", "true",
            "CI_MERGE_REQUEST_IID", "17",
            "TESTLOOKUP_PR_NUMBER", "abc"
        ));
        assertEquals(17, ci.prNumber);
    }
}
