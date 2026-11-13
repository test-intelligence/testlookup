package fixture

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/test-intelligence/testlookup/client/go/testlookup"
)

var reporter *testlookup.TestReporter

func TestMain(m *testing.M) {
	client, err := testlookup.NewFromEnv()
	if err != nil {
		panic(err)
	}
	session, err := client.StartSession(context.Background(), testlookup.SessionOptionsFromEnv())
	if err != nil {
		panic(err)
	}
	reporter = testlookup.NewTestReporter(session, "release-fixture")
	code := m.Run()
	reporter.Flush(context.Background())
	os.Exit(code)
}

func TestReleasePass(t *testing.T) {
	defer reporter.RecordTest(t, t.Name(), time.Now())
	if 2+2 != 4 {
		t.Fatal("incorrect sum")
	}
}

func TestReleaseFail(t *testing.T) {
	defer reporter.RecordTest(t, t.Name(), time.Now())
	t.Error("deliberate reporter fixture failure")
}
