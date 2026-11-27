"""Candidate-bound signal acceptance for an explicitly disposable monitored stack.

The orchestrator owns provisioning, immutable runtime inspection and teardown.
This checker uses normal read APIs plus one synthetic batch and two read-only
worker probes. It never injects spans/logs directly into a telemetry backend.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[2]
PROBE_TASK = "app.worker.celery_app.queue_delivery_probe"
ROLES = {"api": "backend", "worker": "backend", "worker-children": "backend", "mcp": "mcp"}
STATES = {"waiting", "pending", "sending", "published", "processing", "completed", "failed"}
REQUIRED_OPERATIONS = {"run_notifications", "transition_notifications", "run_completed_webhook"}
COLLECTORS = ("queues", "agents", "downstream", "redis", "nightly", "digests", "pipeline_events", "journeys")


class GateError(Exception):
    def __init__(self, stage: str, code: str):
        self.stage, self.code = stage, code
        super().__init__(f"{stage}: {code}")


def require(ok, stage, code):
    if not ok:
        raise GateError(stage, code)


def _sha(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "provenance", "timestamp_missing_timezone")
    return parsed.astimezone(timezone.utc)


def validated_inputs(manifest_raw: bytes, proof_raw: bytes, *, now: datetime, project_id: str, profile: str):
    module_spec = importlib.util.spec_from_file_location("signal_release_manifest", ROOT / "scripts/release/release_manifest.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    try:
        manifest = module.validate_manifest(json.loads(manifest_raw))
        proof = json.loads(proof_raw)
        require(proof["schema_version"] == 1 and proof["disposable"] is True, "provenance", "disposable_proof_required")
        require(proof["source_sha"] == manifest["source_sha"] and proof["profile"] == profile,
                "provenance", "candidate_or_profile_mismatch")
        require(str(uuid.UUID(proof["project_id"])) == str(uuid.UUID(project_id)), "provenance", "fixture_project_mismatch")
        age = (now - _utc(proof["observed_at"])).total_seconds()
        require(-5 <= age <= 600, "provenance", "runtime_inspection_stale")
        rows = proof["roles"]
        require(isinstance(rows, list) and len(rows) == len(ROLES), "provenance", "runtime_role_inventory_incomplete")
        require({row["role"] for row in rows} == set(ROLES), "provenance", "runtime_role_inventory_incomplete")
        for row in rows:
            component = ROLES[row["role"]]
            wanted = manifest["images"][component]["source_ref"]
            require(row["component"] == component and row["candidate"] == wanted, "provenance", "runtime_candidate_mismatch")
            running = row["running"]
            require(isinstance(running, str) and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", running), "provenance", "runtime_digest_required")
            identities = row.get("verified_runtime_digests", [])
            require(isinstance(identities, list) and identities and all(isinstance(item, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in identities),
                    "provenance", "platform_digest_inventory_invalid")
            require(wanted.rsplit("@", 1)[1] in identities and running.rsplit("@", 1)[1] in identities,
                    "provenance", "runtime_digest_mismatch")
    except GateError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise GateError("provenance", "invalid_manifest_or_runtime_proof") from exc
    return manifest, proof


@dataclass(frozen=True)
class Config:
    api: str
    prometheus: str
    loki: str
    jaeger: str
    mcp: str
    api_key: str
    project_id: str
    installation: str
    environment: str
    profile: str = "compose"
    timeout: float = 180
    api_targets: int = 1
    worker_targets: int = 2

    def validate(self):
        for endpoint in (self.api, self.prometheus, self.loki, self.jaeger, self.mcp):
            value = urlsplit(endpoint)
            require(value.scheme in {"http", "https"} and value.hostname and not value.username and not value.password
                    and not value.query and not value.fragment and value.path in {"", "/"}, "configuration", "invalid_base_url")
        require(bool(self.api_key) and "\n" not in self.api_key and "\r" not in self.api_key, "configuration", "api_key_required")
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", self.installation) is not None, "configuration", "installation_required")
        require(self.environment in {"development", "staging", "production"}, "configuration", "invalid_environment")
        require(5 <= self.timeout <= 600 and 1 <= self.api_targets <= 20 and self.worker_targets == 2,
                "configuration", "unsupported_fixture_size")
        try:
            uuid.UUID(self.project_id)
        except ValueError as exc:
            raise GateError("configuration", "invalid_fixture_project") from exc


@dataclass
class Response:
    status: int
    data: object
    headers: dict


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class HTTP:
    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def request(self, method, base, path, *, body=None, headers=None):
        raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = Request(base.rstrip("/") + path, data=raw, method=method,
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            try:
                response = self.opener.open(request, timeout=5)
            except HTTPError as error:
                response = error
            with response:
                payload = response.read(4_194_305)
                require(len(payload) <= 4_194_304, "transport", "response_budget_exceeded")
                try:
                    data = json.loads(payload) if payload else None
                except ValueError:
                    data = None
                return Response(response.code, data, {key.lower(): value for key, value in response.headers.items()})
        except (OSError, URLError) as exc:
            raise GateError("transport", "endpoint_unavailable") from exc


def _label(value):
    return json.dumps(value, ensure_ascii=True)


class Gate:
    def __init__(self, config: Config, manifest, proof, *, http=None, celery=None, clock=time.monotonic, sleep=time.sleep):
        config.validate()
        self.config, self.manifest, self.proof = config, manifest, proof
        self.http, self.celery = http or HTTP(), celery
        self.clock, self.sleep = clock, sleep
        self.trace_id, self.request_id = uuid.uuid4().hex, "release-signal-" + uuid.uuid4().hex
        self.started_ns = time.time_ns()
        self.instances = {}
        self.passed = []

    def wait(self, stage, check):
        deadline = self.clock() + self.config.timeout
        while True:
            result = check()
            if result:
                return result
            if self.clock() >= deadline:
                raise GateError(stage, "deadline_exceeded")
            self.sleep(1)

    def api(self, method, path, *, body=None, request_id=None, traced=False):
        headers = {"X-API-Key": self.config.api_key}
        if request_id:
            headers["X-Request-ID"] = request_id
        if traced:
            headers["traceparent"] = f"00-{self.trace_id}-{uuid.uuid4().hex[:16]}-01"
        return self.http.request(method, self.config.api, path, body=body, headers=headers)

    def prometheus(self, path, **params):
        response = self.http.request("GET", self.config.prometheus, path + ("?" + urlencode(params) if params else ""))
        require(response.status == 200 and isinstance(response.data, dict) and response.data.get("status") == "success",
                "metrics", "prometheus_read_failed")
        return response.data["data"]

    def scalar(self, query, *, empty=None):
        result = self.prometheus("/api/v1/query", query=query)
        require(result.get("resultType") == "vector", "metrics", "unexpected_query_type")
        rows = result.get("result", [])
        if not rows and empty is not None:
            return empty
        require(len(rows) == 1, "metrics", "missing_or_ambiguous_series")
        try:
            value = float(rows[0]["value"][1])
        except (KeyError, TypeError, ValueError) as exc:
            raise GateError("metrics", "invalid_metric_value") from exc
        require(math.isfinite(value), "metrics", "nonfinite_metric_value")
        return value

    def selector(self, metric, *, worker=False, **labels):
        job = "testlookup-worker" if worker else "testlookup-backend"
        instances = self.instances[job]
        expression = "^(?:" + "|".join(re.escape(value) for value in sorted(instances)) + ")$"
        parts = ["job=" + _label(job), "instance=~" + _label(expression)]
        parts.extend(key + "=" + _label(value) for key, value in labels.items())
        return metric + "{" + ",".join(parts) + "}"

    def delta(self, query, baseline, expected):
        def observed():
            value = self.scalar(query, empty=0.0)
            require(value <= baseline + expected, "metrics", "exact_delta_overshot")
            require(value >= baseline, "metrics", "counter_reset_during_proof")
            return value == baseline + expected
        self.wait("metrics_delta", observed)

    def inventory(self):
        targets = self.prometheus("/api/v1/targets")["activeTargets"]
        for job, expected in (("testlookup-backend", self.config.api_targets), ("testlookup-worker", self.config.worker_targets)):
            rows = [row for row in targets if row.get("scrapePool") == job]
            require(len(rows) == expected and all(row.get("health") == "up" for row in rows), "targets", "missing_or_unhealthy_role")
            roles = [row.get("labels", {}).get("telemetry_role") for row in rows]
            require(all(isinstance(value, str) for value in roles), "targets", "incorrect_role_inventory")
            require(roles == ["backend"] * expected if job == "testlookup-backend" else sorted(roles) == ["worker", "worker-children"],
                    "targets", "incorrect_role_inventory")
            instances = {row.get("labels", {}).get("instance") for row in rows}
            require(len(instances) == expected and all(isinstance(value, str) and value for value in instances), "targets", "ambiguous_instance_inventory")
            self.instances[job] = instances

    def collectors(self):
        for collector in COLLECTORS:
            success = self.selector("testlookup_metric_collection_success", collector=collector)
            freshness = self.selector("testlookup_metric_collection_last_success_timestamp_seconds", collector=collector)
            require(self.scalar("count(" + success + ")") == self.config.api_targets
                    and self.scalar("count(" + freshness + ")") == self.config.api_targets, "collectors", "missing_collector_producer")
            require(self.scalar("min(" + success + ")") == 1, "collectors", "failed_collector")
            require(0 <= self.scalar("max(time()-" + freshness + ")") <= 120, "collectors", "stale_collector")
        observed = self.selector("testlookup_journey_cohort_observed_at_seconds", installation=self.config.installation, environment=self.config.environment)
        require(self.scalar("count(" + observed + ")") == self.config.api_targets, "collectors", "missing_business_snapshot")
        require(0 <= self.scalar("max(time()-" + observed + ")") <= 120, "collectors", "stale_business_snapshot")

    def durable(self, run_id):
        response = self.api("GET", "/api/v1/runs/" + run_id)
        if response.status == 404:
            return False
        require(response.status == 200 and isinstance(response.data, dict), "durable", "run_read_failed")
        row = response.data
        require(row.get("id") == run_id and row.get("project_id") == self.config.project_id, "durable", "run_identity_mismatch")
        require(not row.get("ingestion_rejected_tests"), "durable", "partial_ingestion_rejected")
        if row.get("ingestion_complete") is not True:
            return False
        require(row.get("total_tests") == 2 and row.get("passed_tests") == 1 and row.get("failed_tests") == 1
                and row.get("ingestion_rejected_tests") == 0, "durable", "normalized_result_mismatch")
        downstream = self.api("GET", "/api/v1/runs/" + run_id + "/downstream-status")
        require(downstream.status == 200 and isinstance(downstream.data, dict), "durable", "downstream_read_failed")
        value = downstream.data
        require(value.get("run_id") == run_id, "durable", "downstream_identity_mismatch")
        operations = value.get("operations", [])
        require(all(item.get("status") in STATES for item in operations), "durable", "unknown_lifecycle_state")
        require(not any(item["status"] == "failed" for item in operations), "durable", "terminal_business_failure")
        if value.get("status") != "completed":
            return False
        require(REQUIRED_OPERATIONS <= {item.get("operation") for item in operations} and all(item["status"] == "completed" for item in operations),
                "durable", "incomplete_operation_inventory")
        return True

    def traces(self):
        response = self.http.request("GET", self.config.jaeger, "/api/traces/" + self.trace_id)
        if response.status == 404:
            return {}
        require(response.status == 200 and isinstance(response.data, dict), "traces", "trace_query_failed")
        spans = {}
        for item in response.data.get("data", []):
            if item.get("traceID") != self.trace_id:
                continue
            for span in item.get("spans", []):
                process = item.get("processes", {}).get(span.get("processID"), {})
                tags = {tag["key"]: tag.get("value") for tag in process.get("tags", [])}
                role = tags.get("testlookup.service.role")
                if role not in {"api", "worker", "worker-children"}:
                    continue
                require(process.get("serviceName") == "testlookup" and tags.get("testlookup.build.revision") == self.manifest["source_sha"]
                        and tags.get("testlookup.installation") == self.config.installation
                        and tags.get("deployment.environment.name") == self.config.environment
                        and tags.get("service.version") == self.manifest["version"].removeprefix("v"), "traces", "candidate_resource_mismatch")
                if role != "api" and span.get("operationName") != "celery.consume":
                    continue  # Database/HTTP child spans are valid diagnostics too.
                spans[(role, span.get("spanID"))] = True
        return spans

    def logs(self, *, role, identifier, event, spans=None):
        field, digest = identifier
        query = ('{service_namespace="testlookup",deployment_environment_name=' + _label(self.config.environment) + '}'
                 + " | testlookup_installation=" + _label(self.config.installation)
                 + " | testlookup_build_revision=" + _label(self.manifest["source_sha"])
                 + " | service_version=" + _label(self.manifest["version"].removeprefix("v"))
                 + " | testlookup_service_role=" + _label(role) + " | " + field + "=" + _label(digest))
        if spans is not None:
            query += " | trace_id=" + _label(self.trace_id)
        response = self.http.request("GET", self.config.loki, "/loki/api/v1/query_range?" + urlencode({
            "query": query, "start": str(self.started_ns), "end": str(time.time_ns()), "limit": 100, "direction": "forward",
        }))
        require(response.status == 200 and isinstance(response.data, dict) and response.data.get("status") == "success", "logs", "log_query_failed")
        for stream in response.data.get("data", {}).get("result", []):
            for value in stream.get("values", []):
                try:
                    body = json.loads(value[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if body.get("event") != event or body.get(field) != digest:
                    continue
                if spans is not None and (body.get("trace_id") != self.trace_id or (role, body.get("span_id")) not in spans):
                    continue
                return True
        return False

    def run(self):
        version = self.api("GET", "/health/version")
        baseline_after = time.time()
        require(version.status == 200 and isinstance(version.data, dict) and version.data.get("build", {}).get("revision") == self.manifest["source_sha"]
                and version.data.get("version") == self.manifest["version"].removeprefix("v")
                and version.data.get("env") == self.config.environment, "provenance", "api_build_mismatch")
        project = self.api("GET", "/api/v1/projects/" + self.config.project_id)
        require(project.status == 200 and isinstance(project.data, dict)
                and str(project.data.get("slug", "")).startswith("release-signal-"), "fixture", "dedicated_project_required")
        self.inventory()
        # Establish the baseline after the provenance call has reached a scrape.
        def drained():
            rows = self.prometheus("/api/v1/targets")["activeTargets"]
            matching = [row for row in rows if row.get("scrapePool") == "testlookup-backend"
                        and row.get("labels", {}).get("instance") in self.instances["testlookup-backend"]]
            return len(matching) == self.config.api_targets and all(_utc(row["lastScrape"]).timestamp() >= baseline_after for row in matching)
        self.wait("baseline", drained)
        query = "sum(" + self.selector("http_requests_total", handler="/health/version", method="GET", status="2xx") + ")"
        baseline = self.scalar(query)
        for _ in range(5):
            require(self.api("GET", "/health/version", traced=True).status == 200, "api", "normal_read_failed")
        self.delta(query, baseline, 5)
        self.passed.extend(("candidate_runtime_identity", "role_targets", "exact_api_delta"))
        task_query = "sum(" + self.selector("testlookup_celery_tasks_total", worker=True, task_name=PROBE_TASK, status="success") + ")"
        runtime_query = "sum(" + self.selector("celery_task_runtime_seconds_count", worker=True, task_name=PROBE_TASK) + ")"
        task_base, runtime_base = self.scalar(task_query, empty=0), self.scalar(runtime_query, empty=0)
        correlation = "release-signal-" + uuid.uuid4().hex
        results = [self.celery.send_task(PROBE_TASK, queue=queue, kwargs={"correlation_id": correlation},
                                        headers={"traceparent": f"00-{self.trace_id}-{uuid.uuid4().hex[:16]}-01"})
                   for queue in ("default", "agent_children")]
        for queue, result in zip(("default", "agent_children"), results):
            try:
                receipt = result.get(timeout=30)
            except Exception as exc:
                raise GateError("workers", "consumer_result_unavailable") from exc
            require(isinstance(receipt, dict) and receipt.get("correlation_id") == correlation
                    and receipt.get("routing_key") == queue and bool(receipt.get("worker")), "workers", "consumer_execution_failed")
        self.delta(task_query, task_base, 2)
        self.delta(runtime_query, runtime_base, 2)
        self.passed.append("exact_worker_deltas")
        payload = {"project_id": self.config.project_id, "build_number": self.request_id, "run_ai": False,
                   "results": [{"test_name": "release_signal.synthetic_pass", "status": "PASSED", "duration_ms": 5},
                               {"test_name": "release_signal.synthetic_fail", "status": "FAILED", "duration_ms": 5}]}
        accepted = self.api("POST", "/api/v1/ingest", body=payload, request_id=self.request_id, traced=True)
        require(accepted.status == 202 and accepted.headers.get("x-request-id") == self.request_id and isinstance(accepted.data, dict),
                "ingestion", "acceptance_or_client_correlation_failed")
        try:
            run_id, task_id = str(uuid.UUID(accepted.data["run_id"])), str(uuid.UUID(accepted.data["task_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise GateError("ingestion", "invalid_acceptance_identity") from exc
        self.wait("durable_completion", lambda: self.durable(run_id))
        self.passed.append("durable_business_completion")
        def linked():
            spans = self.traces()
            if not {"api", "worker", "worker-children"} <= {role for role, _span in spans}:
                return False
            checks = [("api", ("request_id", _sha(self.request_id)), "http_request"),
                      ("worker", ("celery_task_id", _sha(results[0].id)), "celery_task_execution"),
                      ("worker-children", ("celery_task_id", _sha(results[1].id)), "celery_task_execution"),
                      ("worker", ("celery_task_id", _sha(task_id)), "celery_task_execution")]
            return all(self.logs(role=role, identifier=identifier, event=event, spans=spans) for role, identifier, event in checks)
        self.wait("linked_api_worker_logs_traces", linked)
        self.passed.append("linked_api_worker_logs_traces")
        mcp_id = "release-signal-" + uuid.uuid4().hex
        mcp = self.http.request("GET", self.config.mcp, "/release-signal-probe", headers={"X-Request-ID": mcp_id})
        require(mcp.status == 404 and mcp.headers.get("x-request-id") == mcp_id, "mcp", "operational_probe_failed")
        self.wait("mcp_operational_log", lambda: self.logs(role="mcp", identifier=("request_id", _sha(mcp_id)), event="mcp_request"))
        self.passed.append("candidate_mcp_operational_log")
        self.inventory()
        self.collectors()
        self.passed.append("fresh_required_collectors")
        return {"schema_version": 1, "status": "passed", "source_sha": self.manifest["source_sha"], "profile": self.config.profile,
                "trace_id": self.trace_id, "request_id_sha256": _sha(self.request_id), "run_id_sha256": _sha(run_id),
                "observed_deltas": {"http_version_requests": 5, "worker_tasks": 2, "worker_runtime_observations": 2},
                "normalized_results": {"total": 2, "passed": 1, "failed": 1, "rejected": 0},
                "checked": self.passed, "scope": "disposable API/both worker roles plus MCP operational logs",
                "remaining": ["firing/resolved receiver evidence is a separate gate", "MCP tool/browser journeys and restore acceptance are separate"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--runtime-proof", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = {"schema_version": 1, "status": "failed", "checked": []}
    started = datetime.now(timezone.utc).isoformat()
    gate = None
    try:
        require(os.getenv("TESTLOOKUP_DISPOSABLE_SIGNAL_GATE") == "1", "configuration", "explicit_disposable_target_required")
        config = Config(*(os.environ[name] for name in (
            "TESTLOOKUP_API_URL", "TESTLOOKUP_PROMETHEUS_URL", "TESTLOOKUP_LOKI_URL", "TESTLOOKUP_JAEGER_URL", "TESTLOOKUP_MCP_URL",
            "TESTLOOKUP_API_KEY", "TESTLOOKUP_SIGNAL_PROJECT_ID", "OBSERVABILITY_INSTALLATION", "APP_ENV")))
        manifest_raw, proof_raw = args.manifest.read_bytes(), args.runtime_proof.read_bytes()
        result.update(manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(), runtime_proof_sha256=hashlib.sha256(proof_raw).hexdigest())
        manifest, proof = validated_inputs(manifest_raw, proof_raw, now=datetime.now(timezone.utc), project_id=config.project_id, profile=config.profile)
        result.update(source_sha=manifest["source_sha"], profile=config.profile)
        from celery import Celery
        celery = Celery("release-signal-gate", broker=os.environ["CELERY_BROKER_URL"], backend=os.environ["CELERY_RESULT_BACKEND"])
        gate = Gate(config, manifest, proof, celery=celery)
        result = gate.run()
        result.update(manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(), runtime_proof_sha256=hashlib.sha256(proof_raw).hexdigest())
    except GateError as exc:
        result.update(stage=exc.stage, code=exc.code)
    except Exception:
        result.update(stage="runner", code="checker_failed")
    if gate is not None:
        result["checked"] = list(gate.passed)
    result.update(started_at=started, finished_at=datetime.now(timezone.utc).isoformat(),
                  checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Release signal gate: " + result["status"])
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
