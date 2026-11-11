"""Disposable Podman storage and deployment adapter for upgrade proofs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from ephemeral import ROOT, prepare


class PodmanUpgradeError(RuntimeError):
    """An adapter operation failed; private diagnostics stay in its evidence folder."""


class PodmanUpgrade:
    APP_SERVICES = ("backend", "frontend", "mcp", "worker", "worker-children")
    STORES = {
        "postgres": "/tmp/tl-upgrade-postgres.dump",
        "mongo": "/tmp/tl-upgrade-mongo.archive.gz",
        "minio": "/tmp/tl-upgrade-objects.tar",
    }

    def __init__(self, prior, candidate, out, engine="podman"):
        if engine not in {"podman", "docker"}:
            raise ValueError("Unsupported container engine")
        self.prior, self.candidate = prior, candidate
        self.out = Path(out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.engine = engine
        self.project = "tl-upgrade-" + uuid.uuid4().hex[:12]
        self._temp = tempfile.TemporaryDirectory(prefix=self.project + "-")
        self.directory = Path(self._temp.name).resolve()
        if self.directory.parent != Path(tempfile.gettempdir()).resolve():
            raise ValueError("Unexpected temporary workspace")
        try:
            self.settings = prepare(self.directory, prior, self.project, engine)
        except (OSError, subprocess.SubprocessError) as exc:
            self._temp.cleanup()
            raise PodmanUpgradeError(
                f"Isolated stack preparation failed ({type(exc).__name__})."
            ) from None
        # Existing shell profiles cannot redirect this adapter's data stores.
        self.settings.update(
            POSTGRES_DB="testlookup", POSTGRES_USER="testlookup_user",
            MONGO_DB="testlookup_logs", MONGO_USER="testlookup",
        )
        (self.directory / ".env").write_text(
            "".join(f"{key}={value}\n" for key, value in self.settings.items()),
            encoding="utf-8",
        )
        self.env = {**os.environ, **self.settings, "COMPOSE_PARALLEL_LIMIT": "1"}
        self.config = json.loads((self.directory / "compose.json").read_bytes())
        self.compose = [engine, "compose", "--project-name", self.project,
                        "-f", "compose.json"]
        self._quiesced = False
        self._snapshot = None
        self._failed_migration = False
        self._cleaned = False
        self.object_helper = self.project + "-object-snapshot"
        self.object_volume = self.project + "_minio_data"
        self._object_helper_attempted = False
        self._object_helper_created = False
        self._object_store_id = None

    def _redact(self, text):
        for key, value in self.settings.items():
            if any(word in key for word in ("PASSWORD", "SECRET", "ACCESS_KEY")):
                text = text.replace(value, "[REDACTED]")
        text = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                      "[REDACTED_TOKEN]", text)
        return text

    def _run(self, arguments, *, input=None, check=True, compose=True,
             log_output=True, timeout=600):
        command = [*(self.compose if compose else [self.engine]), *arguments]
        try:
            result = subprocess.run(
                command, cwd=self.directory, env=self.env, input=input,
                text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PodmanUpgradeError(
                f"Container operation could not complete ({type(exc).__name__})."
            ) from None
        with (self.out / "podman-private.log").open("a", encoding="utf-8") as log:
            log.write(self._redact(json.dumps(command)) + "\n")
            log.write(f"exit={result.returncode}\n")
            if log_output:
                log.write(self._redact(result.stdout + result.stderr) + "\n")
        if check and result.returncode:
            raise PodmanUpgradeError(
                f"Container operation failed with exit {result.returncode}; "
                "inspect private adapter diagnostics."
            )
        return result

    def _save_config(self):
        (self.directory / "compose.json").write_text(
            json.dumps(self.config), encoding="utf-8"
        )

    def _write(self, name, value):
        (self.out / name).write_text(json.dumps(value, indent=2), encoding="utf-8")

    def _require_quiesced(self):
        if not self._quiesced:
            raise PodmanUpgradeError("Application must be quiesced for this operation.")

    def install(self):
        self._run(["up", "-d", "--wait", "--wait-timeout", "300",
                   "frontend", "worker", "worker-children", "mcp"])
        self.exec_python((ROOT / "e2e-runner/bootstrap_admin.py").read_text(
            encoding="utf-8"))
        self.observe(self.prior)

    def exec_python(self, source):
        # Helper stdout is deliberately not copied into diagnostics: callers
        # choose their own safe projection of application records.
        result = self._run(["exec", "-T", "backend", "python", "-"],
                           input=source, log_output=False, check=False)
        evidence_lines = [line for line in result.stdout.splitlines()
                          if line.startswith("UPGRADE_JSON=")]
        if result.returncode and len(evidence_lines) != 1:
            raise PodmanUpgradeError(
                "Backend Python transport failed without a single fixture evidence record."
            )
        return result.stdout

    def observe(self, manifest):
        health = json.loads(self.exec_python(
            "import json,urllib.request\n"
            "def get(path):\n"
            "    with urllib.request.urlopen('http://127.0.0.1:8000/'+path,timeout=30) as response:\n"
            "        return json.load(response)\n"
            "print(json.dumps({'build':get('health/version'),'ready':get('health/ready')}))\n"
        ))
        images = []
        for service in self.APP_SERVICES:
            identifier = self._run(["ps", "-q", service]).stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
                raise PodmanUpgradeError("Application container identity unavailable.")
            # Read a narrow projection; a full inspect would expose env secrets.
            format_string = (
                '{{json .Image}}|{{json .ImageDigest}}|{{json .Config.Image}}'
                if self.engine == "podman" else
                '{{json .Image}}|{{json .Config.Image}}'
            )
            row = self._run(["container", "inspect", "--format", format_string,
                             identifier], compose=False).stdout.strip().split("|")
            image_id = json.loads(row[0])
            if self.engine == "podman":
                running, configured = json.loads(row[1]), json.loads(row[2])
            else:
                configured = json.loads(row[1])
                repo_digests = json.loads(self._run(
                    ["image", "inspect", "--format", "{{json .RepoDigests}}", image_id],
                    compose=False).stdout)
                running = next((ref.rsplit("@", 1)[-1] for ref in repo_digests
                                if ref == configured), "unverified")
            component = service if service in {"frontend", "mcp"} else "backend"
            expected = manifest["images"][component]["source_ref"]
            images.append({
                "service": service, "component": component, "candidate": expected,
                "running": running, "image_id": image_id,
                "matched": configured == expected and running == expected.rsplit("@", 1)[1],
            })
        matched = (
            health["build"].get("build", {}).get("revision") == manifest["source_sha"]
            and health["ready"].get("status") == "ready"
            and all(health["ready"].get("checks", {}).get(store, {}).get("status") == "ok"
                    for store in ("postgres", "mongo", "redis"))
            and all(image["matched"] for image in images)
        )
        observation = {"source_sha": manifest["source_sha"], **health,
                       "images": images, "matched": matched}
        self._write("observation-" + uuid.uuid4().hex[:8] + ".json", observation)
        if not matched:
            raise PodmanUpgradeError("Running build, images or readiness did not match.")
        return observation

    def quiesce(self):
        self._run(["stop", "--timeout", "30", *self.APP_SERVICES])
        self._quiesced = True

    def _store_exec(self, service, source):
        return self._run(["exec", "-T", service, "sh", "-eu", "-c", source]).stdout

    def _ensure_object_helper(self):
        """Keep the raw archive independently of the object-server lifecycle."""
        if self._object_helper_created:
            return
        identifier = self._run(["ps", "-q", "minio"]).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
            raise PodmanUpgradeError("Owned object-store container is unavailable.")
        labels = json.loads(self._run(
            ["volume", "inspect", "--format", "{{json .Labels}}", self.object_volume],
            compose=False).stdout)
        owners = {(labels or {})[key] for key in
                  ("com.docker.compose.project", "io.podman.compose.project")
                  if key in (labels or {})}
        if owners != {self.project}:
            raise PodmanUpgradeError("Object volume does not have this project's ownership label.")
        self._object_store_id = identifier
        self._object_helper_attempted = True
        result = self._run(
            ["run", "-d", "--name", self.object_helper, "--network", "none", "--user", "0:0",
             "--label", "io.testlookup.upgrade.project=" + self.project,
             "--volume", self.object_volume + ":/data", "--entrypoint", "python",
             self.prior["images"]["backend"]["source_ref"],
             "-c", "import signal; signal.pause()"], compose=False)
        if not re.fullmatch(r"[0-9a-f]{12,64}", result.stdout.strip()):
            raise PodmanUpgradeError("Snapshot helper identity is unavailable.")
        self._object_helper_created = True
        self._write("object-helper.json", {
            "container": self.object_helper, "volume": self.object_volume,
            "network": "none", "image": self.prior["images"]["backend"]["source_ref"],
            "raw_archive_host_files": False,
        })

    def _object_exec(self, arguments, **kwargs):
        if not self._object_helper_created:
            raise PodmanUpgradeError("An owned persistent snapshot helper is required.")
        return self._run(["exec", self.object_helper, *arguments], compose=False, **kwargs)

    def _object_archive(self, restore=False):
        # The helper owns /tmp; stopping or recreating VersityGW cannot erase
        # this archive. GNU PAX retains nanosecond times and all user xattrs.
        self._object_exec([
            "tar", "--format=pax", "--xattrs", "--xattrs-include=*", "--numeric-owner",
            "-C", "/data", "-xpf" if restore else "-cpf", self.STORES["minio"],
            *([] if restore else ["."]),
        ])

    def _object_tree(self):
        """Independently hash the entire owned volume, including all xattrs."""
        source = r'''
import hashlib,json,os,stat
paths=['/data']
for root,directories,files in os.walk('/data',followlinks=False):
    paths.extend(os.path.join(root,name) for name in directories+files)
records=[]
attribute_count=0
for path in sorted(set(paths)):
    info=os.lstat(path)
    attributes=[]
    for name in sorted(os.listxattr(path,follow_symlinks=False)):
        attributes.append([name,hashlib.sha256(os.getxattr(path,name,follow_symlinks=False)).hexdigest()])
    attribute_count+=len(attributes)
    if stat.S_ISREG(info.st_mode):
        digest=hashlib.sha256()
        with open(path,'rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        body=digest.hexdigest()
    elif stat.S_ISDIR(info.st_mode):body='directory'
    elif stat.S_ISLNK(info.st_mode):body=hashlib.sha256(os.readlink(path).encode()).hexdigest()
    else:raise RuntimeError('Unsupported object-volume filesystem entry')
    records.append([os.path.relpath(path,'/data'),info.st_uid,info.st_gid,info.st_mode,
                    info.st_mtime_ns,body,attributes])
payload=json.dumps(records,sort_keys=True,separators=(',',':')).encode()
print(json.dumps({'sha256':hashlib.sha256(payload).hexdigest(),'entries':len(records),
                  'extended_attributes':attribute_count,
                  'covers':['relative_paths','uid','gid','mode','mtime_ns','body','all_xattrs']}))
'''
        result = self._object_exec(["python", "-c", source], log_output=False)
        return json.loads(result.stdout)

    def _archive_metadata(self, service, path):
        command = f'test -f {path}; sha256sum {path}; wc -c < {path}'
        result = (
            self._object_exec(["sh", "-eu", "-c", command]).stdout
            if service == "minio" else self._store_exec(service, command)
        )
        lines = result.strip().splitlines()
        checksum = lines[-2].split()[0]
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise PodmanUpgradeError("Snapshot checksum unavailable.")
        size = int(lines[-1].strip())
        if size <= 0:
            raise PodmanUpgradeError("Snapshot archive is empty.")
        metadata = {"path": path, "size": size, "sha256": checksum}
        if service == "minio":
            metadata["container"] = self.object_helper
        return metadata

    def snapshot(self):
        self._require_quiesced()
        self._ensure_object_helper()
        self._object_exec(["sh", "-eu", "-c",
            "command -v tar; command -v sha256sum; command -v find; command -v realpath; "
            'test "$(realpath /data)" = /data'])
        self._store_exec("postgres",
            'export PGPASSWORD="$POSTGRES_PASSWORD"; '
            'pg_dump --format=custom --username "$POSTGRES_USER" '
            f'--dbname "$POSTGRES_DB" --file {self.STORES["postgres"]}')
        self._store_exec("mongo",
            'mongodump --host 127.0.0.1 --username "$MONGO_INITDB_ROOT_USERNAME" '
            '--password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin '
            f'--db "$MONGO_INITDB_DATABASE" --gzip --archive={self.STORES["mongo"]}')
        self._object_archive()
        self._snapshot = {
            key: self._archive_metadata(key, path)
            for key, path in self.STORES.items()
        }
        self._snapshot["redis"] = "Not snapshotted; flushed during restore."
        self._snapshot["object_archive_format"] = "GNU PAX tar with all extended attributes and numeric owners."
        self._snapshot["object_tree"] = self._object_tree()
        self._write("snapshot-metadata.json", self._snapshot)
        return self._snapshot

    def migrate(self, manifest, negative=False):
        if not negative:
            self._require_quiesced()
        self.config["services"]["db-migrate"]["image"] = manifest["images"]["backend"]["source_ref"]
        self._save_config()
        revision = "tl_e2e_nonexistent" if negative else "head"
        result = self._run(["run", "--rm", "--no-deps", "db-migrate",
                            "alembic", "upgrade", revision], check=False)
        succeeded = result.returncode == 0
        self._failed_migration = not succeeded
        if negative and not succeeded:
            diagnostic = (result.stdout + result.stderr).lower()
            if not ("tl_e2e_nonexistent" in diagnostic
                    and ("can't locate revision" in diagnostic
                         or "cannot locate revision" in diagnostic)):
                raise PodmanUpgradeError(
                    "Negative migration failed without the intended Alembic revision diagnostic."
                )
        self._write("migration-" + uuid.uuid4().hex[:8] + ".json", {
            "source_sha": manifest["source_sha"], "negative": negative,
            "revision": revision, "exit_code": result.returncode,
            "succeeded": succeeded, "app_images_changed": False,
        })
        return succeeded

    def activate(self, manifest):
        if self._failed_migration and manifest != self.prior:
            raise PodmanUpgradeError("Failed migration prevents candidate activation.")
        for service in self.APP_SERVICES:
            component = service if service in {"frontend", "mcp"} else "backend"
            self.config["services"][service]["image"] = manifest["images"][component]["source_ref"]
        self._save_config()
        self._run(["up", "-d", "--no-deps", "--force-recreate", "--wait",
                   "--wait-timeout", "300", *self.APP_SERVICES])
        self._quiesced = False
        self.observe(manifest)

    def restore(self):
        self._require_quiesced()
        if not self._snapshot:
            raise PodmanUpgradeError("An owned snapshot is required before restore.")
        # Validate all archives before the first destructive database operation.
        for key, path in self.STORES.items():
            current = self._archive_metadata(key, path)
            if current != self._snapshot[key]:
                raise PodmanUpgradeError("Snapshot archive checksum or size changed.")
        # Restore the filesystem with VersityGW offline, as the operator restore
        # path requires. The helper and archive have a separate writable layer.
        self._run(["stop", "--timeout", "10", "minio"], timeout=60)
        running = self._run(
            ["container", "inspect", "--format", "{{json .State.Running}}", self._object_store_id],
            compose=False, timeout=30).stdout.strip()
        if running != "false":
            raise PodmanUpgradeError("Object server must be stopped before filesystem restoration.")
        self._store_exec("postgres",
            'test "$POSTGRES_DB" = testlookup; '
            'test "$POSTGRES_USER" = testlookup_user; '
            'export PGPASSWORD="$POSTGRES_PASSWORD"; '
            'dropdb --if-exists --force --username "$POSTGRES_USER" "$POSTGRES_DB"; '
            'createdb --username "$POSTGRES_USER" "$POSTGRES_DB"; '
            'pg_restore --exit-on-error --username "$POSTGRES_USER" '
            f'--dbname "$POSTGRES_DB" {self.STORES["postgres"]}')
        self._store_exec("mongo",
            'test "$MONGO_INITDB_DATABASE" = testlookup_logs; '
            'mongosh --quiet --host 127.0.0.1 '
            '--username "$MONGO_INITDB_ROOT_USERNAME" '
            '--password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin '
            '--eval \'db.getSiblingDB("testlookup_logs").dropDatabase()\'; '
            'mongorestore --host 127.0.0.1 --username "$MONGO_INITDB_ROOT_USERNAME" '
            '--password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin '
            f'--nsInclude="testlookup_logs.*" --drop --gzip --archive={self.STORES["mongo"]}')
        self._object_exec(["sh", "-eu", "-c", 'test "$(realpath /data)" = /data; '
            'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'])
        self._object_archive(restore=True)
        restored_tree = self._object_tree()
        if restored_tree != self._snapshot["object_tree"]:
            raise PodmanUpgradeError("Restored object tree, ownership or extended attributes differ.")
        self._run(["up", "-d", "--no-deps", "--wait", "--wait-timeout", "60", "minio"],
                  timeout=120)
        self._store_exec("redis", 'export VALKEYCLI_AUTH="$REDIS_PASSWORD"; valkey-cli FLUSHALL')
        self._failed_migration = False
        self._write("restore.json", {"restored": True, "checksums_validated": True,
                                     "redis_flushed": True, "downgrade_used": False,
                                     "whole_object_tree_matched": True,
                                     "object_server_restored_offline": True,
                                     "object_server_ready_before_app_activation": True})

    def cleanup(self):
        if self._cleaned:
            return self._cleanup_result
        # Never widen this to unrelated compose projects or engine-wide pruning.
        if not re.fullmatch(r"tl-upgrade-[0-9a-f]{12}", self.project):
            raise PodmanUpgradeError("Invalid owned project identity.")
        errors = []
        down_exit = None
        helper_removed = not self._object_helper_attempted
        if self._object_helper_attempted:
            try:
                helper_labels = self._run(
                    ["container", "inspect", "--format", "{{json .Config.Labels}}", self.object_helper],
                    compose=False, timeout=30)
                labels = json.loads(helper_labels.stdout)
                if (labels or {}).get("io.testlookup.upgrade.project") != self.project:
                    raise PodmanUpgradeError("Snapshot helper ownership did not match.")
                self._run(["rm", "--force", *(["--time", "0"] if self.engine == "podman" else []),
                           self.object_helper],
                          compose=False, timeout=30)
                helper_removed = True
            except (PodmanUpgradeError, ValueError):
                errors.append("Owned snapshot helper removal is unverified.")
        try:
            down = self._run(["down", "--volumes", "--remove-orphans", "--timeout", "10"],
                             check=False, timeout=60)
            down_exit = down.returncode
        except PodmanUpgradeError:
            errors.append("Owned compose teardown did not complete within its bound.")

        def inventory(arguments, name):
            try:
                return self._run(arguments, compose=False, timeout=30).stdout.splitlines()
            except PodmanUpgradeError:
                errors.append("Owned " + name + " inventory unavailable.")
                return []

        containers = inventory(
            ["ps", "-a", "--filter", "name=" + self.project + "-", "--format", "{{.Names}}"],
            "container")
        volumes = inventory(["volume", "ls", "--format", "{{.Name}}"], "volume")
        networks = inventory(["network", "ls", "--format", "{{.Name}}"], "network")
        result = {"project": self.project, "containers": containers,
                  "volumes": [name for name in volumes if name.startswith(self.project + "_")],
                  "networks": [name for name in networks if name.startswith(self.project + "_")],
                  "down_exit_code": down_exit, "errors": errors,
                  "snapshot_helper_removed": helper_removed}
        result["verified"] = not errors and not any(
            result[key] for key in ("containers", "volumes", "networks"))
        result["clean"] = result["verified"]
        self._write("cleanup.json", result)
        self._cleanup_result = result
        self._cleaned = result["clean"]
        # The resolved path was checked against the system temporary directory
        # during construction; only this random owned workspace is removed.
        if self._cleaned:
            self._temp.cleanup()
        return result
