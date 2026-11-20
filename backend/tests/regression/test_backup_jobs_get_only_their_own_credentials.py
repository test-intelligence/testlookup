"""Each backup and restore container receives only its own store's credentials.

The rule: no container of the backup or restore job takes the whole
``testlookup-secrets`` Secret. The PostgreSQL containers get the database URL
and password, the MongoDB containers the MongoDB URI, the object-store
containers the two object-store keys, and the containers that touch no store
(``finalize``, ``verify``) get nothing. The application's signing and
encryption keys (``JWT_SECRET_KEY``, ``APP_SECRET_KEY``, ``WEBHOOK_SECRET``) and
the Redis password never reach a dump container.

The positive side is checked against the scripts themselves: every secret a
script reads is one its container is given, so narrowing cannot starve a dump.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
COMPONENT = ROOT / "k8s" / "components" / "backup"
APP_SECRET = "testlookup-secrets"
APP_CONFIG = "testlookup-config"

POSTGRES = {"DATABASE_URL", "POSTGRES_PASSWORD"}
MONGO = {"MONGO_URI"}
OBJECT_STORE = {"MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"}
EXPECTED = {
    "testlookup-backup": {"postgres": POSTGRES, "mongo": MONGO, "minio": OBJECT_STORE, "finalize": set()},
    "testlookup-restore": {"verify": set(), "postgres": POSTGRES, "mongo": MONGO, "minio": OBJECT_STORE},
}
# A key the script can do without (it falls back to the ConfigMap's host and
# port pieces), so a Secret that lacks it must not stop the pod.
MAY_BE_ABSENT = {"DATABASE_URL", "POSTGRES_PASSWORD", "MONGO_URI"}
NEVER = {"JWT_SECRET_KEY", "APP_SECRET_KEY", "APP_SECRET_KEY_PREVIOUS", "WEBHOOK_SECRET", "REDIS_PASSWORD",
         "INITIAL_ADMIN_PASSWORD", "MONGO_ROOT_PASSWORD"}


def _containers(cronjob: dict) -> dict[str, dict]:
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return {c["name"]: c for c in [*(pod.get("initContainers") or []), *pod["containers"]]}


def _source(name: str) -> dict[str, dict]:
    file = {"testlookup-backup": "cronjob-backup.yaml", "testlookup-restore": "cronjob-restore.yaml"}[name]
    return _containers(yaml.safe_load((COMPONENT / file).read_text(encoding="utf-8")))


def _secret_keys(container: dict) -> dict[str, dict]:
    """``{env name: secretKeyRef}`` for the entries read from a Secret."""
    return {
        item["name"]: item["valueFrom"]["secretKeyRef"]
        for item in container.get("env") or []
        if "secretKeyRef" in (item.get("valueFrom") or {})
    }


CASES = [(job, container) for job, containers in EXPECTED.items() for container in containers]


def test_every_container_of_both_jobs_is_listed() -> None:
    for job, expected in EXPECTED.items():
        assert list(_source(job)) == list(expected), job


@pytest.mark.parametrize("job, name", CASES)
def test_no_container_takes_a_whole_secret(job: str, name: str) -> None:
    container = _source(job)[name]
    whole = [entry["secretRef"]["name"] for entry in container.get("envFrom") or [] if "secretRef" in entry]
    assert whole == [], f"{job}/{name} receives every key of {whole}"


@pytest.mark.parametrize("job, name", CASES)
def test_each_container_gets_exactly_its_own_keys(job: str, name: str) -> None:
    refs = _secret_keys(_source(job)[name])
    assert set(refs) == EXPECTED[job][name], f"{job}/{name}"
    assert not set(refs) & NEVER
    for variable, ref in refs.items():
        # The variable keeps the Secret key's name: the scripts read these names.
        assert (ref["name"], ref["key"]) == (APP_SECRET, variable), (job, name, variable)
        assert ref.get("optional", False) is (variable in MAY_BE_ABSENT), (job, name, variable)


def _reads(text: str) -> set[str]:
    shell = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]{2,})", text))
    python = set(re.findall(r"os\.environ(?:\.get\(|\[)\s*['\"]([A-Z][A-Z0-9_]+)['\"]", text))
    return shell | python


def test_every_secret_a_script_reads_is_given_to_its_container() -> None:
    """Positive case: the dumps still get what they sign in with."""
    lib = (COMPONENT / "lib.sh").read_text(encoding="utf-8")
    pg_setup = lib[lib.index("pg_setup() {"):lib.index("mongo_uri() {")]
    mongo_uri = lib[lib.index("mongo_uri() {"):lib.index("# Wait until a store")]
    secret_names = POSTGRES | MONGO | OBJECT_STORE | NEVER
    assert _reads(pg_setup) & secret_names == POSTGRES
    assert _reads(mongo_uri) & secret_names == MONGO
    minio = (COMPONENT / "minio_sync.py").read_text(encoding="utf-8")
    assert _reads(minio) & secret_names == OBJECT_STORE
    # Nothing in the component reads a key no container is given.
    everything = "".join((COMPONENT / f).read_text(encoding="utf-8")
                         for f in ("lib.sh", "backup.sh", "restore.sh", "minio_sync.py"))
    assert not _reads(everything) & NEVER


@pytest.mark.parametrize("job, name", [case for case in CASES if EXPECTED[case[0]][case[1]]])
def test_the_store_containers_still_read_the_plain_settings(job: str, name: str) -> None:
    """Positive case: host names, ports and the database name are not secret
    and still come from the ConfigMap as a whole."""
    container = _source(job)[name]
    maps = [entry["configMapRef"]["name"] for entry in container.get("envFrom") or [] if "configMapRef" in entry]
    assert maps == [APP_CONFIG], (job, name)


@pytest.mark.parametrize("overlay", ["k3s", "openshift-artifactory"])
def test_the_rendered_jobs_carry_the_same_keys(overlay: str) -> None:
    tool = shutil.which("kustomize")
    command = [tool, "build"] if tool else [shutil.which("kubectl") or "", "kustomize"]
    if not command[0]:
        pytest.skip("neither kustomize nor kubectl is installed (rendering only; no cluster)")
    rendered = subprocess.run([*command, str(ROOT / "k8s" / "overlays" / overlay)],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
    if rendered.returncode != 0 and 'unknown command "kustomize"' in rendered.stderr:
        pytest.skip(f"{command[0]} has no kustomize command")
    assert rendered.returncode == 0, rendered.stderr[-2000:]
    jobs = {d["metadata"]["name"]: d for d in yaml.safe_load_all(rendered.stdout) if d and d["kind"] == "CronJob"}
    for job, expected in EXPECTED.items():
        for name, container in _containers(jobs[job]).items():
            assert set(_secret_keys(container)) == expected[name], (overlay, job, name)
            assert not [e for e in container.get("envFrom") or [] if "secretRef" in e], (overlay, job, name)
