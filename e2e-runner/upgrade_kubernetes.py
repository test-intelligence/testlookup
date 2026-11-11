"""Own-namespace Kubernetes adapter for a destructive, disposable upgrade proof.

Snapshots stay in the datastore containers. Only references, checksums, health
projections and image identities leave the namespace; credentials never do.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/release"))
from release_manifest import validate_manifest  # noqa: E402


class KubernetesUpgradeError(RuntimeError):
    """An isolated upgrade operation could not be verified."""


class KubernetesUpgrade:
    APP_SERVICES = ("backend", "frontend", "mcp", "beat", "worker-critical",
                    "worker-ingestion", "worker-ai", "worker-default", "worker-children")
    DATA_SERVICES = ("postgres", "mongo", "redis", "minio", "chromadb")
    STORES = {"postgres": "/tmp/tl-upgrade-postgres.dump",
              "mongo": "/tmp/tl-upgrade-mongo.archive.gz",
              "minio": "/tmp/upgrade/tl-upgrade-objects.tar"}
    INFRA_IMAGES = {
        "postgres": "public.ecr.aws/docker/library/postgres@sha256:aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139",
        "mongo": "public.ecr.aws/docker/library/mongo@sha256:8bb4442761a63a8dda452744082cdac9644b429807eb977b42324a63cef59ca4",
        "redis": "docker.io/valkey/valkey@sha256:2602ace2be6569bc2bbb06f873776516ab9089821525be34f4305c67eb9db02a",
        "minio": "ghcr.io/versity/versitygw@sha256:30292fc2eeacc67a36993b01f7a7a5e3361a19cced0e80c1d71cfa2a4b0a2499",
        "chromadb": "docker.io/chromadb/chroma@sha256:1e0b73a187a28757c572acba508c46f48c9e8b0acaf5c20e6d95cdedce1acdf6",
    }

    def __init__(self, prior, candidate, out, context="default"):
        self.prior = validate_manifest(prior)
        self.candidate = validate_manifest(candidate)
        if not isinstance(context, str) or not context or context.startswith("-"):
            raise ValueError("An explicit Kubernetes context is required")
        self.context = context
        self.out = Path(out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.namespace = "testlookup-e2e-upgrade-" + uuid.uuid4().hex[:12]
        self.project = self.namespace
        self.uid = None
        self._namespace_create_attempted = False
        self._quiesced = False
        self._snapshot = None
        self._installed = False
        self._migrated_ref = None
        self._failed_migration = False
        self._cleaned = False
        self._indexes = {}
        self._migration_number = 0
        self._last_job = None
        self._archive_uid = None
        self._archive_node = None
        self._archive_claim_uid = None
        self.settings = {key: secrets.token_hex(32) for key in (
            "POSTGRES_PASSWORD MONGO_PASSWORD REDIS_PASSWORD APP_SECRET_KEY "
            "JWT_SECRET_KEY MINIO_ACCESS_KEY MINIO_SECRET_KEY WEBHOOK_SECRET "
            "INITIAL_ADMIN_PASSWORD").split()}

    @property
    def namespace_uid(self):
        return self.uid

    def _write(self, name, value):
        (self.out / name).write_text(json.dumps(value, indent=2), encoding="utf-8")

    def _redact(self, text):
        for value in self.settings.values():
            text = text.replace(value, "[REDACTED]")
        return re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                      "[REDACTED_TOKEN]", text)

    def _run(self, command, *, input=None, check=True, timeout=90,
             log_output=False):
        try:
            result = subprocess.run(command, input=input, text=True,
                                    encoding="utf-8", errors="replace",
                                    capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            raise KubernetesUpgradeError(
                "Isolated operation could not complete (" + type(error).__name__ + ")."
            ) from None
        with (self.out / "kubernetes-private.log").open("a", encoding="utf-8") as log:
            # stdin may contain Secret values or authenticated helper source.
            log.write(self._redact(json.dumps(command)) + "\n")
            log.write("exit=" + str(result.returncode) + "\n")
            if log_output:
                log.write(self._redact(result.stdout + result.stderr) + "\n")
        if check and result.returncode:
            raise KubernetesUpgradeError(
                "Isolated operation failed with exit " + str(result.returncode) +
                "; credential-bearing output suppressed.")
        return result

    def _k(self, *arguments, **kwargs):
        return self._run(["kubectl", "--context", self.context,
                          "--request-timeout=30s", *arguments], **kwargs)

    def _get(self, resource, *names):
        return json.loads(self._k("-n", self.namespace, "get", resource,
                                  *names, "-o", "json").stdout)

    def _guard(self):
        if not re.fullmatch(r"testlookup-e2e-upgrade-[0-9a-f]{12}", self.namespace) or not self.uid:
            raise KubernetesUpgradeError("Owned upgrade namespace identity is unavailable")
        current = self._get("namespace", self.namespace)
        labels = current["metadata"].get("labels", {})
        if (current["metadata"]["uid"] != self.uid or
                labels.get("testlookup.io/e2e-run") != self.namespace or
                labels.get("testlookup.io/disposable") != "true"):
            raise KubernetesUpgradeError("Upgrade namespace UID or ownership changed")

    def _apply(self, items):
        self._guard()
        for item in items:
            if (item["metadata"].get("namespace") != self.namespace or
                    item["kind"] not in {"Secret", "ServiceAccount", "ConfigMap", "Service",
                                         "Deployment", "PersistentVolumeClaim", "ResourceQuota",
                                         "LimitRange", "NetworkPolicy", "Job", "Pod"}):
                raise KubernetesUpgradeError("Manifest escapes the owned namespace")
        self._k("apply", "-f", "-", input=yaml.safe_dump_all(items, sort_keys=False))

    @staticmethod
    def _env(container, **values):
        env = {entry["name"]: entry for entry in container.get("env", [])}
        for name, value in values.items():
            env[name] = {"name": name, "value": str(value)}
        container["env"] = list(env.values())

    def _render(self, manifest):
        raw = self._run(["kubectl", "kustomize", str(ROOT / "k8s/overlays/homelab")]).stdout
        infrastructure, applications = [], []
        allowed = {"ServiceAccount", "ConfigMap", "Service", "PersistentVolumeClaim", "Deployment"}
        for item in yaml.safe_load_all(raw):
            if not item or item["kind"] not in allowed:
                continue
            name, kind = item["metadata"]["name"], item["kind"]
            if any(word in name for word in ("backup", "restore", "ollama")):
                continue
            item["metadata"]["namespace"] = self.namespace
            item["metadata"].setdefault("labels", {})["testlookup.io/e2e-run"] = self.namespace
            if kind == "ServiceAccount":
                item["automountServiceAccountToken"] = False
            if kind == "PersistentVolumeClaim":
                item["spec"]["resources"]["requests"]["storage"] = (
                    "2Gi" if name in {"postgres-data", "mongo-data"} else "1Gi")
            if kind == "ConfigMap" and name == "testlookup-config":
                item["data"].update(
                    APP_ENV="development", DEV_AUTO_LOGIN_ENABLED="true",
                    SMTP_ENABLED="false", SMTP_HOST="", AI_OFFLINE_MODE="true",
                    PUBLIC_BASE_URL="http://testlookup-frontend", CORS_ORIGINS="http://testlookup-frontend",
                    TRUSTED_PROXY_IPS="10.42.0.0/16")
                for key in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
                    item["data"].pop(key, None)
            if kind == "Deployment":
                service = name.removeprefix("testlookup-")
                pod = item["spec"]["template"]["spec"]
                pod["automountServiceAccountToken"] = False
                item["spec"]["replicas"] = 1
                item["spec"]["template"]["metadata"].setdefault("labels", {})["testlookup.io/e2e-run"] = self.namespace
                for container in pod.get("containers", []):
                    if service in self.APP_SERVICES:
                        component = service if service in {"frontend", "mcp"} else "backend"
                        container["image"] = manifest["images"][component]["source_ref"]
                        container["imagePullPolicy"] = "IfNotPresent"
                        if component == "backend":
                            self._env(container, APP_ENV="development", DEV_AUTO_LOGIN_ENABLED="true")
                        if service.startswith("worker-"):
                            container["command"] = ["--concurrency=1" if x.startswith("--concurrency=") else x
                                                    for x in container["command"]]
                            self._env(container, PG_PROCESSES_PER_POD="1")
                            container["resources"] = {"requests": {"cpu": "100m", "memory": "256Mi"},
                                                      "limits": {"cpu": "1", "memory": "2Gi"}}
                        if service == "mcp":
                            self._env(container, TESTLOOKUP_MCP_ALLOWED_HOSTS=(
                                "127.0.0.1:*,localhost:*,testlookup-mcp:8002,testlookup-mcp." +
                                self.namespace + ".svc.cluster.local:8002"))
                    elif service in self.INFRA_IMAGES:
                        container["image"] = self.INFRA_IMAGES[service]
                    if service == "mongo":
                        self._env(container, MONGO_INITDB_ROOT_USERNAME="testlookup",
                                  MONGO_INITDB_DATABASE="testlookup_logs")
                        container["env"].append({"name": "MONGO_INITDB_ROOT_PASSWORD", "valueFrom": {
                            "secretKeyRef": {"name": "testlookup-secrets", "key": "MONGO_PASSWORD"}}})
                    if service == "redis":
                        container["env"] = [{"name": "REDIS_PASSWORD", "valueFrom": {
                            "secretKeyRef": {"name": "testlookup-secrets", "key": "REDIS_PASSWORD"}}}]
                        container["command"] = ["sh", "-eu", "-c", 'exec valkey-server --appendonly yes --appendfsync everysec --maxmemory 512mb --maxmemory-policy volatile-lru --requirepass "$REDIS_PASSWORD"']
                        for probe in ("readinessProbe", "livenessProbe"):
                            container[probe]["exec"]["command"] = ["sh", "-c", 'VALKEYCLI_AUTH="$REDIS_PASSWORD" REDISCLI_AUTH="$REDIS_PASSWORD" valkey-cli ping']
                (applications if service in self.APP_SERVICES else infrastructure).append(item)
            else:
                infrastructure.append(item)
        infrastructure.extend([
            {"apiVersion": "v1", "kind": "ResourceQuota", "metadata": {"name": "upgrade-quota", "namespace": self.namespace},
             "spec": {"hard": {"requests.cpu": "8", "requests.memory": "16Gi", "limits.cpu": "24",
                               "limits.memory": "32Gi", "requests.storage": "10Gi", "persistentvolumeclaims": "6", "pods": "35"}}},
            {"apiVersion": "v1", "kind": "LimitRange", "metadata": {"name": "upgrade-defaults", "namespace": self.namespace},
             "spec": {"limits": [{"type": "Container", "default": {"cpu": "500m", "memory": "1Gi"},
                                  "defaultRequest": {"cpu": "50m", "memory": "64Mi"}}]}},
            {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
             "metadata": {"name": "isolated-upgrade-only", "namespace": self.namespace},
             "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"],
                      "ingress": [{"from": [{"podSelector": {}}]}],
                      "egress": [{"to": [{"podSelector": {}}]}, {"to": [{"namespaceSelector": {
                          "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
                          "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]}]}}])
        return infrastructure, applications

    def _wait_ready(self, services, timeout=360):
        deadline = time.monotonic() + timeout
        expected = {"testlookup-" + service for service in services}
        while time.monotonic() < deadline:
            rows = self._get("deployments")["items"]
            ready = {d["metadata"]["name"] for d in rows
                     if d.get("status", {}).get("availableReplicas", 0) >= 1
                     and d.get("status", {}).get("updatedReplicas", 0) == d["spec"]["replicas"]
                     and d.get("status", {}).get("observedGeneration", 0) == d["metadata"]["generation"]}
            if expected <= ready:
                return
            time.sleep(2)
        raise KubernetesUpgradeError("Owned deployment readiness timed out")

    def install(self):
        if self.uid:
            raise KubernetesUpgradeError("This adapter has already created its namespace")
        namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {
            "name": self.namespace, "labels": {"testlookup.io/disposable": "true", "testlookup.io/e2e-run": self.namespace}}}
        self._namespace_create_attempted = True
        created = json.loads(self._k("create", "-f", "-", "-o", "json", input=json.dumps(namespace)).stdout)
        self.uid = created["metadata"]["uid"]
        self._write("namespace.json", {"context": self.context, "namespace": self.namespace,
                                       "uid": self.uid, "created_at": created["metadata"]["creationTimestamp"]})
        values = dict(self.settings)
        values.update(DATABASE_URL="postgresql+asyncpg://testlookup_user:" + values["POSTGRES_PASSWORD"] +
                      "@testlookup-postgres:5432/testlookup", MONGO_URI="mongodb://testlookup:" +
                      values["MONGO_PASSWORD"] + "@testlookup-mongo:27017/testlookup_logs?authSource=admin")
        for key, database in (("REDIS_URL", 0), ("CELERY_BROKER_URL", 0), ("CELERY_RESULT_BACKEND", 1)):
            values[key] = "redis://:" + values["REDIS_PASSWORD"] + "@testlookup-redis:6379/" + str(database)
        # Creating instead of apply avoids a second credential copy in annotations.
        self._guard()
        self._k("create", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                "metadata": {"name": "testlookup-secrets", "namespace": self.namespace,
                             "labels": {"testlookup.io/e2e-run": self.namespace}}, "stringData": values}))
        infrastructure, _ = self._render(self.prior)
        self._apply(infrastructure)
        self._wait_ready(self.DATA_SERVICES)
        self._create_archive_helper()
        self._quiesced = True
        if not self.migrate(self.prior):
            raise KubernetesUpgradeError("Prior-release database initialization failed")
        initialized = self._job(self.prior, ["sh", "-eu", "-c"], [
            'ADMIN_PASSWORD="$INITIAL_ADMIN_PASSWORD" ADMIN_PASSWORD_GENERATED=true python /app/scripts/create_admin.py && python /app/scripts/s3_setup.py'], "initialize")
        if not initialized:
            raise KubernetesUpgradeError("Prior-release admin or object-store initialization failed")
        self.activate(self.prior)
        self.exec_python((ROOT / "e2e-runner/bootstrap_admin.py").read_text(encoding="utf-8"))
        self._installed = True

    def exec_python(self, source):
        self._guard()
        result = self._k("-n", self.namespace, "exec", "-i", "deployment/testlookup-backend",
                         "-c", "backend", "--", "python", "-", input=source,
                         timeout=180, check=False)
        classified = [line for line in result.stdout.splitlines() if line.startswith("UPGRADE_JSON=")]
        if result.returncode and len(classified) != 1:
            raise KubernetesUpgradeError("Upgrade helper failed; credential-bearing diagnostics suppressed")
        return result.stdout

    def _create_archive_helper(self):
        minio = [pod for pod in self._get("pods")["items"]
                 if pod["metadata"].get("labels", {}).get("app") == "testlookup-minio"
                 and not pod["metadata"].get("deletionTimestamp")]
        if len(minio) != 1:
            raise KubernetesUpgradeError("Object-store node identity is ambiguous")
        self._archive_node = minio[0]["spec"]["nodeName"]
        self._archive_claim_uid = self._get("pvc", "minio-data")["metadata"]["uid"]
        helper = {"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": "upgrade-archive", "namespace": self.namespace,
            "labels": {"app": "testlookup-upgrade-archive", "testlookup.io/e2e-run": self.namespace}},
            "spec": {"nodeName": self._archive_node, "automountServiceAccountToken": False,
                     "restartPolicy": "Always", "containers": [{
                         "name": "archive", "image": self.prior["images"]["backend"]["source_ref"],
                         "command": ["sh", "-eu", "-c", "mkdir -p /tmp/upgrade; exec sleep infinity"],
                         "volumeMounts": [{"name": "data", "mountPath": "/data"},
                                          {"name": "upgrade-snapshots", "mountPath": "/tmp/upgrade"}],
                         "securityContext": {"runAsUser": 0, "runAsGroup": 0, "runAsNonRoot": False,
                                             "allowPrivilegeEscalation": False,
                                             "capabilities": {"drop": ["ALL"], "add": ["CHOWN", "FOWNER", "DAC_OVERRIDE"]}},
                         "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                                       "limits": {"cpu": "1", "memory": "1Gi"}}}],
                     "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "minio-data"}},
                                 {"name": "upgrade-snapshots", "emptyDir": {}}]}}
        self._apply([helper])
        current = self._get("pod", "upgrade-archive")
        self._archive_uid = current["metadata"]["uid"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = self._get("pod", "upgrade-archive")
            statuses = current.get("status", {}).get("containerStatuses", [])
            if len(statuses) == 1 and statuses[0].get("ready"):
                self._write("archive-helper.json", {"namespace": self.namespace, "uid": self._archive_uid,
                            "node": self._archive_node, "claim": "minio-data",
                            "claim_uid": self._archive_claim_uid,
                            "image": self.prior["images"]["backend"]["source_ref"]})
                return
            time.sleep(2)
        raise KubernetesUpgradeError("Owned archive helper did not become ready")

    def _registry(self, reference):
        if reference not in self._indexes:
            index = json.loads(self._run(["podman", "manifest", "inspect", reference]).stdout)
            children = [m["digest"] for m in index.get("manifests", [])
                        if m.get("platform", {}).get("os") == "linux" and
                        m.get("platform", {}).get("architecture") not in {None, "unknown"}]
            self._indexes[reference] = children
            self._write("registry-index-" + reference.rsplit(":", 1)[-1][:16] + ".json", index)
        return self._indexes[reference]

    def observe(self, manifest):
        manifest = validate_manifest(manifest)
        self._guard()
        health = json.loads(self.exec_python(
            "import json,urllib.request\n"
            "def get(path):\n"
            "    with urllib.request.urlopen('http://127.0.0.1:8000/'+path,timeout=30) as response:\n"
            "        return json.load(response)\n"
            "print(json.dumps({'build':get('health/version'),'ready':get('health/ready')}))\n"))
        pods = self._get("pods")["items"]
        images = []
        for service in self.APP_SERVICES:
            component = service if service in {"frontend", "mcp"} else "backend"
            expected = manifest["images"][component]["source_ref"]
            children = self._registry(expected)
            selected = [p for p in pods if p["metadata"].get("labels", {}).get("app") == "testlookup-" + service
                        and p["status"]["phase"] == "Running" and not p["metadata"].get("deletionTimestamp")
                        and p["metadata"].get("ownerReferences", [{}])[0].get("kind") != "Job"]
            if not selected:
                raise KubernetesUpgradeError("No running owned application pod for " + service)
            for pod in selected:
                if len(pod.get("status", {}).get("containerStatuses", [])) != len(pod["spec"]["containers"]):
                    raise KubernetesUpgradeError("Incomplete application container observation")
                for container in pod.get("status", {}).get("containerStatuses", []):
                    running = container.get("imageID", "").rsplit("@", 1)[-1]
                    configured = next(c["image"] for c in pod["spec"]["containers"] if c["name"] == container["name"])
                    images.append({"service": service, "component": component, "candidate": expected,
                                   "running": running, "image_id": container.get("imageID"),
                                   "pod": pod["metadata"]["name"], "children": children,
                                   "matched": container["ready"] and configured == expected and
                                   running in [expected.rsplit("@", 1)[-1], *children]})
        # Migration pods may intentionally use the candidate while prior apps
        # remain running. Verify them against their own immutable Job image.
        for pod in pods:
            if not any(owner.get("kind") == "Job" for owner in pod["metadata"].get("ownerReferences", [])):
                continue
            for container in pod.get("status", {}).get("containerStatuses", []):
                expected = next(c["image"] for c in pod["spec"]["containers"] if c["name"] == container["name"])
                allowed = {m["images"]["backend"]["source_ref"] for m in (self.prior, self.candidate)}
                running = container.get("imageID", "").rsplit("@", 1)[-1]
                matched = expected in allowed and running in [expected.rsplit("@", 1)[-1], *self._registry(expected)]
                images.append({"service": pod["metadata"]["name"], "component": "backend", "candidate": expected,
                               "running": running, "image_id": container.get("imageID"), "matched": matched})
        matched = (health["build"].get("build", {}).get("revision") == manifest["source_sha"]
                   and health["ready"].get("status") == "ready"
                   and all(health["ready"].get("checks", {}).get(store, {}).get("status") == "ok"
                           for store in ("postgres", "mongo", "redis"))
                   and all(image["matched"] for image in images))
        observation = {"source_sha": manifest["source_sha"], **health, "images": images, "matched": matched,
                       "namespace": self.namespace, "namespace_uid": self.uid}
        self._write("observation-" + uuid.uuid4().hex[:8] + ".json", observation)
        if not matched:
            raise KubernetesUpgradeError("Running upgrade build, images or readiness did not match")
        return observation

    def quiesce(self):
        self._guard()
        self._k("-n", self.namespace, "scale", *["deployment/testlookup-" + x for x in self.APP_SERVICES], "--replicas=0")
        self._quiesced = True
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            pods = self._get("pods")["items"]
            writers = [p for p in pods if p["metadata"].get("labels", {}).get("app") in
                       {"testlookup-" + x for x in self.APP_SERVICES} and
                       p["status"]["phase"] not in {"Succeeded", "Failed"}]
            if not writers:
                self._require_quiesced()
                self._write("quiesce-" + uuid.uuid4().hex[:8] + ".json", {"writers": [], "verified": True})
                return
            time.sleep(2)
        raise KubernetesUpgradeError("Owned application writers did not stop")

    def _require_quiesced(self):
        self._guard()
        deployments = self._get("deployments")["items"]
        pods = self._get("pods")["items"]
        if (not self._quiesced or any(d["spec"]["replicas"] != 0 for d in deployments
                                     if d["metadata"]["name"] in {"testlookup-" + x for x in self.APP_SERVICES})
                or any(p["metadata"].get("labels", {}).get("app") in {"testlookup-" + x for x in self.APP_SERVICES}
                       and p["status"]["phase"] not in {"Succeeded", "Failed"} for p in pods)
                or any(j.get("status", {}).get("active", 0) for j in self._get("jobs")["items"])):
            raise KubernetesUpgradeError("All owned application and migration writers must be quiesced")

    def _guard_stores(self):
        self._require_quiesced()
        for service in ("postgres", "mongo", "minio", "redis"):
            deployment = self._get("deployment", "testlookup-" + service)
            pod = deployment["spec"]["template"]["spec"]
            volumes = {v["name"]: v for v in pod.get("volumes", [])}
            data = volumes.get("data", {})
            if (any("hostPath" in v for v in volumes.values()) or
                    data.get("persistentVolumeClaim", {}).get("claimName") != service + "-data"):
                raise KubernetesUpgradeError("Owned datastore storage binding changed")
            claim = self._get("pvc", service + "-data")
            if claim["metadata"].get("labels", {}).get("testlookup.io/e2e-run") != self.namespace:
                raise KubernetesUpgradeError("Datastore claim ownership changed")
        self._guard_archive()

    def _guard_archive(self):
        self._guard()
        helper = self._get("pod", "upgrade-archive")
        pod = helper["spec"]
        archive = pod["containers"][0]
        volumes = {v["name"]: v for v in pod.get("volumes", [])}
        expected = self.prior["images"]["backend"]["source_ref"]
        claim = self._get("pvc", "minio-data")
        if (helper["metadata"]["uid"] != self._archive_uid or
                helper["metadata"].get("labels", {}).get("testlookup.io/e2e-run") != self.namespace or
                pod.get("nodeName") != self._archive_node or pod.get("automountServiceAccountToken") is not False or
                archive["name"] != "archive" or archive["image"] != expected or
                any("hostPath" in volume for volume in volumes.values()) or
                volumes.get("data", {}).get("persistentVolumeClaim", {}).get("claimName") != "minio-data" or
                claim["metadata"]["uid"] != self._archive_claim_uid or
                claim["metadata"].get("labels", {}).get("testlookup.io/e2e-run") != self.namespace or
                not any(m["name"] == "data" and m["mountPath"] == "/data" for m in archive["volumeMounts"]) or
                volumes.get("upgrade-snapshots", {}).get("emptyDir") != {}):
            raise KubernetesUpgradeError("Owned GNU archive helper identity or storage binding changed")
        statuses = helper.get("status", {}).get("containerStatuses", [])
        if len(statuses) != 1 or statuses[0].get("imageID", "").rsplit("@", 1)[-1] not in [expected.rsplit("@", 1)[-1], *self._registry(expected)]:
            raise KubernetesUpgradeError("GNU archive helper running digest mismatch")

    def _store_exec(self, service, source):
        self._guard()
        if service == "minio":
            self._guard_archive()
        return self._k("-n", self.namespace, "exec", "pod/upgrade-archive" if service == "minio" else "deployment/testlookup-" + service,
                       "-c", "archive" if service == "minio" else service,
                       "--", "sh", "-eu", "-c", source, timeout=180).stdout

    def _archive_metadata(self, service, path):
        if path != self.STORES.get(service):
            raise KubernetesUpgradeError("Unexpected snapshot path")
        output = self._store_exec(service, f"test -f {path}; sha256sum {path}; wc -c < {path}")
        lines = output.strip().splitlines()
        digest, size = lines[-2].split()[0], int(lines[-1])
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or size <= 0:
            raise KubernetesUpgradeError("Snapshot checksum or size unavailable")
        return {"path": path, "size": size, "sha256": digest}

    def _object_tree_metadata(self):
        # ACL and ownership xattrs can contain credential identifiers. Hash
        # them within the owned pod, never serialize their values to the host.
        source = '''import hashlib,json,os,stat
assert os.path.realpath('/data') == '/data'
rows=[]
files=0
xattrs=0
for parent,dirs,names in os.walk('/data',followlinks=False):
    dirs.sort()
    for name in ['.'] if parent=='/data' else []:
        names.append(name)
    for name in sorted(set(dirs+names)):
        path=os.path.join(parent,name)
        current=os.lstat(path)
        attrs={key:hashlib.sha256(os.getxattr(path,key,follow_symlinks=False)).hexdigest()
               for key in sorted(os.listxattr(path,follow_symlinks=False))}
        xattrs+=len(attrs)
        row={'path':os.path.relpath(path,'/data'),'mode':current.st_mode,'uid':current.st_uid,
             'gid':current.st_gid,'mtime_ns':current.st_mtime_ns,'xattrs':attrs}
        if stat.S_ISREG(current.st_mode):
            digest=hashlib.sha256()
            with open(path,'rb') as stream:
                while block:=stream.read(1024*1024): digest.update(block)
            row['body_sha256']=digest.hexdigest()
            files+=1
        elif stat.S_ISLNK(current.st_mode):
            row['link_sha256']=hashlib.sha256(os.readlink(path).encode()).hexdigest()
        rows.append(row)
raw=json.dumps(sorted(rows,key=lambda row:row['path']),sort_keys=True,separators=(',',':')).encode()
print(json.dumps({'sha256':hashlib.sha256(raw).hexdigest(),'entries':len(rows),'files':files,'xattrs':xattrs,
                  'fields':'relative paths, types, modes, uid/gid, nanosecond mtimes, file bodies, link targets and all xattrs'}))
'''
        self._guard_archive()
        result = self._k("-n", self.namespace, "exec", "-i", "pod/upgrade-archive",
                         "-c", "archive", "--", "python", "-", input=source, timeout=180)
        return json.loads(result.stdout)

    def snapshot(self):
        self._guard_stores()
        object_tree = self._object_tree_metadata()
        self._store_exec("minio", 'tar --version | head -n1 | grep -F "GNU tar"; command -v sha256sum; command -v find; test "$(realpath /data)" = /data')
        self._store_exec("postgres", 'export PGPASSWORD="$POSTGRES_PASSWORD"; '
                         'pg_dump --format=custom --no-owner --no-acl --username "$POSTGRES_USER" '
                         f'--dbname "$POSTGRES_DB" --file {self.STORES["postgres"]}')
        self._store_exec("mongo", 'mongodump --host 127.0.0.1 --username "$MONGO_INITDB_ROOT_USERNAME" '
                         '--password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin '
                         f'--db "$MONGO_INITDB_DATABASE" --gzip --archive={self.STORES["mongo"]}')
        self._store_exec("minio", f'tar --format=pax --xattrs --xattrs-include="*" --acls --numeric-owner -C /data -cpf {self.STORES["minio"]} .')
        self._snapshot = {key: self._archive_metadata(key, path) for key, path in self.STORES.items()}
        self._snapshot["redis"] = "Not snapshotted; flushed during restore."
        self._snapshot["object_metadata"] = "GNU tar preserves xattrs, POSIX ACLs, numeric ownership and modes; archive stays in the pinned owned helper pod /tmp."
        if self._object_tree_metadata() != object_tree:
            raise KubernetesUpgradeError("Object filesystem changed while quiesced snapshot was captured")
        self._snapshot["object_tree"] = object_tree
        self._write("snapshot-metadata.json", self._snapshot)
        return self._snapshot

    def _job(self, manifest, command, args=None, purpose="migrate"):
        self._guard()
        self._migration_number += 1
        name = "upgrade-" + purpose + "-" + str(self._migration_number)
        self._last_job = name
        container = {"name": "migrate", "image": manifest["images"]["backend"]["source_ref"],
                     "imagePullPolicy": "IfNotPresent", "command": command,
                     "envFrom": [{"configMapRef": {"name": "testlookup-config"}}, {"secretRef": {"name": "testlookup-secrets"}}],
                     "env": [{"name": "PG_PROCESS_ROLE", "value": "operation"}, {"name": "PG_PROCESSES_PER_POD", "value": "1"},
                             {"name": "PG_POOL_SIZE", "value": "1"}, {"name": "PG_MAX_OVERFLOW", "value": "0"}],
                     "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}}}
        if args is not None:
            container["args"] = args
        # A newly scheduled Pod can start before its Service/CNI path settles,
        # even when the datastore Deployment was ready for an earlier Pod.
        # Probe only an authenticated SELECT here; never retry migrations or
        # application mutations after they have started.
        wait_source = '''import asyncio,os,time
import asyncpg
from sqlalchemy.engine import make_url
url=make_url(os.environ['DATABASE_URL'])
assert (url.host,url.database,url.username)==('testlookup-postgres','testlookup','testlookup_user')
async def main():
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        connection=None
        try:
            connection=await asyncpg.connect(host=url.host,port=url.port or 5432,user=url.username,
                password=url.password,database=url.database,timeout=5,ssl=False)
            assert await connection.fetchval('SELECT 1')==1
            print('Owned database authenticated readiness verified')
            return
        except (OSError,asyncpg.PostgresError,TimeoutError):
            await asyncio.sleep(2)
        finally:
            if connection is not None: await connection.close()
    raise RuntimeError('Owned database authenticated readiness timed out')
asyncio.run(main())
'''
        wait_container = {"name": "wait-for-postgres", "image": container["image"],
                          "command": ["python", "-c", wait_source],
                          "envFrom": container["envFrom"], "resources": container["resources"]}
        job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": self.namespace,
               "labels": {"testlookup.io/e2e-run": self.namespace}}, "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 600,
               "template": {"metadata": {"labels": {"app": "testlookup-backend", "testlookup.io/e2e-run": self.namespace}},
               "spec": {"automountServiceAccountToken": False, "restartPolicy": "Never", "containers": [container],
                        "initContainers": [wait_container],
                        "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True}}}}}
        self._apply([job])
        deadline = time.monotonic() + 630
        while time.monotonic() < deadline:
            current = self._get("job", name)
            conditions = {x["type"]: x["status"] for x in current.get("status", {}).get("conditions", [])}
            if conditions.get("Complete") == "True" or conditions.get("Failed") == "True":
                succeeded = conditions.get("Complete") == "True"
                pods = self._get("pods", "-l", "job-name=" + name)["items"]
                terminations = [{"pod": p["metadata"]["name"], "container": c["name"],
                                 "exit_code": c.get("state", {}).get("terminated", {}).get("exitCode"),
                                 "reason": c.get("state", {}).get("terminated", {}).get("reason")}
                                for p in pods for c in p.get("status", {}).get("containerStatuses", [])]
                if not succeeded:
                    diagnostics = []
                    for name_of_container in ("wait-for-postgres", "migrate"):
                        diagnostic = self._k("-n", self.namespace, "logs", "job/" + name,
                                             "-c", name_of_container, check=False)
                        diagnostics.append(name_of_container + "\n" + diagnostic.stdout + diagnostic.stderr)
                    (self.out / (name + "-private.log")).write_text(
                        self._redact("\n".join(diagnostics)), encoding="utf-8")
                self._write(name + ".json", {"job": name, "source_sha": manifest["source_sha"],
                            "backend_image": container["image"], "command": command, "succeeded": succeeded,
                            "conditions": current.get("status", {}).get("conditions", []),
                            "terminations": terminations})
                return succeeded
            time.sleep(2)
        raise KubernetesUpgradeError("Owned migration Job timed out")

    def migrate(self, manifest, negative=False):
        manifest = validate_manifest(manifest)
        if not negative:
            self._require_quiesced()
            if self._installed and not self._snapshot:
                raise KubernetesUpgradeError("A verified owned snapshot is required before upgrade migration")
        revision = "tl_e2e_nonexistent" if negative else "head"
        succeeded = self._job(manifest, ["alembic", "upgrade", revision])
        if negative and not succeeded:
            pods = self._get("pods", "-l", "job-name=" + self._last_job)["items"]
            terminated = [c.get("state", {}).get("terminated", {}) for pod in pods
                          for c in pod.get("status", {}).get("containerStatuses", []) if c["name"] == "migrate"]
            logs = self._redact(self._k("-n", self.namespace, "logs", "job/" + self._last_job,
                                        "-c", "migrate").stdout)
            rejected_revision = (len(terminated) == 1 and terminated[0].get("exitCode") == 255
                                 and "Can't locate revision identified by 'tl_e2e_nonexistent'" in logs)
            # Alembic's CommandError exit is normally 255, but some entrypoints
            # return 1; require the exact diagnostic in either case.
            rejected_revision = rejected_revision or (
                len(terminated) == 1 and terminated[0].get("exitCode") == 1
                and "Can't locate revision identified by 'tl_e2e_nonexistent'" in logs)
            if not rejected_revision:
                raise KubernetesUpgradeError("Negative migration did not exercise the expected Alembic revision rejection")
            self._write("negative-migration-control.json", {
                "job": self._last_job, "revision": revision, "expected_revision_rejection": True,
                "exit_code": terminated[0]["exitCode"], "raw_logs_retained": False})
        if not negative:
            self._migrated_ref = manifest["images"]["backend"]["source_ref"] if succeeded else None
        self._failed_migration = not succeeded
        self._write("migration-" + uuid.uuid4().hex[:8] + ".json", {
            "source_sha": manifest["source_sha"], "negative": negative, "revision": revision,
            "succeeded": succeeded, "app_images_changed": False})
        return succeeded

    def activate(self, manifest):
        manifest = validate_manifest(manifest)
        expected = manifest["images"]["backend"]["source_ref"]
        if self._migrated_ref != expected or self._failed_migration:
            raise KubernetesUpgradeError("Successful migration or validated restore is required before activation")
        _, applications = self._render(manifest)
        self._apply(applications)
        self._wait_ready(self.APP_SERVICES)
        self._quiesced = False
        self.observe(manifest)

    def restore(self):
        self._guard_stores()
        if not self._snapshot:
            raise KubernetesUpgradeError("An owned snapshot is required before restore")
        # Check every archive before the first destructive operation.
        for key, path in self.STORES.items():
            if self._archive_metadata(key, path) != self._snapshot[key]:
                raise KubernetesUpgradeError("Snapshot checksum or size changed; nothing restored")
        # POSIX gateways retain their backend working-directory inode. Stop
        # the server before replacing its tree and start it against the new
        # tree only after the independent filesystem oracle matches.
        self._k("-n", self.namespace, "scale", "deployment/testlookup-minio", "--replicas=0")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if not [pod for pod in self._get("pods")["items"]
                    if pod["metadata"].get("labels", {}).get("app") == "testlookup-minio"]:
                break
            time.sleep(2)
        else:
            raise KubernetesUpgradeError("Owned object server did not stop before offline restore")
        self._store_exec("postgres", 'test "$POSTGRES_DB" = testlookup; test "$POSTGRES_USER" = testlookup_user; '
                         'export PGPASSWORD="$POSTGRES_PASSWORD"; '
                         'dropdb --if-exists --force --username "$POSTGRES_USER" "$POSTGRES_DB"; '
                         'createdb --username "$POSTGRES_USER" "$POSTGRES_DB"; '
                         'pg_restore --exit-on-error --no-owner --no-acl --username "$POSTGRES_USER" '
                         f'--dbname "$POSTGRES_DB" {self.STORES["postgres"]}')
        self._store_exec("mongo", 'test "$MONGO_INITDB_DATABASE" = testlookup_logs; '
                         'mongosh --quiet --host 127.0.0.1 --username "$MONGO_INITDB_ROOT_USERNAME" '
                         '--password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin '
                         '--eval \'db.getSiblingDB("testlookup_logs").dropDatabase()\'; '
                         'mongorestore --host 127.0.0.1 --username "$MONGO_INITDB_ROOT_USERNAME" '
                         '--password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin '
                         f'--nsInclude="testlookup_logs.*" --drop --gzip --archive={self.STORES["mongo"]}')
        self._store_exec("minio", 'test "$(realpath /data)" = /data; '
                         'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; '
                         f'tar --xattrs --xattrs-include="*" --acls --numeric-owner -C /data -xpf {self.STORES["minio"]}')
        self._store_exec("redis", 'VALKEYCLI_AUTH="$REDIS_PASSWORD" REDISCLI_AUTH="$REDIS_PASSWORD" valkey-cli FLUSHALL SYNC')
        observed_tree = self._object_tree_metadata()
        self._write("restored-object-tree.json", observed_tree)
        if observed_tree != self._snapshot["object_tree"]:
            raise KubernetesUpgradeError("Restored object filesystem bodies or metadata differ from snapshot baseline")
        self._k("-n", self.namespace, "scale", "deployment/testlookup-minio", "--replicas=1")
        self._wait_ready(("minio",))
        self._migrated_ref = self.prior["images"]["backend"]["source_ref"]
        self._failed_migration = False
        self._write("restore.json", {"restored": True, "checksums_validated": True, "redis_flushed": True,
                                     "downgrade_used": False, "offline_object_restore": True,
                                     "object_gateway_restarted": True, "namespace": self.namespace})

    def cleanup(self):
        if self._cleaned:
            return self._cleanup_result
        if not self.uid:
            # A create request can succeed server-side even if its response is
            # lost. Resolve this exact random name before claiming it absent.
            observed = self._k("get", "namespace", self.namespace, "--ignore-not-found", "-o", "json", check=False)
            if observed.returncode:
                result = {"project": self.project, "containers": [self.namespace], "volumes": [],
                          "networks": [], "verified": False, "clean": False,
                          "reason": "Namespace creation outcome could not be observed"}
                self._write("cleanup.json", result)
                return result
            if not observed.stdout.strip():
                result = {"project": self.project, "containers": [], "volumes": [], "networks": [],
                          "verified": True, "clean": True, "namespace_created": False,
                          "namespace_absence_observed": True}
                self._write("cleanup.json", result)
                return result
            current = json.loads(observed.stdout)
            labels = current["metadata"].get("labels", {})
            if (not self._namespace_create_attempted or labels.get("testlookup.io/e2e-run") != self.namespace
                    or labels.get("testlookup.io/disposable") != "true"):
                raise KubernetesUpgradeError("Unconfirmed namespace ownership; cleanup refused")
            self.uid = current["metadata"]["uid"]
        self._guard()
        claims = self._get("pvc")["items"]
        owned_volumes = {c["spec"].get("volumeName") for c in claims if c["spec"].get("volumeName")}
        self._k("delete", "namespace", self.namespace, "--wait=false")
        deadline = time.monotonic() + 240
        remaining_namespace = True
        remaining_volumes = set(owned_volumes)
        while time.monotonic() < deadline:
            namespace = self._k("get", "namespace", self.namespace, "--ignore-not-found", "-o", "name").stdout.strip()
            remaining_namespace = bool(namespace)
            remaining_volumes = {name for name in owned_volumes if self._k(
                "get", "pv", name, "--ignore-not-found", "-o", "name").stdout.strip()}
            if not remaining_namespace and not remaining_volumes:
                break
            time.sleep(2)
        result = {"project": self.project, "namespace_uid": self.uid,
                  "containers": [self.namespace] if remaining_namespace else [],
                  "volumes": sorted(remaining_volumes), "networks": [self.namespace] if remaining_namespace else [],
                  "namespace_deleted": not remaining_namespace,
                  "verified": not remaining_namespace and not remaining_volumes,
                  "clean": not remaining_namespace and not remaining_volumes}
        self._write("cleanup.json", result)
        self._cleanup_result = result
        self._cleaned = result["verified"]
        return result
