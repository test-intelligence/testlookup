# Baseline — QA Run 2026-08-29-02

## Repository state

- Remote: `https://github.com/anandtopu/testlookup.git`
- Synchronization: fast-forwarded local `main` from `e230a81c` to `30f2e979` with `git pull --ff-only origin main`.
- Working branch: `qa-improvements/2026-08-29-02`.
- Source/test inventory: 716 Python test files, 229 frontend test files, 36 E2E specs, and 2,270 tracked files discovered with `rg --files` (including non-source assets/docs).

## Baseline checks

| Area | Command | Result |
|---|---|---|
| Frontend unit | `npm run test -- --reporter=dot --silent` | 164 files, 1,123 passed |
| Frontend lint | `npm run lint` | 0 errors, 18 existing warnings |
| Frontend type/build | `npm run build` | Passed |
| Frontend theme | `npm run check:theme` | Passed |
| Frontend bundle | `npm run check:bundle` | Passed; 141,370 gzip bytes / 180,000 budget |
| Backend tests | `pytest tests -q` | 7,445 passed, 58 skipped; 11 failures + 5 errors from Windows shell/Python environment |
| CLI + SDK focused rerun | `pytest cli/tests client/tests -q` | 254 passed after fix |
| Commit-range regression | `pytest client/tests/test_commit_range.py -q` | 161 passed after fix |

The backend shell failures were reproduced as missing `cat`/`dirname` in the Git Bash child environment and a WSL `bash.exe` syntax-check incompatibility. The deploy/tag shell cases passed when run with a real Git Bash and shell utilities available; quickstart generation still cannot execute host `python3` from this sandbox.

