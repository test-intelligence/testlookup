package testlookup

import (
	"os"
	"testing"
)

// All env vars the detection matrix reads — every test case starts by
// blanking these so ambient CI env (e.g. when this suite itself runs in
// GitHub Actions) cannot leak into the assertions.
var allCIEnvVars = []string{
	"GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_REF",
	"GITHUB_ACTOR", "GITHUB_SERVER_URL", "GITHUB_RUN_ID",
	"GITLAB_CI", "CI_PROJECT_PATH", "CI_MERGE_REQUEST_IID",
	"GITLAB_USER_LOGIN", "GITLAB_USER_NAME", "CI_JOB_URL", "CI_PIPELINE_URL",
	"JENKINS_URL", "GIT_URL", "CHANGE_ID", "CHANGE_AUTHOR", "BUILD_USER_ID", "BUILD_URL",
	"TF_BUILD", "BUILD_REPOSITORY_NAME", "SYSTEM_PULLREQUEST_PULLREQUESTNUMBER",
	"SYSTEM_PULLREQUEST_PULLREQUESTID", "BUILD_REQUESTEDFOR",
	"SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", "SYSTEM_TEAMPROJECT", "BUILD_BUILDID",
	"CIRCLECI", "CIRCLE_PROJECT_USERNAME", "CIRCLE_PROJECT_REPONAME",
	"CIRCLE_PULL_REQUEST", "CIRCLE_USERNAME", "CIRCLE_BUILD_URL",
	"TESTLOOKUP_CI_PROVIDER", "TESTLOOKUP_CI_REPO", "TESTLOOKUP_PR_NUMBER",
	"TESTLOOKUP_CI_ACTOR", "TESTLOOKUP_CI_RUN_URL",
}

func setEnv(t *testing.T, env map[string]string) {
	t.Helper()
	for _, key := range allCIEnvVars {
		t.Setenv(key, "")
	}
	for key, value := range env {
		t.Setenv(key, value)
	}
}

func TestDetectCIContext(t *testing.T) {
	cases := []struct {
		name string
		env  map[string]string
		want CIContext
	}{
		{
			name: "github actions pull request",
			env: map[string]string{
				"GITHUB_ACTIONS":    "true",
				"GITHUB_REPOSITORY": "acme/webapp",
				"GITHUB_EVENT_NAME": "pull_request",
				"GITHUB_REF":        "refs/pull/421/merge",
				"GITHUB_ACTOR":      "octocat",
				"GITHUB_SERVER_URL": "https://github.com",
				"GITHUB_RUN_ID":     "99",
			},
			want: CIContext{
				Provider: "github_actions",
				Repo:     "acme/webapp",
				PRNumber: 421,
				Actor:    "octocat",
				RunURL:   "https://github.com/acme/webapp/actions/runs/99",
			},
		},
		{
			name: "github actions push has no pr number",
			env: map[string]string{
				"GITHUB_ACTIONS":    "true",
				"GITHUB_REPOSITORY": "acme/webapp",
				"GITHUB_EVENT_NAME": "push",
				"GITHUB_REF":        "refs/heads/main",
				"GITHUB_ACTOR":      "octocat",
			},
			want: CIContext{
				Provider: "github_actions",
				Repo:     "acme/webapp",
				Actor:    "octocat",
			},
		},
		{
			name: "github actions run url needs all three parts",
			env: map[string]string{
				"GITHUB_ACTIONS":    "true",
				"GITHUB_REPOSITORY": "acme/webapp",
				"GITHUB_RUN_ID":     "99",
				// GITHUB_SERVER_URL missing → no RunURL
			},
			want: CIContext{
				Provider: "github_actions",
				Repo:     "acme/webapp",
			},
		},
		{
			name: "gitlab merge request",
			env: map[string]string{
				"GITLAB_CI":            "true",
				"CI_PROJECT_PATH":      "group/project",
				"CI_MERGE_REQUEST_IID": "17",
				"GITLAB_USER_LOGIN":    "jdoe",
				"CI_JOB_URL":           "https://gitlab.com/group/project/-/jobs/123",
			},
			want: CIContext{
				Provider: "gitlab_ci",
				Repo:     "group/project",
				PRNumber: 17,
				Actor:    "jdoe",
				RunURL:   "https://gitlab.com/group/project/-/jobs/123",
			},
		},
		{
			name: "gitlab fallbacks (user name, pipeline url)",
			env: map[string]string{
				"GITLAB_CI":        "true",
				"CI_PROJECT_PATH":  "group/project",
				"GITLAB_USER_NAME": "Jane Doe",
				"CI_PIPELINE_URL":  "https://gitlab.com/group/project/-/pipelines/9",
			},
			want: CIContext{
				Provider: "gitlab_ci",
				Repo:     "group/project",
				Actor:    "Jane Doe",
				RunURL:   "https://gitlab.com/group/project/-/pipelines/9",
			},
		},
		{
			name: "jenkins multibranch pr with https git url",
			env: map[string]string{
				"JENKINS_URL":   "https://ci.example.com/",
				"GIT_URL":       "https://github.com/acme/webapp.git",
				"CHANGE_ID":     "55",
				"CHANGE_AUTHOR": "jdoe",
				"BUILD_URL":     "https://ci.example.com/job/webapp/55/",
			},
			want: CIContext{
				Provider: "jenkins",
				Repo:     "acme/webapp",
				PRNumber: 55,
				Actor:    "jdoe",
				RunURL:   "https://ci.example.com/job/webapp/55/",
			},
		},
		{
			name: "jenkins ssh git url and build user fallback",
			env: map[string]string{
				"JENKINS_URL":   "https://ci.example.com/",
				"GIT_URL":       "git@github.com:acme/webapp.git",
				"BUILD_USER_ID": "release-bot",
				"BUILD_URL":     "https://ci.example.com/job/webapp/56/",
			},
			want: CIContext{
				Provider: "jenkins",
				Repo:     "acme/webapp",
				Actor:    "release-bot",
				RunURL:   "https://ci.example.com/job/webapp/56/",
			},
		},
		{
			name: "jenkins unparseable git url leaves repo empty",
			env: map[string]string{
				"JENKINS_URL": "https://ci.example.com/",
				"GIT_URL":     "webapp",
			},
			want: CIContext{Provider: "jenkins"},
		},
		{
			name: "azure devops pull request",
			env: map[string]string{
				"TF_BUILD":                             "True",
				"BUILD_REPOSITORY_NAME":                "acme/webapp",
				"SYSTEM_PULLREQUEST_PULLREQUESTNUMBER": "88",
				"BUILD_REQUESTEDFOR":                   "Jane Doe",
				"SYSTEM_TEAMFOUNDATIONCOLLECTIONURI":   "https://dev.azure.com/acme/",
				"SYSTEM_TEAMPROJECT":                   "WebApp",
				"BUILD_BUILDID":                        "1234",
			},
			want: CIContext{
				Provider: "azure_devops",
				Repo:     "acme/webapp",
				PRNumber: 88,
				Actor:    "Jane Doe",
				RunURL:   "https://dev.azure.com/acme/WebApp/_build/results?buildId=1234",
			},
		},
		{
			name: "azure devops pr id fallback",
			env: map[string]string{
				"TF_BUILD":                         "True",
				"BUILD_REPOSITORY_NAME":            "acme/webapp",
				"SYSTEM_PULLREQUEST_PULLREQUESTID": "89",
			},
			want: CIContext{
				Provider: "azure_devops",
				Repo:     "acme/webapp",
				PRNumber: 89,
			},
		},
		{
			name: "circleci pull request",
			env: map[string]string{
				"CIRCLECI":                "true",
				"CIRCLE_PROJECT_USERNAME": "acme",
				"CIRCLE_PROJECT_REPONAME": "webapp",
				"CIRCLE_PULL_REQUEST":     "https://github.com/acme/webapp/pull/33",
				"CIRCLE_USERNAME":         "jdoe",
				"CIRCLE_BUILD_URL":        "https://circleci.com/gh/acme/webapp/77",
			},
			want: CIContext{
				Provider: "circleci",
				Repo:     "acme/webapp",
				PRNumber: 33,
				Actor:    "jdoe",
				RunURL:   "https://circleci.com/gh/acme/webapp/77",
			},
		},
		{
			name: "no ci detected",
			env:  map[string]string{},
			want: CIContext{},
		},
		{
			name: "provider precedence: github wins over gitlab",
			env: map[string]string{
				"GITHUB_ACTIONS":    "true",
				"GITHUB_REPOSITORY": "acme/webapp",
				"GITLAB_CI":         "true",
				"CI_PROJECT_PATH":   "group/project",
			},
			want: CIContext{
				Provider: "github_actions",
				Repo:     "acme/webapp",
			},
		},
		{
			name: "malformed pr numbers are dropped, never panic",
			env: map[string]string{
				"GITLAB_CI":            "true",
				"CI_PROJECT_PATH":      "group/project",
				"CI_MERGE_REQUEST_IID": "not-a-number",
			},
			want: CIContext{
				Provider: "gitlab_ci",
				Repo:     "group/project",
			},
		},
		{
			name: "zero and negative pr numbers are dropped",
			env: map[string]string{
				"JENKINS_URL": "https://ci.example.com/",
				"CHANGE_ID":   "0",
			},
			want: CIContext{Provider: "jenkins"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			setEnv(t, tc.env)
			got := DetectCIContext()
			if got != tc.want {
				t.Errorf("DetectCIContext() = %+v, want %+v", got, tc.want)
			}
		})
	}
}

func TestDetectCIContextTruncatesToBackendCaps(t *testing.T) {
	longRepo := ""
	for i := 0; i < 40; i++ {
		longRepo += "0123456789"
	} // 400 chars
	setEnv(t, map[string]string{
		"GITLAB_CI":       "true",
		"CI_PROJECT_PATH": longRepo,
	})
	got := DetectCIContext()
	if len(got.Repo) != maxCIRepoLen {
		t.Errorf("Repo length = %d, want %d", len(got.Repo), maxCIRepoLen)
	}
}

func TestResolveCIContextPrecedence(t *testing.T) {
	// detection < TESTLOOKUP_CI_* env < explicit
	setEnv(t, map[string]string{
		"GITHUB_ACTIONS":     "true",
		"GITHUB_REPOSITORY":  "acme/webapp",
		"GITHUB_ACTOR":       "octocat",
		"TESTLOOKUP_CI_REPO": "acme/override-repo",
		"TESTLOOKUP_PR_NUMBER": "7",
	})
	got := resolveCIContext(os.Getenv, CIContext{Actor: "explicit-actor"})

	if got.Provider != "github_actions" {
		t.Errorf("Provider = %q, want github_actions", got.Provider)
	}
	if got.Repo != "acme/override-repo" {
		t.Errorf("Repo = %q, want env override to win over detection", got.Repo)
	}
	if got.PRNumber != 7 {
		t.Errorf("PRNumber = %d, want 7 from env override", got.PRNumber)
	}
	if got.Actor != "explicit-actor" {
		t.Errorf("Actor = %q, want explicit value to win", got.Actor)
	}
}

func TestResolveCIContextMalformedEnvOverrideIgnored(t *testing.T) {
	setEnv(t, map[string]string{
		"GITLAB_CI":            "true",
		"CI_PROJECT_PATH":      "group/project",
		"CI_MERGE_REQUEST_IID": "17",
		"TESTLOOKUP_PR_NUMBER": "abc",
	})
	got := resolveCIContext(os.Getenv, CIContext{})
	if got.PRNumber != 17 {
		t.Errorf("PRNumber = %d, want detection value 17 to survive malformed override", got.PRNumber)
	}
}
