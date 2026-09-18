# EXP-BUG-124 — live guide described an unusable Python SDK install

## Failure

The public Python SDK endpoint changed from a single reporter file to a ZIP so
that required sibling modules and package metadata ship together. The live
execution guide still labelled the artifact `Python (.py)` and told users to
copy only the reporter, which fails at import time.

## Fix and regression

The guide labels the artifact `Python (.zip)` and tells users to extract it and
run `pip install ".[yaml]"` in the bundled `python` directory so the immediately
recommended `testlookup.yaml` configuration is loaded. A raw-source consumer
contract test pins the label and instructions. The M22 mutation harness removes
the YAML extra from the install command and requires that test to fail with
status 1.

## Verification

Review, exact deployed revision, and final frontend results are recorded in
`M22-cli-mcp-sdk-parity.md` and `defects.csv`.
