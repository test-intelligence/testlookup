package testlookup

// CI-context auto-detection (US-4.3b).
//
// Detects the CI provider, repository, PR number, actor, and run URL from
// the standard environment variables each CI system exports, so test runs
// land in TestLookup already linked to their pull request and CI job.
//
// Detection matrix (first match wins — mirrors client/ci_context.py and the
// JS/Java SDK ports; keep all copies in sync):
//
//  1. GitHub Actions — GITHUB_ACTIONS=true
//  2. GitLab CI      — GITLAB_CI=true
//  3. Jenkins        — JENKINS_URL set
//  4. Azure DevOps   — TF_BUILD=True
//  5. CircleCI       — CIRCLECI=true
//
// Nothing matches → zero-value CIContext (never guess). Malformed integers
// → the field stays unset (never panic). String values are defensively
// truncated to the backend's length caps.

import (
	"os"
	"regexp"
	"strconv"
	"strings"
)

// Backend length caps — see backend IngestPayload / LiveSessionCreate.
const (
	maxCIProviderLen = 30
	maxCIRepoLen     = 300
	maxCIActorLen    = 120
	maxCIRunURLLen   = 1000
)

// CIContext carries the CI metadata stamped onto a test run. Zero values
// ("" / 0) mean "unknown" and are omitted from the session-create payload.
type CIContext struct {
	// Provider is the detected CI system, e.g. "github_actions".
	Provider string
	// Repo is the repository as "org/name".
	Repo string
	// PRNumber is the pull/merge request number (0 = not a PR build).
	PRNumber int
	// Actor is the CI user that triggered the run.
	Actor string
	// RunURL deep-links back to the CI job.
	RunURL string
}

var (
	githubPRRef   = regexp.MustCompile(`^refs/pull/(\d+)/`)
	trailingPRNum = regexp.MustCompile(`/(\d+)/?$`)
)

// DetectCIContext detects CI context from the process environment. Outside
// CI it returns a zero-value CIContext (never guesses).
func DetectCIContext() CIContext {
	return detectCIContext(os.Getenv)
}

func detectCIContext(getenv func(string) string) CIContext {
	get := func(key string) string { return strings.TrimSpace(getenv(key)) }
	truthy := func(key string) bool { return strings.EqualFold(get(key), "true") }

	ctx := CIContext{}
	switch {
	case truthy("GITHUB_ACTIONS"):
		ctx.Provider = "github_actions"
		ctx.Repo = get("GITHUB_REPOSITORY")
		eventName := get("GITHUB_EVENT_NAME")
		if eventName == "pull_request" || eventName == "pull_request_target" {
			if m := githubPRRef.FindStringSubmatch(get("GITHUB_REF")); m != nil {
				ctx.PRNumber = toPRNumber(m[1])
			}
		}
		ctx.Actor = get("GITHUB_ACTOR")
		server, runID := get("GITHUB_SERVER_URL"), get("GITHUB_RUN_ID")
		if server != "" && ctx.Repo != "" && runID != "" {
			ctx.RunURL = strings.TrimRight(server, "/") + "/" + ctx.Repo + "/actions/runs/" + runID
		}
	case truthy("GITLAB_CI"):
		ctx.Provider = "gitlab_ci"
		ctx.Repo = get("CI_PROJECT_PATH")
		ctx.PRNumber = toPRNumber(get("CI_MERGE_REQUEST_IID"))
		ctx.Actor = coalesce(get("GITLAB_USER_LOGIN"), get("GITLAB_USER_NAME"))
		ctx.RunURL = coalesce(get("CI_JOB_URL"), get("CI_PIPELINE_URL"))
	case get("JENKINS_URL") != "":
		ctx.Provider = "jenkins"
		ctx.Repo = repoFromGitURL(get("GIT_URL"))
		ctx.PRNumber = toPRNumber(get("CHANGE_ID")) // multibranch PR builds
		ctx.Actor = coalesce(get("CHANGE_AUTHOR"), get("BUILD_USER_ID"))
		ctx.RunURL = get("BUILD_URL")
	case truthy("TF_BUILD"):
		ctx.Provider = "azure_devops"
		ctx.Repo = get("BUILD_REPOSITORY_NAME")
		ctx.PRNumber = toPRNumber(get("SYSTEM_PULLREQUEST_PULLREQUESTNUMBER"))
		if ctx.PRNumber == 0 {
			ctx.PRNumber = toPRNumber(get("SYSTEM_PULLREQUEST_PULLREQUESTID"))
		}
		ctx.Actor = get("BUILD_REQUESTEDFOR")
		coll, proj, buildID := get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI"), get("SYSTEM_TEAMPROJECT"), get("BUILD_BUILDID")
		if coll != "" && proj != "" && buildID != "" {
			if !strings.HasSuffix(coll, "/") {
				coll += "/"
			}
			ctx.RunURL = coll + proj + "/_build/results?buildId=" + buildID
		}
	case truthy("CIRCLECI"):
		ctx.Provider = "circleci"
		user, name := get("CIRCLE_PROJECT_USERNAME"), get("CIRCLE_PROJECT_REPONAME")
		if user != "" && name != "" {
			ctx.Repo = user + "/" + name
		}
		if prURL := get("CIRCLE_PULL_REQUEST"); prURL != "" {
			if m := trailingPRNum.FindStringSubmatch(prURL); m != nil {
				ctx.PRNumber = toPRNumber(m[1])
			}
		}
		ctx.Actor = get("CIRCLE_USERNAME")
		ctx.RunURL = get("CIRCLE_BUILD_URL")
	default:
		return CIContext{}
	}

	return ctx.clipped()
}

// resolveCIContext merges CI context with the standard precedence chain:
// detection < TESTLOOKUP_CI_* env overrides < explicit caller values.
func resolveCIContext(getenv func(string) string, explicit CIContext) CIContext {
	ctx := detectCIContext(getenv)

	get := func(key string) string { return strings.TrimSpace(getenv(key)) }
	if v := get("TESTLOOKUP_CI_PROVIDER"); v != "" {
		ctx.Provider = v
	}
	if v := get("TESTLOOKUP_CI_REPO"); v != "" {
		ctx.Repo = v
	}
	if n := toPRNumber(get("TESTLOOKUP_PR_NUMBER")); n != 0 {
		ctx.PRNumber = n
	}
	if v := get("TESTLOOKUP_CI_ACTOR"); v != "" {
		ctx.Actor = v
	}
	if v := get("TESTLOOKUP_CI_RUN_URL"); v != "" {
		ctx.RunURL = v
	}

	if explicit.Provider != "" {
		ctx.Provider = explicit.Provider
	}
	if explicit.Repo != "" {
		ctx.Repo = explicit.Repo
	}
	if explicit.PRNumber >= 1 {
		ctx.PRNumber = explicit.PRNumber
	}
	if explicit.Actor != "" {
		ctx.Actor = explicit.Actor
	}
	if explicit.RunURL != "" {
		ctx.RunURL = explicit.RunURL
	}

	return ctx.clipped()
}

// clipped truncates string fields to the backend length caps and drops
// out-of-range PR numbers.
func (c CIContext) clipped() CIContext {
	c.Provider = clipTo(c.Provider, maxCIProviderLen)
	c.Repo = clipTo(c.Repo, maxCIRepoLen)
	c.Actor = clipTo(c.Actor, maxCIActorLen)
	c.RunURL = clipTo(c.RunURL, maxCIRunURLLen)
	if c.PRNumber < 1 {
		c.PRNumber = 0
	}
	return c
}

func clipTo(s string, limit int) string {
	s = strings.TrimSpace(s)
	if len(s) > limit {
		return s[:limit]
	}
	return s
}

// toPRNumber coerces a PR number to a positive int; malformed / <1 → 0.
func toPRNumber(s string) int {
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil || n < 1 {
		return 0
	}
	return n
}

// repoFromGitURL derives "org/name" from a Jenkins GIT_URL, or "" when
// unparseable: strip a trailing ".git", treat ":" like a path separator,
// take the last two path segments.
func repoFromGitURL(gitURL string) string {
	s := strings.TrimSpace(gitURL)
	if s == "" {
		return ""
	}
	s = strings.TrimSuffix(s, ".git")
	parts := strings.FieldsFunc(strings.ReplaceAll(s, ":", "/"), func(r rune) bool { return r == '/' })
	if len(parts) < 2 {
		return ""
	}
	return parts[len(parts)-2] + "/" + parts[len(parts)-1]
}
