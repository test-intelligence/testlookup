"""The bundled Redis and MongoDB of the air-gapped OpenShift install take a password.

``k8s/overlays/openshift-artifactory`` runs Valkey (as ``testlookup-redis``)
and MongoDB inside the namespace. Both accepted any client that reached their
port; a NetworkPolicy keyed on pod labels was the only barrier. Redis is the
Celery broker and the token-revocation store, and MongoDB holds raw logs.

The rules:

* Valkey starts with ``--requirepass`` from ``testlookup-secrets``, and every
  workload that reads the Redis URLs of the base ConfigMap gets them from the
  Secret instead (an ``env`` entry wins over ``envFrom``). The base ConfigMap
  keeps its URLs: the cloud overlays depend on them.
* The deploy script generates the Redis password once, adds it to a Secret
  that an older install already has, and reuses it on every later run.
* MongoDB reads its root user from the Secret when the Secret has one. The
  script adds one only when the operator sets ``ENABLE_MONGO_AUTH=true``, and
  then creates the user in the running server BEFORE authentication is switched
  on (the image creates it only on an empty data directory). It refuses when it
  cannot do that safely.

Rendered with kustomize and run against a stand-in for ``oc``; no cluster.
"""
from __future__ import annotations

import base64
import functools
import re
import shutil
import subprocess

import pytest
import yaml

from tests.regression.openshift_deploy_harness import ROOT, SCRIPT, credential, read, run_scenarios

pytestmark = pytest.mark.regression

OVERLAY = ROOT / "k8s" / "overlays" / "openshift-artifactory"
SECRET = "testlookup-secrets"
REDIS_URL_KEYS = ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
# Every Deployment that runs the backend image: the API, five workers and beat.
REDIS_CLIENTS = 7


@functools.lru_cache(maxsize=None)
def _rendered() -> tuple[dict, ...]:
    kustomize, kubectl = shutil.which("kustomize"), shutil.which("kubectl")
    if kustomize:
        command = [kustomize, "build", str(OVERLAY)]
    elif kubectl:
        command = [kubectl, "kustomize", str(OVERLAY)]
    else:
        pytest.skip("neither kustomize nor kubectl is installed (rendering only; no cluster access)")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0 and 'unknown command "kustomize"' in result.stderr:
        pytest.skip(f"{command[0]} has no kustomize command (a version-picking wrapper)")
    assert result.returncode == 0, result.stderr
    return tuple(d for d in yaml.safe_load_all(result.stdout) if d)


def _deployments() -> dict[str, dict]:
    return {d["metadata"]["name"]: d["spec"]["template"]["spec"]
            for d in _rendered() if d["kind"] == "Deployment"}


def _container(deployment: str) -> dict:
    return _deployments()[deployment]["containers"][0]


def _env(container: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in container.get("env") or []}


def _from_secret(entry: dict | None) -> tuple[str, str, bool] | None:
    """(secret, key, optional) of an env entry that reads a Secret key."""
    ref = ((entry or {}).get("valueFrom") or {}).get("secretKeyRef")
    return (ref["name"], ref["key"], bool(ref.get("optional"))) if ref else None


# ── Redis: the manifests ───────────────────────────────────────────────────

def _valkey_command() -> str:
    """The shell line the Valkey container runs."""
    command = _container("testlookup-redis")["command"]
    assert command[:2] == ["sh", "-c"] and len(command) == 3, command
    return command[2]


def test_valkey_takes_its_password_from_the_secret():
    redis = _container("testlookup-redis")
    assert '${REDIS_PASSWORD:+--requirepass "$REDIS_PASSWORD"}' in _valkey_command()
    # optional: this overlay is also what the offline bundle renders, and an
    # install whose hand-made Secret has no REDIS_PASSWORD yet must keep
    # starting. The deploy script always writes the key.
    assert _from_secret(_env(redis).get("REDIS_PASSWORD")) == (SECRET, "REDIS_PASSWORD", True)


def test_valkey_keeps_its_durability_settings():
    """Positive case: the flags that make the broker durable are all still there."""
    line = _valkey_command()
    assert line.startswith("exec valkey-server "), line
    for flag in ("--appendonly yes", "--appendfsync everysec", "--maxmemory 512mb",
                 "--maxmemory-policy volatile-lru"):
        assert flag in line, flag


def test_valkey_probes_sign_in_and_require_the_pong():
    """Positive case: with a password set, a bare ``valkey-cli ping`` gets a
    NOAUTH error reply. The probes sign in and look for PONG; REDISCLI_AUTH
    keeps ``oc exec ... valkey-cli`` working without a flag."""
    redis = _container("testlookup-redis")
    assert _from_secret(_env(redis).get("REDISCLI_AUTH")) == (SECRET, "REDIS_PASSWORD", True)
    for probe in ("readinessProbe", "livenessProbe"):
        command = redis[probe]["exec"]["command"]
        assert command[:2] == ["sh", "-c"], command
        assert 'valkey-cli -a "$REDIS_PASSWORD"' in command[2] and "grep -q PONG" in command[2], command


def test_every_redis_client_reads_its_urls_from_the_secret():
    clients = {name: pod["containers"][0] for name, pod in _deployments().items()
               if any((source.get("configMapRef") or {}).get("name") == "testlookup-config"
                      for source in pod["containers"][0].get("envFrom") or [])}
    assert len(clients) == REDIS_CLIENTS, sorted(clients)
    # optional for the same reason as Valkey's own key: without the key the
    # ConfigMap's password-less URL applies, which is right for a Valkey that
    # has no password either.
    wrong = [f"{name}:{key}" for name, container in clients.items() for key in REDIS_URL_KEYS
             if _from_secret(_env(container).get(key)) != (SECRET, key, True)]
    assert not wrong, f"these still read the password-less URL of the ConfigMap: {wrong}"


def test_the_base_configmap_keeps_its_urls_and_holds_no_password():
    """The cloud overlays read these keys; a password never goes into a ConfigMap."""
    data = yaml.safe_load((ROOT / "k8s" / "base" / "configmap.yaml").read_text(encoding="utf-8"))["data"]
    for key in REDIS_URL_KEYS:
        assert data[key].startswith("redis://testlookup-redis:6379/"), data[key]
    rendered = next(d for d in _rendered() if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "testlookup-config")
    assert not [key for key, value in rendered["data"].items() if "@" in str(value) and "://" in str(value)]


# ── MongoDB: the manifests ─────────────────────────────────────────────────

def test_mongo_takes_its_root_user_from_the_secret_when_there_is_one():
    env = _env(_container("testlookup-mongo"))
    # optional: an install that has not switched authentication on has no such
    # keys, and its MongoDB must start exactly as before.
    assert _from_secret(env.get("MONGO_INITDB_ROOT_USERNAME")) == (SECRET, "MONGO_ROOT_USERNAME", True)
    assert _from_secret(env.get("MONGO_INITDB_ROOT_PASSWORD")) == (SECRET, "MONGO_ROOT_PASSWORD", True)


def test_no_manifest_carries_a_password_value():
    for pod in _deployments().values():
        for container in pod["containers"]:
            for entry in container.get("env") or []:
                if "PASSWORD" in entry["name"] or entry["name"] in (*REDIS_URL_KEYS, "REDISCLI_AUTH", "MONGO_URI"):
                    assert "value" not in entry, f"{entry['name']} is a literal in a manifest"


# ── The deploy script ──────────────────────────────────────────────────────

BODY = r"""
# The Valkey container's own command line, with a stand-in that records its arguments.
scenario valkey
mkdir -p "$FAKE_DIR/bin"
cat > "$FAKE_DIR/bin/valkey-server" <<'STUB'
#!/bin/sh
printf '%s\n' "$@" > "$ARGS_FILE"
STUB
chmod +x "$FAKE_DIR/bin/valkey-server"
# cd first: FAKE_DIR can be a C:/ path, whose colon would split PATH.
( cd "$FAKE_DIR/bin"; export PATH="$PWD:$PATH" ARGS_FILE="$FAKE_DIR/with-password.txt" REDIS_PASSWORD=stubredispassword
  sh -c "$VALKEY_COMMAND" )
( cd "$FAKE_DIR/bin"; unset REDIS_PASSWORD; export PATH="$PWD:$PATH" ARGS_FILE="$FAKE_DIR/without-password.txt"
  sh -c "$VALKEY_COMMAND" )

ENABLE_MONGO_AUTH=false
scenario upgrade                      # an install made before Redis had a password
mkdir -p "$FAKE_DIR/secret"; printf 'c3R1Yg==' > "$FAKE_DIR/secret/POSTGRES_PASSWORD"
touch "$FAKE_DIR/has-mongo" "$FAKE_DIR/has-mongo-pvc"
ensure_redis_credentials; ensure_mongo_credentials
ensure_redis_credentials; ensure_mongo_credentials      # the next deploy
echo "upgrade: MONGO_AUTH_JUST_ENABLED=$MONGO_AUTH_JUST_ENABLED"

ENABLE_MONGO_AUTH=true
scenario mongo-running                # opt-in on an install with data
mkdir -p "$FAKE_DIR/secret"; touch "$FAKE_DIR/has-mongo" "$FAKE_DIR/has-mongo-pvc"
export FAKE_EXEC_REPLY=TESTLOOKUP_ROOT_USER_READY
ensure_mongo_credentials
echo "mongo-running: MONGO_AUTH_JUST_ENABLED=$MONGO_AUTH_JUST_ENABLED"
ensure_mongo_credentials              # the next deploy keeps the same user
echo "mongo-running-again: MONGO_AUTH_JUST_ENABLED=$MONGO_AUTH_JUST_ENABLED"

scenario mongo-user-not-created       # the server did not confirm the user
mkdir -p "$FAKE_DIR/secret"; touch "$FAKE_DIR/has-mongo" "$FAKE_DIR/has-mongo-pvc"
export FAKE_EXEC_REPLY="MongoServerError: not confirmed"
( ensure_mongo_credentials ) && echo "mongo-user-not-created: ACCEPTED" || echo "mongo-user-not-created: REFUSED"

scenario mongo-stopped-with-data      # a data volume but no server to create the user in
mkdir -p "$FAKE_DIR/secret"; touch "$FAKE_DIR/has-mongo-pvc"
( ensure_mongo_credentials ) && echo "mongo-stopped-with-data: ACCEPTED" || echo "mongo-stopped-with-data: REFUSED"

scenario mongo-fresh                  # first install: the image creates the user itself
mkdir -p "$FAKE_DIR/secret"
ensure_mongo_credentials
echo "mongo-fresh: MONGO_AUTH_JUST_ENABLED=$MONGO_AUTH_JUST_ENABLED"
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    work = tmp_path_factory.mktemp("openshift-stores")
    result = run_scenarios(work, BODY, {"VALKEY_COMMAND": _valkey_command()})
    assert result.returncode == 0, result.stderr
    return work, result.stdout


def _patches(directory) -> list[dict[str, str]]:
    """Each Secret patch the script sent, decoded: key -> clear value."""
    out = []
    for path in sorted(directory.glob("patch.*.json"), key=lambda p: int(p.name.split(".")[1])):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))["data"]
        out.append({key: base64.b64decode(value).decode() for key, value in data.items()})
    return out


def test_valkey_requires_the_password_it_is_given(run):
    work, _ = run
    arguments = read(work / "valkey" / "with-password.txt").split()
    assert arguments[-2:] == ["--requirepass", "stubredispassword"], arguments
    assert arguments[:2] == ["--appendonly", "yes"]


def test_valkey_still_starts_for_an_install_whose_secret_has_no_password_yet(run):
    """Positive case for the offline bundle: its hand-made Secret gets the key
    when the operator follows the upgrade note; until then Valkey runs as before."""
    work, _ = run
    arguments = read(work / "valkey" / "without-password.txt").split()
    assert arguments and "--requirepass" not in arguments, arguments
    assert arguments == read(work / "valkey" / "with-password.txt").split()[:-2]


def test_an_older_install_gets_a_redis_password_once(run):
    work, _ = run
    upgrade = work / "upgrade"
    patches = _patches(upgrade)
    assert len(patches) == 1, "the second run changed the Secret again (the password must be reused)"
    patch = patches[0]
    password = patch["REDIS_PASSWORD"]
    assert len(password) >= 16 and password.isalnum()
    assert patch["REDIS_URL"] == f"redis://:{password}@testlookup-redis:6379/0"
    assert patch["CELERY_BROKER_URL"] == f"redis://:{password}@testlookup-redis:6379/0"
    assert patch["CELERY_RESULT_BACKEND"] == f"redis://:{password}@testlookup-redis:6379/1"
    assert credential(upgrade / ".openshift-credentials", "REDIS_PASSWORD") == password
    assert password not in read(upgrade / "argv.log"), "the Redis password is on a command line"


def test_mongo_is_left_alone_until_the_operator_opts_in(run):
    work, stdout = run
    assert not [key for patch in _patches(work / "upgrade") for key in patch if "MONGO" in key]
    assert " exec " not in read(work / "upgrade" / "argv.log")
    assert "upgrade: MONGO_AUTH_JUST_ENABLED=false" in stdout


def test_opting_in_creates_the_user_before_the_secret_turns_authentication_on(run):
    work, stdout = run
    scenario = work / "mongo-running"
    calls = read(scenario / "argv.log").splitlines()
    exec_at = next(i for i, call in enumerate(calls) if " exec -i deployment/testlookup-mongo " in f" {call} ")
    patch_at = next(i for i, call in enumerate(calls) if "patch secret" in call)
    assert exec_at < patch_at, "the Secret was changed before the root user existed"
    patches = _patches(scenario)
    assert len(patches) == 1, "the second run changed the Secret again"
    user, password = patches[0]["MONGO_ROOT_USERNAME"], patches[0]["MONGO_ROOT_PASSWORD"]
    assert patches[0]["MONGO_URI"] == f"mongodb://{user}:{password}@testlookup-mongo:27017/?authSource=admin"
    script = read(scenario / f"stdin.{exec_at + 1}")
    assert "createUser" in script and password in script and '"root"' in script
    assert password not in "\n".join(calls), "the MongoDB password is on a command line"
    assert credential(scenario / ".openshift-credentials", "MONGO_ROOT_PASSWORD") == password
    assert "mongo-running: MONGO_AUTH_JUST_ENABLED=true" in stdout
    assert "mongo-running-again: MONGO_AUTH_JUST_ENABLED=false" in stdout


@pytest.mark.parametrize("scenario", ["mongo-user-not-created", "mongo-stopped-with-data"])
def test_authentication_is_not_switched_on_when_the_user_cannot_be_created(run, scenario):
    work, stdout = run
    assert f"{scenario}: REFUSED" in stdout
    assert "patch secret" not in read(work / scenario / "argv.log"), (
        "the Secret got MongoDB credentials although no user exists: the application would be locked out"
    )


def test_a_first_install_needs_no_running_server(run):
    work, stdout = run
    assert " exec " not in read(work / "mongo-fresh" / "argv.log")
    assert "MONGO_ROOT_PASSWORD" in _patches(work / "mongo-fresh")[0]
    assert "mongo-fresh: MONGO_AUTH_JUST_ENABLED=true" in stdout


def test_the_script_sets_the_credentials_before_it_applies():
    script = SCRIPT.read_text(encoding="utf-8")
    main = script[script.index("# <<< secret helpers"):]
    apply_at = re.search(r'^"\$KCLI" apply -k "\$OVERLAY_DIR"$', main, re.MULTILINE).start()
    for call in ("ensure_redis_credentials", "ensure_mongo_credentials"):
        called_at = re.search(rf"^\s*{call}$", main, re.MULTILINE)
        assert called_at and called_at.start() < apply_at, f"{call} does not run before the apply"


def test_a_dry_run_changes_no_credentials():
    """--dry-run renders and exits; it must not create a database user or add a password."""
    script = SCRIPT.read_text(encoding="utf-8")
    main = script[script.index("# <<< secret helpers"):]
    dry_run_exit = main.index('log "Dry run complete')
    assert re.search(r"^\s*ensure_redis_credentials$", main, re.MULTILINE).start() > dry_run_exit
    mongo = re.search(r"^\s*ensure_mongo_credentials$", main, re.MULTILINE).start()
    assert 'if [ "$DRY_RUN" = false ]; then' in main[mongo - 80:mongo]


def test_mongo_authentication_is_off_unless_asked_for():
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'ENABLE_MONGO_AUTH="${ENABLE_MONGO_AUTH:-false}"' in script
    example = (ROOT / "openshiftsetup" / "artifactory.env.example").read_text(encoding="utf-8")
    assert 'ENABLE_MONGO_AUTH="false"' in example
