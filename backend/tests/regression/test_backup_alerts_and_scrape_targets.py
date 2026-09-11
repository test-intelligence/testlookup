"""Re-audit R-B45-2, R-B45-5 and QA-B45-P5: the backup and the alert rules are
watched on a Kubernetes (Prometheus Operator) deployment.

* R-B45-2: a failed or stale backup alerts. The rules are parsed from the one
  rules file (the k8s PrometheusRule is rendered from it; parity is pinned in
  test_alert_rules_are_deployable.py) and matched against the real CronJob.
* R-B45-5: the backup pod's egress is DNS plus the three stores' ports, and
  every overlay that includes the component renders with it.
* QA-B45-P5: every deployment that serves /metrics is selected by a PodMonitor
  on the port and path it serves, and every ``job`` an alert selects on is
  a job some monitor produces.

promtool and a cluster are not available offline, so the manifests are parsed
and their values asserted.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
ALERTS = REPO_ROOT / "infra" / "monitoring" / "prometheus-rules" / "testlookup-alerts.yml"
MONITORING = REPO_ROOT / "k8s" / "monitoring"
BACKUP = REPO_ROOT / "k8s" / "components" / "backup"
BASE = REPO_ROOT / "k8s" / "base"

pytestmark = pytest.mark.skipif(
    not (ALERTS.exists() and MONITORING.exists()), reason="infra/ or k8s/ not present"
)


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _rules() -> dict[str, dict]:
    doc = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return {r["alert"]: r for g in doc["groups"] for r in g["rules"] if "alert" in r}


def _expr(name: str) -> str:
    return " ".join(_rules()[name]["expr"].split())


def _cronjob(path: Path) -> dict:
    return next(d for d in _docs(path) if d.get("kind") == "CronJob")


# ── R-B45-2 ──────────────────────────────────────────────────────────────────


# A tiny PromQL evaluator, enough for the backup rule's operators: vector
# selectors (= and =~ matchers), `and on (...)`, `or`, `max by (...)` and a
# scalar comparison. promtool is not installed offline, so the rule's REAL
# expression is parsed and run against synthetic kube-state-metrics series
# (R-B45-R2-1): a Job whose retry succeeded must not page.

_TOKEN = re.compile(r'\s*(?:(=~|==|!=|>=|<=|[(){},=><])|"([^"]*)"|([A-Za-z_][A-Za-z0-9_]*)|(\d+(?:\.\d+)?))')


def _tokens(expr: str) -> list[tuple[str, str]]:
    out, pos, expr = [], 0, expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        assert m and m.end() > pos, f"cannot tokenize at {expr[pos:]!r}"
        pos = m.end()
        if m.group(1):
            out.append(("op", m.group(1)))
        elif m.group(2) is not None:
            out.append(("str", m.group(2)))
        elif m.group(3):
            out.append(("id", m.group(3)))
        else:
            out.append(("num", m.group(4)))
    return out


Series = tuple[dict, float]


class _PromQL:
    def __init__(self, expr: str, series: list[tuple[str, dict, float]]) -> None:
        self.toks = _tokens(expr)
        self.i = 0
        self.series = series

    def peek(self, value: str | None = None) -> bool:
        return self.i < len(self.toks) and (value is None or self.toks[self.i][1] == value)

    def take(self, value: str | None = None) -> str:
        assert self.i < len(self.toks), "unexpected end"
        kind, tok = self.toks[self.i]
        assert value is None or tok == value, (value, tok, self.toks[self.i:])
        self.i += 1
        return tok

    def labels(self) -> list[str]:
        self.take("(")
        names = [self.take()]
        while self.peek(","):
            self.take(",")
            names.append(self.take())
        self.take(")")
        return names

    def run(self) -> list[Series]:
        out = self.or_expr()
        assert self.i == len(self.toks), self.toks[self.i:]
        return out

    def or_expr(self) -> list[Series]:
        left = self.and_expr()
        while self.peek("or"):
            self.take("or")
            right = self.and_expr()
            keys = {frozenset(lbl.items()) for lbl, _ in left}
            left = left + [s for s in right if frozenset(s[0].items()) not in keys]
        return left

    def and_expr(self) -> list[Series]:
        left = self.cmp_expr()
        while self.peek("and"):
            self.take("and")
            on = self.labels() if self.peek("on") and self.take("on") else None
            right = self.cmp_expr()

            def key(lbl: dict) -> frozenset:
                return frozenset((k, v) for k, v in lbl.items() if on is None or k in on)

            wanted = {key(lbl) for lbl, _ in right}
            left = [s for s in left if key(s[0]) in wanted]
        return left

    def cmp_expr(self) -> list[Series]:
        vec = self.primary()
        if self.peek("==") or self.peek(">"):
            op = self.take()
            n = float(self.take())
            vec = [s for s in vec if (s[1] == n if op == "==" else s[1] > n)]
        return vec

    def primary(self) -> list[Series]:
        if self.peek("("):
            self.take("(")
            vec = self.or_expr()
            self.take(")")
            return vec
        name = self.take()
        if name == "max":
            self.take("by")
            by = self.labels()
            self.take("(")
            inner = self.or_expr()
            self.take(")")
            groups: dict[frozenset, float] = {}
            for lbl, value in inner:
                k = frozenset((n, lbl.get(n, "")) for n in by)
                groups[k] = max(groups.get(k, value), value)
            return [(dict(k), v) for k, v in groups.items()]
        matchers: list[tuple[str, str, str]] = []
        if self.peek("{"):
            self.take("{")
            while not self.peek("}"):
                label, op, value = self.take(), self.take(), self.take()
                matchers.append((label, op, value))
                if self.peek(","):
                    self.take(",")
            self.take("}")
        out = []
        for metric, lbl, value in self.series:
            if metric != name:
                continue
            ok = True
            for label, op, want in matchers:
                have = lbl.get(label, "")
                ok &= (have == want) if op == "=" else bool(re.fullmatch(want, have)) if op == "=~" else have != want
            if ok:
                out.append(({**lbl, "__name__": metric}, value))
        return out


def _job_series(job: str, *, failed_pods: int, succeeded: int, final: str | None,
                owner: str | None, namespace: str = "testlookup") -> list[tuple[str, dict, float]]:
    """kube-state-metrics series for one Job, the way KSM v2 exports them."""
    base = {"namespace": namespace, "job_name": job}
    out = [
        ("kube_job_info", dict(base), 1.0),
        ("kube_job_status_failed", dict(base), float(failed_pods)),
        ("kube_job_status_succeeded", dict(base), float(succeeded)),
        ("kube_job_owner", {**base, "owner_kind": "CronJob" if owner else "<none>",
                            "owner_name": owner or "<none>", "owner_is_controller": "true"}, 1.0),
    ]
    for condition in ("Complete", "Failed"):
        for status in ("true", "false", "unknown"):
            value = 1.0 if (final == condition and status == "true") or (final != condition and status == "false") else 0.0
            out.append(("kube_job_status_condition",
                        {**base, "condition": condition, "status": status}, value))
    return out


def _firing(series: list[tuple[str, dict, float]]) -> set[str]:
    expr = _rules()["TestLookupBackupJobFailed"]["expr"]
    return {lbl["job_name"] for lbl, _ in _PromQL(expr, series).run()}


def test_a_failed_backup_job_alerts() -> None:
    cronjob = _cronjob(BACKUP / "cronjob-backup.yaml")
    name = cronjob["metadata"]["name"]
    restore = _cronjob(BACKUP / "cronjob-restore.yaml")["metadata"]["name"]
    # The scenario R-B45-R2-1 is about only exists with a retry allowed.
    assert cronjob["spec"]["jobTemplate"]["spec"]["backoffLimit"] >= 1
    assert cronjob["spec"]["successfulJobsHistoryLimit"] >= 1

    series = (
        # every pod failed, retries exhausted: the Job's Failed condition.
        _job_series(f"{name}-29301234", failed_pods=2, succeeded=0, final="Failed", owner=name)
        # first pod failed, the retry succeeded: a SUCCESSFUL backup.
        + _job_series(f"{name}-29301235", failed_pods=1, succeeded=1, final="Complete", owner=name)
        # still retrying: one failed pod, no terminal condition yet.
        + _job_series(f"{name}-29301236", failed_pods=1, succeeded=0, final=None, owner=name)
        # a clean success.
        + _job_series(f"{name}-29301237", failed_pods=0, succeeded=1, final="Complete", owner=name)
        # `kubectl create job --from=cronjob/...` with another name: owned.
        + _job_series("manual-backup", failed_pods=2, succeeded=0, final="Failed", owner=name)
        # a hand-made manual run named per the README, no owner.
        + _job_series(f"{name}-now", failed_pods=2, succeeded=0, final="Failed", owner=None)
        # the restore and an unrelated Job fail: not a backup.
        + _job_series(f"{restore}-29301234", failed_pods=2, succeeded=0, final="Failed", owner=restore)
        + _job_series("other-29301234", failed_pods=2, succeeded=0, final="Failed", owner="other")
    )
    assert _firing(series) == {f"{name}-29301234", "manual-backup", f"{name}-now"}
    rule = _rules()["TestLookupBackupJobFailed"]
    assert rule["labels"]["severity"] == "critical"
    assert rule.get("for", "0m") == "0m"


def test_the_failed_alert_keeps_the_job_and_namespace_labels() -> None:
    name = _cronjob(BACKUP / "cronjob-backup.yaml")["metadata"]["name"]
    series = (
        _job_series(f"{name}-1", failed_pods=2, succeeded=0, final="Failed", owner=name, namespace="a")
        + _job_series(f"{name}-1", failed_pods=0, succeeded=1, final="Complete", owner=name, namespace="b")
        # same Job name in two namespaces: the backup succeeded in c, an
        # unrelated Job failed in d. A join on job_name alone would page.
        + _job_series("nightly", failed_pods=0, succeeded=1, final="Complete", owner=name, namespace="c")
        + _job_series("nightly", failed_pods=2, succeeded=0, final="Failed", owner="other", namespace="d")
    )
    expr = _rules()["TestLookupBackupJobFailed"]["expr"]
    fired = _PromQL(expr, series).run()
    # the description templates both labels; a namespace-less join would
    # also let namespace b's successful Job match namespace a's failure.
    assert [lbl for lbl, _ in fired] == [{"namespace": "a", "job_name": f"{name}-1"}]


def test_no_successful_backup_in_26_hours_alerts_including_never() -> None:
    rule = _rules()["TestLookupBackupStale"]
    expr = _expr("TestLookupBackupStale")
    cronjob = _cronjob(BACKUP / "cronjob-backup.yaml")
    name = cronjob["metadata"]["name"]
    assert cronjob["spec"]["schedule"].split()[2:] == ["*", "*", "*"]  # daily

    stale = re.search(
        r'\(time\(\) - max by \(namespace, cronjob\) \(kube_cronjob_status_last_successful_time'
        r'\{cronjob="([^"]+)"\}\)\) > (\d+) \* 3600',
        expr,
    )
    assert stale, expr
    assert stale.group(1) == name
    hours = int(stale.group(2))
    assert 24 < hours <= 26  # a daily job, plus slack for one slow run

    # Never succeeded: the CronJob exists and has no success timestamp at all.
    never = re.search(
        r'kube_cronjob_created\{cronjob="([^"]+)"\}\) < time\(\) - (\d+) \* 3600 '
        r'unless on \(namespace, cronjob\) kube_cronjob_status_last_successful_time\{cronjob="([^"]+)"\}',
        expr,
    )
    assert never, expr
    assert never.group(1) == never.group(3) == name and int(never.group(2)) == hours
    assert " or " in expr
    assert rule["labels"]["severity"] == "critical"


# ── R-B45-5 ──────────────────────────────────────────────────────────────────


def _backup_egress_policy(docs: list[dict]) -> dict:
    return next(
        d for d in docs
        if d.get("kind") == "NetworkPolicy" and d["metadata"]["name"] == "allow-backup-egress"
    )


def _egress_grants(policy: dict) -> set[tuple]:
    """(peer app label or namespace, protocol, port) for every egress rule."""
    grants: set[tuple] = set()
    for rule in policy["spec"]["egress"]:
        assert rule.get("to"), f"an egress rule with no peer allows every address: {rule}"
        assert rule.get("ports"), f"an egress rule with no port allows every port: {rule}"
        for peer in rule["to"]:
            assert "ipBlock" not in peer, peer
            ns = (peer.get("namespaceSelector") or {}).get("matchLabels", {}).get("kubernetes.io/metadata.name")
            app = (peer.get("podSelector") or {}).get("matchLabels", {})
            key = ns or app.get("app")
            assert key, f"a peer that selects everything: {peer}"
            for port in rule["ports"]:
                grants.add((key, port["protocol"], port["port"]))
    return grants


def test_the_backup_pod_egresses_only_to_dns_and_the_stores() -> None:
    policy = _backup_egress_policy(_docs(BACKUP / "networkpolicy.yaml"))
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app": "testlookup-backup"}
    assert "Egress" in policy["spec"]["policyTypes"]
    assert _egress_grants(policy) == {
        ("kube-system", "UDP", 53), ("kube-system", "TCP", 53),
        ("kube-system", "UDP", 5353), ("kube-system", "TCP", 5353),
        ("openshift-dns", "UDP", 53), ("openshift-dns", "TCP", 53),
        ("openshift-dns", "UDP", 5353), ("openshift-dns", "TCP", 5353),
        ("testlookup-postgres", "TCP", 5432),
        ("testlookup-mongo", "TCP", 27017),
        ("testlookup-minio", "TCP", 9000),
    }


def _store_ports() -> dict[str, int]:
    """app label -> the port its Service targets, from k8s/base/services.yaml."""
    ports: dict[str, int] = {}
    for doc in _docs(BASE / "services.yaml"):
        if doc.get("kind") != "Service":
            continue
        app = (doc["spec"].get("selector") or {}).get("app")
        if app in {"testlookup-postgres", "testlookup-mongo", "testlookup-minio"}:
            first = doc["spec"]["ports"][0]
            ports[app] = int(first.get("targetPort", first["port"]))
    return ports


def test_the_store_ports_are_the_ones_the_stores_serve() -> None:
    grants = _egress_grants(_backup_egress_policy(_docs(BACKUP / "networkpolicy.yaml")))
    for app, port in _store_ports().items():
        assert (app, "TCP", port) in grants, (app, port)
    assert set(_store_ports()) == {"testlookup-postgres", "testlookup-mongo", "testlookup-minio"}


def _overlays_with_backup() -> list[Path]:
    overlays = []
    for kustomization in sorted((REPO_ROOT / "k8s" / "overlays").glob("*/kustomization.yaml")):
        doc = yaml.safe_load(kustomization.read_text(encoding="utf-8"))
        if any(str(c).rstrip("/").endswith("components/backup") for c in doc.get("components") or []):
            overlays.append(kustomization.parent)
    return overlays


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.parametrize("overlay", _overlays_with_backup(), ids=lambda p: p.name)
def test_every_overlay_with_the_backup_renders_the_restricted_egress(overlay: Path) -> None:
    rendered = subprocess.run(
        ["kubectl", "kustomize", str(overlay)], capture_output=True, text=True, timeout=120,
    )
    assert rendered.returncode == 0, rendered.stderr[-2000:]
    docs = [d for d in yaml.safe_load_all(rendered.stdout) if d]
    policy = _backup_egress_policy(docs)
    grants = _egress_grants(policy)
    assert ("testlookup-postgres", "TCP", 5432) in grants
    assert all(key != "0.0.0.0/0" for key, _, _ in grants)


def test_the_backup_component_is_included_by_the_in_cluster_store_overlays() -> None:
    assert {p.name for p in _overlays_with_backup()} >= {"homelab", "openshift-artifactory"}


# ── QA-B45-P5 ────────────────────────────────────────────────────────────────


def _monitors() -> list[dict]:
    kustomization = yaml.safe_load((MONITORING / "kustomization.yaml").read_text(encoding="utf-8"))
    docs: list[dict] = []
    for resource in kustomization["resources"]:
        docs += _docs(MONITORING / resource)
    return [d for d in docs if d.get("kind") in {"PodMonitor", "ServiceMonitor"}]


def _scraped_deployments() -> list[dict]:
    deployments = []
    for path in sorted(BASE.glob("*.yaml")):
        for doc in _docs(path):
            if doc.get("kind") != "Deployment":
                continue
            annotations = doc["spec"]["template"]["metadata"].get("annotations") or {}
            if str(annotations.get("prometheus.io/scrape", "")).lower() == "true":
                deployments.append(doc)
    return deployments


def _selects(monitor: dict, labels: dict) -> bool:
    wanted = monitor["spec"]["selector"].get("matchLabels") or {}
    return bool(wanted) and all(labels.get(k) == v for k, v in wanted.items())


def _job_of(endpoint: dict) -> str | None:
    for relabel in endpoint.get("relabelings") or []:
        if relabel.get("targetLabel") == "job" and relabel.get("action", "replace") == "replace":
            return relabel.get("replacement")
    return None


def test_the_monitors_are_podmonitors_in_the_opt_in_component() -> None:
    monitors = _monitors()
    assert {m["metadata"]["name"] for m in monitors} == {"testlookup-backend", "testlookup-worker"}
    base = yaml.safe_load((BASE / "kustomization.yaml").read_text(encoding="utf-8"))
    assert not any("monitoring" in str(r) for r in base.get("resources") or [])


def test_every_deployment_serving_metrics_is_scraped_on_its_real_port_and_path() -> None:
    deployments = _scraped_deployments()
    names = {d["metadata"]["name"] for d in deployments}
    assert {"testlookup-backend", "testlookup-worker-ingestion", "testlookup-worker-children"} <= names
    for deployment in deployments:
        template = deployment["spec"]["template"]
        labels = template["metadata"]["labels"]
        annotations = template["metadata"]["annotations"]
        port = int(annotations["prometheus.io/port"])
        path = annotations.get("prometheus.io/path", "/metrics")
        port_names = {
            p.get("name"): int(p["containerPort"])
            for c in template["spec"]["containers"] for p in c.get("ports") or []
        }
        covering = [
            endpoint
            for monitor in _monitors() if monitor["kind"] == "PodMonitor" and _selects(monitor, labels)
            for endpoint in monitor["spec"]["podMetricsEndpoints"]
        ]
        assert covering, f"{deployment['metadata']['name']} serves /metrics and no PodMonitor selects it"
        assert any(
            port_names.get(endpoint["port"]) == port and endpoint.get("path", "/metrics") == path
            for endpoint in covering
        ), (deployment["metadata"]["name"], port, path, covering, port_names)


def test_every_job_an_alert_selects_is_produced_by_a_monitor() -> None:
    produced = {
        _job_of(endpoint)
        for monitor in _monitors()
        for endpoint in monitor["spec"].get("podMetricsEndpoints") or monitor["spec"].get("endpoints") or []
    }
    selected = {
        job for rule in _rules().values() for job in re.findall(r'(?<![A-Za-z0-9_])job="([^"]+)"', rule["expr"])
    }
    assert selected, "no alert selects on job -- the check is vacuous"
    assert selected <= produced, selected - produced


#: Alert metrics served by an exporter the cluster runs, not by TestLookup.
EXTERNAL_EXPORTERS = {"kube_": "kube-state-metrics", "node_": "node-exporter"}


def test_every_alert_metric_is_scraped_by_a_monitor_or_named_as_external() -> None:
    kustomization = (MONITORING / "kustomization.yaml").read_text(encoding="utf-8")
    ours = {"testlookup-backend", "testlookup-worker"}
    produced = {_job_of(e) for m in _monitors() for e in m["spec"]["podMetricsEndpoints"]}
    assert ours <= produced
    for name, rule in _rules().items():
        metrics = set(re.findall(r"\b([a-z_][a-z0-9_]*)\s*(?:\{|\[)", rule["expr"])) - {"by", "on"}
        for metric in metrics:
            external = next((x for p, x in EXTERNAL_EXPORTERS.items() if metric.startswith(p)), None)
            if external:
                # Not ours to scrape: the component must say it depends on it.
                assert external in kustomization, (name, metric, external)
