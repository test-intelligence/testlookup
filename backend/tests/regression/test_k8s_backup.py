"""Kubernetes backup + restore (re-audit M25): k8s/components/backup.

There was no Kubernetes backup path at all; the ops scripts were Compose-only.
These tests run the REAL scripts (backup.sh / restore.sh through /bin/sh, with
pg_dump / psql / mongodump / pg_restore / mongorestore replaced by shims that
record their arguments) and the real MinIO exporter against a fake S3 client,
then check the manifests the overlays render from.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPONENT = REPO_ROOT / "k8s" / "components" / "backup"
SH = shutil.which("sh") or shutil.which("bash")

pytestmark = pytest.mark.skipif(not COMPONENT.exists(), reason="k8s/ not present")
needs_sh = pytest.mark.skipif(SH is None, reason="no POSIX shell")


def _posix(path: Path) -> str:
    """A path the shell's own tar/sed accept (MSYS on Windows: /c/...)."""
    text = str(path)
    if os.name == "nt" and re.match(r"^[A-Za-z]:", text):
        text = "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text


SHIMS = {
    # Each shim appends "<name> <args>" to $SHIM_LOG; SHIM_FAIL / SHIM_EMPTY
    # name the tool that should fail or produce nothing.
    "pg_dump": """out=""
for a in "$@"; do case "$a" in --file=*) out="${a#--file=}";; esac; done
echo "pg_dump $* PGHOST=${PGHOST:-}" >> "$SHIM_LOG"
[ "${SHIM_FAIL:-}" = pg_dump ] && exit 1
if [ "${SHIM_EMPTY:-}" = pg_dump ]; then : > "$out"; else printf 'PGDMP-fake' > "$out"; fi
""",
    "psql": """echo "psql $*" >> "$SHIM_LOG"
echo "0167_head"
""",
    "mongodump": """out=""
for a in "$@"; do case "$a" in --archive=*) out="${a#--archive=}";; esac; done
echo "mongodump $*" >> "$SHIM_LOG"
[ "${SHIM_FAIL:-}" = mongodump ] && exit 1
if [ "${SHIM_EMPTY:-}" = mongodump ]; then : > "$out"; else printf 'mongo-fake' > "$out"; fi
""",
    "pg_restore": """echo "pg_restore $*" >> "$SHIM_LOG"
""",
    "mongorestore": """echo "mongorestore $*" >> "$SHIM_LOG"
""",
}


@pytest.fixture
def box(tmp_path: Path):
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in SHIMS.items():
        path = shims / name
        path.write_bytes(("#!/bin/sh\n" + body).encode())
        path.chmod(0o755)
    (tmp_path / "staging").mkdir()
    (tmp_path / "backups").mkdir()
    return tmp_path


def _run(box: Path, script: str, phase: str, **env: str) -> subprocess.CompletedProcess:
    base = {
        "PATH": f"{box / 'shims'}{os.pathsep}{os.environ.get('PATH', '')}",
        "STAGING": _posix(box / "staging"),
        "BACKUP_DIR": _posix(box / "backups"),
        "SHIM_LOG": _posix(box / "shim.log"),
        "DATABASE_URL": "postgresql+asyncpg://tl:secret@db:5432/testlookup",
        "MONGO_URI": "mongodb://mongo:27017",
    }
    for key in ("SYSTEMROOT", "TEMP", "TMP", "HOME"):
        if key in os.environ:
            base[key] = os.environ[key]
    base.update(env)
    base = {k: v for k, v in base.items() if v is not None}
    return subprocess.run(
        [SH, _posix(COMPONENT / script), phase],
        env=base, capture_output=True, text=True, check=False,
    )


def _log(box: Path) -> str:
    path = box / "shim.log"
    return path.read_text() if path.exists() else ""


def _minio_archive(box: Path) -> None:
    buf = io.BytesIO(b"object-bytes")
    with tarfile.open(box / "staging" / "minio_data.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo("test-telemetry/run/report.xml")
        info.size = len(buf.getvalue())
        tar.addfile(info, buf)


def _archives(box: Path) -> list[str]:
    return sorted(p.name for p in (box / "backups").iterdir()
                  if p.name.startswith("testlookup-backup-"))


def _full_backup(box: Path, **env: str) -> subprocess.CompletedProcess:
    for phase in ("postgres", "mongo"):
        result = _run(box, "backup.sh", phase, **env)
        assert result.returncode == 0, result.stderr
    _minio_archive(box)
    return _run(box, "backup.sh", "finalize", **env)


# ── backup ──────────────────────────────────────────────────────────────────


@needs_sh
def test_a_backup_is_one_verifiable_archive(box: Path) -> None:
    result = _full_backup(box, APP_VERSION="1.2.3")
    assert result.returncode == 0, result.stderr
    [name] = _archives(box)
    assert re.fullmatch(r"testlookup-backup-\d{8}T\d{6}Z\.tar\.gz", name)

    with tarfile.open(box / "backups" / name) as tar:
        members = {m.name.lstrip("./"): tar.extractfile(m).read() for m in tar if m.isfile()}
    assert set(members) == {"manifest.json", "postgres.dump", "mongo.archive.gz", "minio_data.tar.gz"}
    manifest = json.loads(members["manifest.json"])
    assert manifest["schema_version"] == 2
    assert manifest["source"] == "kubernetes-cronjob"
    assert manifest["alembic_head"] == "0167_head"
    assert manifest["app_version"] == "1.2.3"
    for component in manifest["components"]:
        assert hashlib.sha256(members[component["file"]]).hexdigest() == component["sha256"]
    assert {e["name"] for e in manifest["excluded"]} == {"redis", "chromadb", "ollama"}

    log = _log(box)
    # libpq cannot read SQLAlchemy's driver suffix.
    assert "--dbname=postgresql://tl:secret@db:5432/testlookup" in log
    assert "+asyncpg" not in log
    assert "--format=custom" in log
    assert "--uri=mongodb://mongo:27017" in log and "--gzip" in log


@needs_sh
def test_postgres_falls_back_to_the_postgres_pieces(box: Path) -> None:
    result = _run(box, "backup.sh", "postgres", DATABASE_URL=None,
                  POSTGRES_HOST="pg", POSTGRES_USER="tl", POSTGRES_DB="testlookup",
                  POSTGRES_PASSWORD="pw")
    assert result.returncode == 0, result.stderr
    assert "--dbname=testlookup" in _log(box) and "PGHOST=pg" in _log(box)


@needs_sh
@pytest.mark.parametrize("tool,phase,mode", [
    ("pg_dump", "postgres", "SHIM_FAIL"),
    ("pg_dump", "postgres", "SHIM_EMPTY"),
    ("mongodump", "mongo", "SHIM_FAIL"),
    ("mongodump", "mongo", "SHIM_EMPTY"),
])
def test_a_failed_or_empty_dump_fails_and_never_becomes_an_archive(box, tool, phase, mode) -> None:
    result = _run(box, "backup.sh", phase, **{mode: tool})
    assert result.returncode != 0
    staged = {"postgres": "postgres.dump", "mongo": "mongo.archive.gz"}[phase]
    assert not (box / "staging" / staged).exists()
    # finalize refuses a partial set
    _minio_archive(box)
    finalize = _run(box, "backup.sh", "finalize")
    assert finalize.returncode != 0
    assert "refusing to write a partial backup" in finalize.stderr
    assert _archives(box) == []


@needs_sh
def test_retention_keeps_the_newest_archives_and_nothing_else_is_touched(box: Path) -> None:
    backups = box / "backups"
    old = [f"testlookup-backup-2026090{d}T021700Z.tar.gz" for d in range(1, 6)]
    for name in old:
        (backups / name).write_bytes(b"old")
    (backups / "notes.txt").write_bytes(b"keep me")
    result = _full_backup(box, BACKUP_KEEP="3")
    assert result.returncode == 0, result.stderr
    remaining = _archives(box)
    assert len(remaining) == 3
    assert remaining[:2] == old[3:]           # the two newest old ones
    assert (backups / "notes.txt").exists()


@needs_sh
@pytest.mark.parametrize("keep", ["0", "abc", "-1"])
def test_a_bad_retention_setting_writes_nothing(box: Path, keep: str) -> None:
    # (An EMPTY value means "unset" -> the default 14, as `value: ""` in a pod
    # spec should; only a value that is set and wrong is refused.)
    result = _full_backup(box, BACKUP_KEEP=keep)
    assert result.returncode != 0
    assert _archives(box) == []


# ── restore ─────────────────────────────────────────────────────────────────


def _archive_name(box: Path) -> str:
    result = _full_backup(box)
    assert result.returncode == 0, result.stderr
    return _archives(box)[0]


@needs_sh
def test_restore_verifies_then_restores_with_the_safe_flags(box: Path) -> None:
    name = _archive_name(box)
    for f in (box / "staging").iterdir():
        f.unlink()
    verify = _run(box, "restore.sh", "verify", RESTORE_FILE=name, RESTORE_CONFIRM="yes")
    assert verify.returncode == 0, verify.stderr
    assert (box / "staging" / "postgres.dump").read_bytes() == b"PGDMP-fake"

    assert _run(box, "restore.sh", "postgres").returncode == 0
    assert _run(box, "restore.sh", "mongo").returncode == 0
    log = _log(box)
    pg_line = next(line for line in log.splitlines() if line.startswith("pg_restore"))
    for flag in ("--clean", "--if-exists", "--single-transaction", "--exit-on-error"):
        assert flag in pg_line
    assert "--dbname=postgresql://tl:secret@db:5432/testlookup" in pg_line
    mongo_line = next(line for line in log.splitlines() if line.startswith("mongorestore"))
    assert "--drop" in mongo_line and "--gzip" in mongo_line


@needs_sh
def test_restore_refuses_an_altered_archive(box: Path) -> None:
    name = _archive_name(box)
    path = box / "backups" / name
    with tarfile.open(path) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
    members["./postgres.dump" if "./postgres.dump" in members else "postgres.dump"] = b"tampered"
    with tarfile.open(path, "w:gz") as tar:
        for member_name, data in members.items():
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    result = _run(box, "restore.sh", "verify", RESTORE_FILE=name, RESTORE_CONFIRM="yes")
    assert result.returncode != 0
    assert "digest mismatch" in result.stderr


@needs_sh
@pytest.mark.parametrize("env,message", [
    ({"RESTORE_CONFIRM": "no"}, "RESTORE_CONFIRM"),
    ({"RESTORE_CONFIRM": "yes", "RESTORE_FILE": "../etc/x.tar.gz"}, "bare archive name"),
    ({"RESTORE_CONFIRM": "yes", "RESTORE_FILE": ""}, "RESTORE_FILE is empty"),
    ({"RESTORE_CONFIRM": "yes", "RESTORE_FILE": "missing.tar.gz"}, "no such archive"),
])
def test_restore_refuses_without_an_explicit_valid_request(box: Path, env, message) -> None:
    env = {"RESTORE_FILE": "whatever.tar.gz", **env}
    result = _run(box, "restore.sh", "verify", **env)
    assert result.returncode != 0
    assert message in result.stderr


@needs_sh
def test_restore_refuses_a_compose_archive(box: Path) -> None:
    staging = box / "compose"
    staging.mkdir()
    (staging / "manifest.json").write_text('{"schema_version": 2}\n')
    with tarfile.open(box / "backups" / "testlookup-backup-20260101T000000Z.tar.gz", "w:gz") as tar:
        tar.add(staging / "manifest.json", arcname="manifest.json")
    result = _run(box, "restore.sh", "verify",
                  RESTORE_FILE="testlookup-backup-20260101T000000Z.tar.gz", RESTORE_CONFIRM="yes")
    assert result.returncode != 0
    assert "make restore" in result.stderr


# ── MinIO export / import ───────────────────────────────────────────────────


def _minio_sync():
    # No bytecode: a __pycache__ inside k8s/components/backup is litter in a
    # kustomize directory.
    import sys

    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location("minio_sync", COMPONENT / "minio_sync.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class FakeS3:
    def __init__(self, objects: dict[str, dict[str, bytes]]):
        self.objects = {b: dict(o) for b, o in objects.items()}
        self.created: list[str] = []

    def list_buckets(self):
        return {"Buckets": [{"Name": b} for b in self.objects]}

    def get_paginator(self, _name):
        store = self.objects

        class Pages:
            def paginate(self, Bucket):  # noqa: N803 -- boto3's keyword
                keys = sorted(store[Bucket])
                for start in range(0, len(keys), 2):          # 2 per page
                    chunk = keys[start:start + 2]
                    yield {"Contents": [{"Key": k, "Size": len(store[Bucket][k])} for k in chunk]}
                if not keys:
                    yield {}
        return Pages()

    def get_object(self, Bucket, Key):  # noqa: N803
        return {"Body": io.BytesIO(self.objects[Bucket][Key])}

    def create_bucket(self, Bucket):  # noqa: N803
        self.created.append(Bucket)
        self.objects.setdefault(Bucket, {})

    def upload_fileobj(self, fileobj, bucket, key):
        self.objects[bucket][key] = fileobj.read()


def test_minio_round_trip_keeps_every_object_and_empty_buckets(tmp_path: Path) -> None:
    sync = _minio_sync()
    source = FakeS3({
        "test-telemetry": {"a.xml": b"A", "b/c.xml": b"BC", "d.json": b"{}"},
        "knowledge-docs": {},
    })
    archive = str(tmp_path / "minio.tar.gz")
    assert sync.dump(archive, s3=source) == 3
    assert not os.path.exists(archive + ".tmp")

    target = FakeS3({"test-telemetry": {"a.xml": b"stale", "newer.xml": b"kept"}})
    assert sync.restore(archive, s3=target) == 3
    assert target.created == ["knowledge-docs"]
    assert target.objects["test-telemetry"] == {
        "a.xml": b"A", "b/c.xml": b"BC", "d.json": b"{}", "newer.xml": b"kept",
    }


def test_minio_restore_without_an_archive_fails(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _minio_sync().restore(str(tmp_path / "missing.tar.gz"), s3=FakeS3({}))


# ── manifests ───────────────────────────────────────────────────────────────


def _docs(name: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((COMPONENT / name).read_text(encoding="utf-8")) if d]


def test_the_component_ships_every_script_it_mounts() -> None:
    kustomization = yaml.safe_load((COMPONENT / "kustomization.yaml").read_text(encoding="utf-8"))
    assert kustomization["kind"] == "Component"
    for resource in kustomization["resources"]:
        assert (COMPONENT / resource).is_file()
    scripts = next(g for g in kustomization["configMapGenerator"] if g["name"] == "testlookup-backup-scripts")
    assert set(scripts["files"]) == {"lib.sh", "backup.sh", "restore.sh", "minio_sync.py"}
    request = next(g for g in kustomization["configMapGenerator"] if g["name"] == "testlookup-restore-request")
    assert "RESTORE_CONFIRM=no" in request["literals"]


def _pod(cronjob: dict) -> dict:
    return cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def test_the_backup_cronjob_runs_every_phase_in_order_and_never_overlaps() -> None:
    [cronjob] = _docs("cronjob-backup.yaml")
    assert re.fullmatch(r"\S+ \S+ \S+ \S+ \S+", cronjob["spec"]["schedule"])
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"
    pod = _pod(cronjob)
    assert [c["command"][-1] if c["name"] != "minio" else "minio" for c in pod["initContainers"]] == [
        "postgres", "mongo", "minio"]
    assert pod["containers"][0]["command"][-1] == "finalize"
    keep = next(e["value"] for e in pod["containers"][0]["env"] if e["name"] == "BACKUP_KEEP")
    assert int(keep) >= 7
    claims = {v["persistentVolumeClaim"]["claimName"] for v in pod["volumes"] if "persistentVolumeClaim" in v}
    [pvc] = _docs("pvc.yaml")
    assert claims == {pvc["metadata"]["name"]}
    assert pod["securityContext"]["runAsNonRoot"] is True


def test_the_restore_cronjob_never_runs_on_its_own() -> None:
    [cronjob] = _docs("cronjob-restore.yaml")
    assert cronjob["spec"]["suspend"] is True
    pod = _pod(cronjob)
    assert [c["command"][-1] for c in pod["initContainers"]] == ["verify", "postgres", "mongo"]
    verify = pod["initContainers"][0]
    assert {e["configMapRef"]["name"] for e in verify["envFrom"]} == {"testlookup-restore-request"}


def test_every_image_is_one_the_release_manifest_pins() -> None:
    manifest = (REPO_ROOT / "deploy" / "images.manifest.txt").read_text(encoding="utf-8")
    pinned = {line.split()[0] for line in manifest.splitlines() if line.strip() and not line.startswith("#")}
    for name in ("cronjob-backup.yaml", "cronjob-restore.yaml"):
        pod = _pod(_docs(name)[0])
        for container in pod["initContainers"] + pod["containers"]:
            image = container["image"]
            if image.startswith("testlookup/backend:"):
                continue                       # the app image; overlays set its tag
            assert image in pinned, image


def test_the_backup_pod_can_reach_the_stores_under_default_deny() -> None:
    egress, stores = _docs("networkpolicy.yaml")
    assert egress["spec"]["podSelector"]["matchLabels"] == {"app": "testlookup-backup"}
    # R-B45-5: no allow-all rule; every rule names a peer and a port, and the
    # three stores are among them (the full set is pinned in
    # test_backup_alerts_and_scrape_targets.py).
    assert all(rule.get("to") and rule.get("ports") for rule in egress["spec"]["egress"])
    reachable = {
        (peer.get("podSelector", {}).get("matchLabels", {}).get("app"), port["port"])
        for rule in egress["spec"]["egress"] for peer in rule["to"] for port in rule["ports"]
    }
    assert {("testlookup-postgres", 5432), ("testlookup-mongo", 27017), ("testlookup-minio", 9000)} <= reachable
    assert {"testlookup-postgres", "testlookup-mongo", "testlookup-minio"} <= set(
        stores["spec"]["podSelector"]["matchExpressions"][0]["values"])
    assert stores["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"] == {"app": "testlookup-backup"}
    labels = _pod(_docs("cronjob-backup.yaml")[0])
    assert labels  # pod template carries the selector label:
    template = _docs("cronjob-backup.yaml")[0]["spec"]["jobTemplate"]["spec"]["template"]
    assert template["metadata"]["labels"]["app"] == "testlookup-backup"


@pytest.mark.parametrize("overlay", ["homelab", "openshift-artifactory"])
def test_overlays_with_in_cluster_stores_include_the_component(overlay: str) -> None:
    kustomization = yaml.safe_load(
        (REPO_ROOT / "k8s" / "overlays" / overlay / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "../../components/backup" in kustomization.get("components", [])
