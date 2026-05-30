package testlookup

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
)

// ResolvedConfig is the merged configuration from the discovery chain
// (testlookup.properties → environment variables). Caller-supplied values
// in Config still take final precedence.
type ResolvedConfig struct {
	BaseURL     string
	APIKey      string
	Token       string
	ProjectID   string
	LaunchName  string
	// SuiteName is the run-level suite identifier, sourced from
	// testlookup.suite (preferred) with testlookup.launch as the
	// documented fallback. Stamped on every record() that doesn't
	// supply its own suite so dashboards group runs cleanly.
	SuiteName   string
	// ReleaseName is the release this test run belongs to. Sourced from
	// testlookup.release / TESTLOOKUP_RELEASE. When blank the server
	// falls back to the project's default release on session create.
	ReleaseName string
	BuildNumber string
	Branch      string
	CommitHash  string
	Framework   string
}

// LoadConfig discovers a testlookup.properties file (preferred — matches the
// ReportPortal convention) and overlays TESTLOOKUP_* environment variables
// on top of it.
//
// Discovery order (first existing file wins):
//
//	./testlookup.properties
//	./.testlookup/testlookup.properties
//	~/.testlookup/testlookup.properties
//
// Canonical key prefix is testlookup.* (e.g. testlookup.endpoint,
// testlookup.api.key, testlookup.launch).
func LoadConfig() ResolvedConfig {
	cfg := ResolvedConfig{}

	if path := findPropertiesFile(); path != "" {
		applyPropertiesFile(path, &cfg)
	}

	// Env-var overlay (legacy + canonical names — both work).
	overlay := func(envName string, dst *string) {
		if v := os.Getenv(envName); v != "" {
			*dst = v
		}
	}
	overlay("TESTLOOKUP_URL", &cfg.BaseURL)
	overlay("TESTLOOKUP_ENDPOINT", &cfg.BaseURL)
	overlay("TESTLOOKUP_API_KEY", &cfg.APIKey)
	overlay("TESTLOOKUP_TOKEN", &cfg.Token)
	overlay("TESTLOOKUP_PROJECT_ID", &cfg.ProjectID)
	overlay("TESTLOOKUP_PROJECT", &cfg.ProjectID)
	overlay("TESTLOOKUP_LAUNCH", &cfg.LaunchName)
	overlay("TESTLOOKUP_SUITE", &cfg.SuiteName)
	overlay("TESTLOOKUP_RELEASE", &cfg.ReleaseName)
	overlay("TESTLOOKUP_BUILD", &cfg.BuildNumber)
	overlay("TESTLOOKUP_BRANCH", &cfg.Branch)
	overlay("TESTLOOKUP_COMMIT", &cfg.CommitHash)
	overlay("TESTLOOKUP_FRAMEWORK", &cfg.Framework)

	return cfg
}

func findPropertiesFile() string {
	candidates := []string{
		"testlookup.properties",
		filepath.Join(".testlookup", "testlookup.properties"),
	}
	for _, p := range candidates {
		if fileExists(p) {
			return p
		}
	}
	if home, err := os.UserHomeDir(); err == nil {
		hp := filepath.Join(home, ".testlookup", "testlookup.properties")
		if fileExists(hp) {
			return hp
		}
	}
	return ""
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func applyPropertiesFile(path string, cfg *ResolvedConfig) {
	f, err := os.Open(path)
	if err != nil {
		return
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, "!") {
			continue
		}
		eq := strings.IndexAny(line, "=:")
		if eq < 0 {
			continue
		}
		key := strings.TrimSpace(line[:eq])
		val := strings.TrimSpace(line[eq+1:])
		if val == "" {
			continue
		}
		switch key {
		case "testlookup.endpoint", "testlookup.url":
			cfg.BaseURL = val
		case "testlookup.api.key", "testlookup.api_key", "testlookup.apiKey":
			cfg.APIKey = val
		case "testlookup.token":
			cfg.Token = val
		case "testlookup.project":
			cfg.ProjectID = val
		case "testlookup.launch":
			cfg.LaunchName = val
		case "testlookup.suite":
			cfg.SuiteName = val
		case "testlookup.release":
			cfg.ReleaseName = val
		case "testlookup.build":
			cfg.BuildNumber = val
		case "testlookup.branch":
			cfg.Branch = val
		case "testlookup.commit":
			cfg.CommitHash = val
		case "testlookup.framework":
			cfg.Framework = val
		}
	}
}
