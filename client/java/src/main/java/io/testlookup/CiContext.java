package io.testlookup;

import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * CI-context auto-detection (US-4.3b).
 * <p>
 * Detects the CI provider, repository, PR number, actor, and run URL from
 * the standard environment variables each CI system exports, so test runs
 * land in TestLookup already linked to their pull request and CI job.
 * <p>
 * Detection matrix (first match wins — mirrors {@code client/ci_context.py}
 * and the JS/Go SDK ports; keep all copies in sync):
 * <ol>
 *   <li>GitHub Actions — {@code GITHUB_ACTIONS=true}</li>
 *   <li>GitLab CI      — {@code GITLAB_CI=true}</li>
 *   <li>Jenkins        — {@code JENKINS_URL} set</li>
 *   <li>Azure DevOps   — {@code TF_BUILD=True}</li>
 *   <li>CircleCI       — {@code CIRCLECI=true}</li>
 * </ol>
 * Nothing matches → all fields null (never guess). Malformed integers →
 * null for that field (never throw). String values are defensively
 * truncated to the backend's length caps.
 * <p>
 * {@link #resolve(Map)} additionally applies the {@code TESTLOOKUP_CI_*}
 * env-var overrides on top of detection; explicit
 * {@code SessionOptions} values win over both (applied in
 * {@code TestLookupReporter.startSession}).
 */
public final class CiContext {

    // Backend length caps — see backend IngestPayload / LiveSessionCreate.
    static final int MAX_PROVIDER_LEN = 30;
    static final int MAX_REPO_LEN     = 300;
    static final int MAX_ACTOR_LEN    = 120;
    static final int MAX_RUN_URL_LEN  = 1000;

    private static final Pattern GITHUB_PR_REF   = Pattern.compile("^refs/pull/(\\d+)/.*");
    private static final Pattern TRAILING_PR_NUM = Pattern.compile(".*/(\\d+)/?$");

    /** Detected CI system, e.g. "github_actions"; null outside CI. */
    public final String provider;
    /** Repository as "org/name"; null when undeterminable. */
    public final String repo;
    /** Pull/merge request number (>= 1); null when not a PR build. */
    public final Integer prNumber;
    /** CI user that triggered the run; null when undeterminable. */
    public final String actor;
    /** Deep link back to the CI job; null when undeterminable. */
    public final String runUrl;

    private CiContext(String provider, String repo, Integer prNumber, String actor, String runUrl) {
        this.provider = clip(provider, MAX_PROVIDER_LEN);
        this.repo     = clip(repo, MAX_REPO_LEN);
        this.prNumber = (prNumber != null && prNumber >= 1) ? prNumber : null;
        this.actor    = clip(actor, MAX_ACTOR_LEN);
        this.runUrl   = clip(runUrl, MAX_RUN_URL_LEN);
    }

    private static final CiContext EMPTY = new CiContext(null, null, null, null, null);

    /** Detect CI context from the process environment. Never throws. */
    public static CiContext detect() {
        return detect(System.getenv());
    }

    /**
     * Detect CI context from the given environment map (injectable for
     * tests — {@code System.getenv()} is effectively unmockable).
     */
    public static CiContext detect(Map<String, String> env) {
        if (env == null) return EMPTY;

        if (truthy(env, "GITHUB_ACTIONS")) {
            String repo = get(env, "GITHUB_REPOSITORY");
            Integer pr = null;
            String eventName = get(env, "GITHUB_EVENT_NAME");
            if ("pull_request".equals(eventName) || "pull_request_target".equals(eventName)) {
                String ref = get(env, "GITHUB_REF");
                if (ref != null) {
                    Matcher m = GITHUB_PR_REF.matcher(ref);
                    if (m.matches()) pr = toPrNumber(m.group(1));
                }
            }
            String runUrl = null;
            String server = get(env, "GITHUB_SERVER_URL");
            String runId  = get(env, "GITHUB_RUN_ID");
            if (server != null && repo != null && runId != null) {
                runUrl = server.replaceAll("/+$", "") + "/" + repo + "/actions/runs/" + runId;
            }
            return new CiContext("github_actions", repo, pr, get(env, "GITHUB_ACTOR"), runUrl);
        }

        if (truthy(env, "GITLAB_CI")) {
            String actor = get(env, "GITLAB_USER_LOGIN");
            if (actor == null) actor = get(env, "GITLAB_USER_NAME");
            String runUrl = get(env, "CI_JOB_URL");
            if (runUrl == null) runUrl = get(env, "CI_PIPELINE_URL");
            return new CiContext(
                "gitlab_ci",
                get(env, "CI_PROJECT_PATH"),
                toPrNumber(get(env, "CI_MERGE_REQUEST_IID")),
                actor,
                runUrl
            );
        }

        if (get(env, "JENKINS_URL") != null) {
            String actor = get(env, "CHANGE_AUTHOR");
            if (actor == null) actor = get(env, "BUILD_USER_ID");
            return new CiContext(
                "jenkins",
                repoFromGitUrl(get(env, "GIT_URL")),
                toPrNumber(get(env, "CHANGE_ID")),  // multibranch PR builds
                actor,
                get(env, "BUILD_URL")
            );
        }

        if (truthy(env, "TF_BUILD")) {
            Integer pr = toPrNumber(get(env, "SYSTEM_PULLREQUEST_PULLREQUESTNUMBER"));
            if (pr == null) pr = toPrNumber(get(env, "SYSTEM_PULLREQUEST_PULLREQUESTID"));
            String runUrl = null;
            String coll    = get(env, "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI");
            String proj    = get(env, "SYSTEM_TEAMPROJECT");
            String buildId = get(env, "BUILD_BUILDID");
            if (coll != null && proj != null && buildId != null) {
                if (!coll.endsWith("/")) coll = coll + "/";
                runUrl = coll + proj + "/_build/results?buildId=" + buildId;
            }
            return new CiContext(
                "azure_devops",
                get(env, "BUILD_REPOSITORY_NAME"),
                pr,
                get(env, "BUILD_REQUESTEDFOR"),
                runUrl
            );
        }

        if (truthy(env, "CIRCLECI")) {
            String user = get(env, "CIRCLE_PROJECT_USERNAME");
            String name = get(env, "CIRCLE_PROJECT_REPONAME");
            String repo = (user != null && name != null) ? user + "/" + name : null;
            Integer pr = null;
            String prUrl = get(env, "CIRCLE_PULL_REQUEST");
            if (prUrl != null) {
                Matcher m = TRAILING_PR_NUM.matcher(prUrl);
                if (m.matches()) pr = toPrNumber(m.group(1));
            }
            return new CiContext("circleci", repo, pr, get(env, "CIRCLE_USERNAME"),
                                 get(env, "CIRCLE_BUILD_URL"));
        }

        return EMPTY;
    }

    /**
     * Detection + {@code TESTLOOKUP_CI_*} env-var overrides. Explicit
     * caller values (e.g. {@code SessionOptions}) should be applied on top
     * of the result.
     */
    public static CiContext resolve(Map<String, String> env) {
        CiContext detected = detect(env);
        if (env == null) return detected;

        String provider = firstNonNull(get(env, "TESTLOOKUP_CI_PROVIDER"), detected.provider);
        String repo     = firstNonNull(get(env, "TESTLOOKUP_CI_REPO"),     detected.repo);
        Integer pr      = toPrNumber(get(env, "TESTLOOKUP_PR_NUMBER"));
        if (pr == null) pr = detected.prNumber;
        String actor    = firstNonNull(get(env, "TESTLOOKUP_CI_ACTOR"),    detected.actor);
        String runUrl   = firstNonNull(get(env, "TESTLOOKUP_CI_RUN_URL"),  detected.runUrl);
        return new CiContext(provider, repo, pr, actor, runUrl);
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private static String get(Map<String, String> env, String key) {
        String v = env.get(key);
        if (v == null) return null;
        v = v.trim();
        return v.isEmpty() ? null : v;
    }

    private static boolean truthy(Map<String, String> env, String key) {
        String v = get(env, key);
        return v != null && v.equalsIgnoreCase("true");
    }

    private static String firstNonNull(String a, String b) {
        return a != null ? a : b;
    }

    /** Coerce a PR number to a positive Integer; malformed / &lt; 1 → null. */
    static Integer toPrNumber(String value) {
        if (value == null) return null;
        try {
            int n = Integer.parseInt(value.trim());
            return n >= 1 ? n : null;
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static String clip(String value, int cap) {
        if (value == null) return null;
        String s = value.trim();
        if (s.isEmpty()) return null;
        return s.length() > cap ? s.substring(0, cap) : s;
    }

    /**
     * Derive "org/name" from a Jenkins GIT_URL, or null when unparseable:
     * strip a trailing ".git", treat ":" like a path separator, take the
     * last two path segments.
     */
    static String repoFromGitUrl(String gitUrl) {
        if (gitUrl == null) return null;
        String s = gitUrl.trim();
        if (s.isEmpty()) return null;
        if (s.endsWith(".git")) s = s.substring(0, s.length() - ".git".length());
        String[] raw = s.replace(':', '/').split("/");
        java.util.List<String> segments = new java.util.ArrayList<>();
        for (String part : raw) {
            if (!part.isEmpty()) segments.add(part);
        }
        if (segments.size() < 2) return null;
        return segments.get(segments.size() - 2) + "/" + segments.get(segments.size() - 1);
    }
}
