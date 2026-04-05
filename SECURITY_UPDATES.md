# Security Updates

Updated: 2026-03-27

This document summarizes the dependency security remediation work completed in this repo after the GitHub Dependabot alerts reported on the default branch.

## Completed Updates

### Backend dependency changes

Updated in [backend/requirements.txt](C:\Users\anand\Downloads\Projects\AI_TestDashboard\testlookup\backend\requirements.txt):

- `python-multipart` `0.0.12` -> `0.0.22`
- `python-jose[cryptography]` `3.3.0` -> `3.4.0`
- `aiohttp` `3.11.10` -> `3.13.3`
- `langchain` `0.3.9` -> `0.3.27`
- `langchain-core` `0.3.21` -> `0.3.81`

Removed unused direct dependencies:

- `langchain-community`
- `jinja2`
- `orjson`

Reason for removal:

- `langchain-community`: no repo import evidence under `backend/app` or `backend/tests`
- `jinja2`: no repo import evidence; email/report HTML is built with plain Python string templates in [backend/app/services/notification/email_service.py](C:\Users\anand\Downloads\Projects\AI_TestDashboard\testlookup\backend\app\services\notification\email_service.py) and [backend/app/services/report_service.py](C:\Users\anand\Downloads\Projects\AI_TestDashboard\testlookup\backend\app\services\report_service.py)
- `orjson`: no repo import evidence under `backend/app` or `backend/tests`

### Frontend dependency changes

Updated in [frontend/package.json](C:\Users\anand\Downloads\Projects\AI_TestDashboard\testlookup\frontend\package.json) and refreshed [frontend/package-lock.json](C:\Users\anand\Downloads\Projects\AI_TestDashboard\testlookup\frontend\package-lock.json):

- targeted `overrides` added for `picomatch` and `brace-expansion`
- lockfile refreshed with `npm install`

Result:

- `npm audit` now reports `0 vulnerabilities`

## Alert Mapping

### Expected cleared by version bump

- `python-jose algorithm confusion with OpenSSH ECDSA keys`
- `python-jose denial of service via compressed JWE content`
- `Denial of service (DoS) via deformation multipart/form-data boundary`
- `Python-Multipart has Arbitrary File Write via Non-Default Configuration`
- `AIOHTTP's HTTP Parser auto_decompress feature is vulnerable to zip bomb`
- `AIOHTTP vulnerable to denial of service through large payloads`
- `AIOHTTP vulnerable to DoS when bypassing asserts`
- `AIOHTTP vulnerable to DoS through chunked messages`
- frontend `picomatch` alerts
- frontend `brace-expansion` alerts

### Expected cleared by dependency removal

- `Langchain Community Vulnerable to XML External Entity (XXE) Attacks`
- `orjson does not limit recursion for deeply nested JSON documents`
- Jinja2 sandbox breakout alerts

### Expected cleared by LangChain core/library bump

- `LangChain serialization injection vulnerability enables secret extraction in dumps/loads APIs`
- `LangChain Vulnerable to Template Injection via Attribute Access in Prompt Templates`
- `LangChain Core has Path Traversal vulnerabilites in legacy load_prompt functions`

## Residual Risk / Follow-up

### LangGraph advisory

GitHub alert:

- `LangGraph checkpoint loading has unsafe msgpack deserialization`

Current repo state:

- repo evidence only shows `StateGraph` usage in [backend/app/agents/workflow.py](C:\Users\anand\Downloads\Projects\AI_TestDashboard\testlookup\backend\app\agents\workflow.py)
- no repo evidence found for LangGraph checkpoint loading APIs

Status:

- not directly exercised by repo-evidenced code paths
- still worth monitoring for a future clean upgrade path if GitHub continues to flag it

### Python 3.14 warning from upstream packages

Backend tests still show third-party warnings related to LangChain/Pydantic v1 compatibility on Python 3.14. These are not failing tests, but they are useful to watch during future dependency upgrades.

## Validation Performed

- `backend\venv\Scripts\python.exe -m pytest backend\tests -q`
- `npm run type-check` in `frontend`
- `npm audit --json` in `frontend`

Results:

- backend tests: `76 passed`
- frontend type-check: passed
- frontend audit: `0 vulnerabilities`
