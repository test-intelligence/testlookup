"""On the K3s install, Redis and MongoDB accept only a client with the password.

Until the security audit of 2026-11-18 the in-cluster Valkey and MongoDB took
any client that could reach their port: NetworkPolicy, which tells clients
apart by pod label, was the only barrier in front of the Celery broker, the
token-revocation store and the raw logs.

The rules pinned here:

* Valkey starts with ``--requirepass`` and MongoDB with a root user, both read
  from the app Secret; no password is written in a manifest.
* Every workload that talks to Redis gets its Redis URLs from the Secret (an
  ``env`` entry wins over the ConfigMap that ``envFrom`` loads). The base
  ConfigMap keeps its password-less URLs: the cloud overlays depend on them.
* ``k3ssetup/deploy-k3s.sh`` generates the passwords once, keeps existing
  ones, and upgrades a running install in place: the MongoDB root user is
  created in the running database BEFORE authentication is switched on, and a
  failure stops the deploy before anything is applied.
"""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from tests.shell_utils import bash_environment

pytestmark = pytest.mark.regression

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "k3ssetup" / "deploy-k3s.sh"
OVERLAY = REPO / "k8s" / "overlays" / "k3s"
BASE_CONFIG = REPO / "k8s" / "base" / "configmap.yaml"
SECRET = "testlookup-secrets"
REDIS_URL_KEYS = ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
# Deployments that run the backend image with the app configuration.
REDIS_CLIENTS = {
    "testlookup-backend", "testlookup-beat", "testlookup-worker-critical", "testlookup-worker-ingestion",
    "testlookup-worker-ai", "testlookup-worker-default", "testlookup-worker-children",
}


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _container(manifest: str) -> dict:
    deployment = next(d for d in _docs(OVERLAY / manifest) if d["kind"] == "Deployment")
    [container] = deployment["spec"]["template"]["spec"]["containers"]
    return container


def _secret_env(container: dict) -> dict[str, str]:
    """env name -> Secret key, for entries read from the app Secret."""
    found = {}
    for entry in container.get("env") or []:
        ref = (entry.get("valueFrom") or {}).get("secretKeyRef") or {}
        if ref.get("name") == SECRET:
            found[entry["name"]] = ref["key"]
    return found


# ── the manifests ────────────────────────────────────────────────────────────

def test_valkey_starts_with_the_password_from_the_secret():
    container = _container("infra-redis.yaml")
    command = container["command"]
    assert "--requirepass" in command, "Valkey accepts every client without --requirepass"
    assert command[command.index("--requirepass") + 1] == "$(REDIS_PASSWORD)", (
        "the password must come from the container environment, never be written here"
    )
    assert _secret_env(container).get("REDIS_PASSWORD") == "REDIS_PASSWORD"
    # The probes must sign in, or a healthy server reads as dead (and the other
    # way round: `redis-cli ping` exits 0 on a NOAUTH error reply).
    for probe in ("readinessProbe", "livenessProbe"):
        probe_command = " ".join(container[probe]["exec"]["command"])
        assert "$REDIS_PASSWORD" in probe_command and "PONG" in probe_command, probe


def test_mongodb_gets_a_root_user_from_the_secret():
    env = _secret_env(_container("infra-mongo.yaml"))
    assert env.get("MONGO_INITDB_ROOT_USERNAME") == "MONGO_ROOT_USERNAME"
    assert env.get("MONGO_INITDB_ROOT_PASSWORD") == "MONGO_ROOT_PASSWORD"


@pytest.mark.parametrize("manifest", ["infra-redis.yaml", "infra-mongo.yaml"])
def test_no_password_is_written_in_a_data_store_manifest(manifest):
    for entry in _container(manifest).get("env") or []:
        if re.search(r"PASSWORD|AUTH|USERNAME", entry["name"]):
            assert "value" not in entry, f"{manifest}: {entry['name']} carries a literal value"


def _rendered_overlay() -> list[dict]:
    for argv in (["kustomize", "build"], ["kubectl", "kustomize"]):
        if shutil.which(argv[0]):
            done = subprocess.run(
                [*argv, str(OVERLAY)], capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            assert done.returncode == 0, done.stderr
            return [d for d in yaml.safe_load_all(done.stdout) if d]
    pytest.skip("no kustomize or kubectl on PATH")
    raise AssertionError("unreachable: pytest.skip raises")


def test_every_redis_client_reads_its_urls_from_the_secret():
    docs = _rendered_overlay()
    config = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "testlookup-config")
    in_config = {key for key, value in config["data"].items() if str(value).startswith(("redis://", "rediss://"))}
    assert in_config == set(REDIS_URL_KEYS), (
        f"the ConfigMap carries a Redis URL this overlay does not override: {sorted(in_config)}"
    )
    clients = set()
    for doc in docs:
        if doc["kind"] != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            loads_config = any(
                (source.get("configMapRef") or {}).get("name") == "testlookup-config"
                for source in container.get("envFrom") or []
            )
            if not loads_config:
                continue
            clients.add(doc["metadata"]["name"])
            env = _secret_env(container)
            for key in REDIS_URL_KEYS:
                assert env.get(key) == key, (
                    f"{doc['metadata']['name']}/{container['name']}: {key} still comes from the "
                    "ConfigMap, whose URL has no password"
                )
    assert clients == REDIS_CLIENTS, sorted(clients ^ REDIS_CLIENTS)


def test_the_base_configmap_keeps_its_urls_for_the_cloud_overlays():
    [config] = [d for d in _docs(BASE_CONFIG) if d["kind"] == "ConfigMap"]
    assert config["data"]["REDIS_URL"] == "redis://testlookup-redis:6379/0"
    assert config["data"]["CELERY_BROKER_URL"] == "redis://testlookup-redis:6379/0"
    assert config["data"]["CELERY_RESULT_BACKEND"] == "redis://testlookup-redis:6379/1"


# ── the deploy script ────────────────────────────────────────────────────────

def _find_bash() -> str | None:
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    return None if found and "system32" in found.lower() else found


BASH = _find_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="requires POSIX bash")
FUNCTIONS = (
    "read_app_secret", "app_secret_value", "new_datastore_password", "patch_app_secret",
    "ensure_datastore_credentials", "record_datastore_credentials", "live_pod", "mongo_run_js",
    "ensure_mongo_root_user", "prepare_mongo_authentication",
)


def _function(name: str) -> str:
    source = DEPLOY.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{$", source, re.M)
    assert match, f"{name}() is no longer in deploy-k3s.sh"
    lines = source[match.start():].splitlines()
    for index, line in enumerate(lines[1:], 1):
        if line == "}":
            return "\n".join(lines[: index + 1])
    raise AssertionError(f"no closing brace for {name}()")


def _constants() -> str:
    source = DEPLOY.read_text(encoding="utf-8")
    return "\n".join(
        line for line in source.splitlines()
        if re.match(r"^(MONGO_ROOT_USER_DEFAULT|MONGO_IN_CLUSTER_URI|MONGO_[A-Z_]+_JS)=", line)
    )


def _bash(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    runner = tmp_path / "runner.sh"
    prelude = (
        "set -euo pipefail\nNAMESPACE=testlookup\n"
        'log() { echo "[+] $1"; }\nwarn() { echo "[!] $1"; }\n'
        + _constants() + "\n" + "\n".join(_function(name) for name in FUNCTIONS) + "\n"
    )
    runner.write_text(prelude + body, encoding="utf-8", newline="\n")
    assert BASH is not None
    return subprocess.run(
        [BASH, str(runner)], cwd=tmp_path, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=bash_environment(BASH), timeout=120, check=False,
    )


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


# A fake kubectl that keeps the Secret in ./secret.env (KEY=<base64>), applies
# `patch secret ... -p <json>` to it with sed-free shell, and logs every call.
FAKE_SECRET_KUBECTL = textwrap.dedent(
    r"""
    kubectl() {
      printf '%s\n' "$*" | sed -E 's/"[A-Z_]+":"[^"]*"/"K":"<hidden>"/g' >> kubectl.log
      case " $* " in
        *" get secret "*)
          [ "${SECRET_READ_FAILS:-false}" = true ] && return 1
          cat secret.env ;;
        *" patch secret "*)
          local json=""
          while [ "$#" -gt 0 ]; do [ "$1" = -p ] && json=$2; shift; done
          printf '%s\n' "$json" > last-patch.json
          printf '%s\n' "$json" | sed -e 's/^{"stringData":{//' -e 's/}}$//' | tr ',' '\n' \
            | sed -E 's/^"([A-Z_]+)":"(.*)"$/\1 \2/' | while read -r key value; do
                grep -v "^${key}=" secret.env > secret.tmp || true
                printf '%s=%s\n' "$key" "$(printf '%s' "$value" | base64 | tr -d '\n')" >> secret.tmp
                mv secret.tmp secret.env
              done ;;
      esac
    }
    """
)


def _secret(tmp_path: Path) -> dict[str, str]:
    values = {}
    for line in (tmp_path / "secret.env").read_text(encoding="utf-8").splitlines():
        key, _, encoded = line.partition("=")
        values[key] = base64.b64decode(encoded).decode()
    return values


@needs_bash
def test_passwords_are_generated_once_kept_on_a_rerun_and_never_on_a_failed_read(tmp_path: Path):
    """An install from before this change: the Secret exists without the keys."""
    (tmp_path / "secret.env").write_text(
        f"MONGO_URI={_b64('mongodb://testlookup-mongo:27017')}\nJWT_SECRET_KEY={_b64('not-a-real-key')}\n",
        encoding="utf-8", newline="\n",
    )
    body = FAKE_SECRET_KUBECTL + textwrap.dedent(
        """
        CREDS_FILE="$PWD/creds/.k3s-credentials"
        mkdir -p creds && printf 'POSTGRES_PASSWORD=kept-as-it-was\\n' > "$CREDS_FILE"
        ensure_datastore_credentials
        record_datastore_credentials
        echo "FIRST generated=$DATASTORE_PASSWORDS_GENERATED patches=$(grep -c ' patch secret ' kubectl.log)"
        cp secret.env secret.first && cp "$CREDS_FILE" creds.first
        ensure_datastore_credentials
        record_datastore_credentials
        echo "SECOND generated=$DATASTORE_PASSWORDS_GENERATED patches=$(grep -c ' patch secret ' kubectl.log)"
        cmp -s secret.env secret.first && cmp -s "$CREDS_FILE" creds.first && echo SECOND_RUN_CHANGED_NOTHING
        SECRET_READ_FAILS=true
        if ensure_datastore_credentials 2>/dev/null; then echo BLIP=accepted; else echo BLIP=refused; fi
        echo "BLIP patches=$(grep -c ' patch secret ' kubectl.log)"
        """
    )
    result = _bash(tmp_path, body)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "FIRST generated=true patches=1" in out
    assert "SECOND generated=false patches=1" in out, "a second run must not patch the Secret again"
    assert "SECOND_RUN_CHANGED_NOTHING" in out
    # The API not answering is not "the key is absent": no new password, no patch.
    assert "BLIP=refused" in out and "BLIP patches=1" in out

    secret = _secret(tmp_path)
    redis_pw, mongo_pw = secret["REDIS_PASSWORD"], secret["MONGO_ROOT_PASSWORD"]
    assert re.fullmatch(r"[A-Za-z0-9]{32}", redis_pw) and re.fullmatch(r"[A-Za-z0-9]{32}", mongo_pw)
    assert redis_pw != mongo_pw
    assert secret["MONGO_ROOT_USERNAME"] == "testlookup_root"
    # The same hosts, ports and database numbers as the ConfigMap, plus the credentials.
    [config] = [d for d in _docs(BASE_CONFIG) if d["kind"] == "ConfigMap"]
    for key in REDIS_URL_KEYS:
        plain = config["data"][key]
        assert secret[key] == plain.replace("redis://", f"redis://default:{redis_pw}@"), key
    # MONGO_URI changes only after the root user is known to work (next test).
    assert secret["MONGO_URI"] == "mongodb://testlookup-mongo:27017"
    assert secret["JWT_SECRET_KEY"] == "not-a-real-key"

    creds = (tmp_path / "creds" / ".k3s-credentials").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=kept-as-it-was\n" in creds
    for line in (f"REDIS_PASSWORD={redis_pw}", "MONGO_ROOT_USERNAME=testlookup_root", f"MONGO_ROOT_PASSWORD={mongo_pw}"):
        assert creds.count(line + "\n") == 1, line


# A fake cluster for the MongoDB step. MONGO_STATE says what the database is:
#   open         no access control, no root user yet (every install before this change)
#   open-user    no access control, the root user exists (a re-run before the apply)
#   auth-user    access control on, the root user signs in (after the upgrade)
#   auth-empty   access control on, no user at all (a new Deployment on an old volume)
#   auth-other   access control on, another password (the Secret was regenerated)
FAKE_MONGO_KUBECTL = textwrap.dedent(
    r"""
    kubectl() {
      case " $* " in
        *" get secret "*) cat secret.env; return 0 ;;
        *" patch secret "*) echo "patch" >> calls.log; printf '%s\n' "$*" > last-patch.txt; return 0 ;;
        *" get deployment "*) [ "$MONGO_DEPLOYED" = true ] && echo deployment.apps/testlookup-mongo; return 0 ;;
        *" get pod "*) [ "$MONGO_POD_RUNNING" = true ] && echo "testlookup-mongo-5d8f-abcde <none>"; return 0 ;;
        *" exec "*) ;;
        *) return 0 ;;
      esac
      # The password must travel in the environment of mongosh, not as its argument.
      case " $* " in *" -p "*|*" --password"*) echo "PASSWORD_ON_ARGV" >> calls.log ;; esac
      case " $* " in *" TL_MONGO_USER=testlookup_root TL_MONGO_PW=fakepw123 "*) ;; *) echo "NO_ENV" >> calls.log ;; esac
      local state; state=$(cat mongo.state)
      case "$*" in
        *createUser*)
          echo "write" >> calls.log
          case "$state" in
            open) echo open-user > mongo.state ;;
            auth-empty) echo auth-user > mongo.state ;;
            open-user) ;;
            *) return 1 ;;
          esac ;;
        *"adminDb.auth("*)
          echo "signin" >> calls.log
          case "$state" in open-user|auth-user) ;; *) return 1 ;; esac ;;
      esac
    }
    """
)


@needs_bash
def test_the_mongodb_root_user_exists_before_authentication_is_switched_on(tmp_path: Path):
    scenarios = [
        # name, deployed, pod running, state before
        ("upgrade", "true", "true", "open"),
        ("rerun", "true", "true", "open-user"),
        ("already", "true", "true", "auth-user"),
        ("oldvolume", "true", "true", "auth-empty"),
        ("mismatch", "true", "true", "auth-other"),
        ("nopod", "true", "false", "open"),
        ("fresh", "false", "false", "none"),
    ]
    (tmp_path / "secret.env").write_text(
        f"MONGO_URI={_b64('mongodb://testlookup-mongo:27017')}\n", encoding="utf-8", newline="\n",
    )
    body = FAKE_MONGO_KUBECTL + "MONGO_ROOT_USER=testlookup_root\nMONGO_ROOT_PW=fakepw123\n"
    for name, deployed, running, state in scenarios:
        body += textwrap.dedent(
            f"""
            MONGO_DEPLOYED={deployed}; MONGO_POD_RUNNING={running}
            echo {state} > mongo.state; : > calls.log; rm -f last-patch.txt
            if prepare_mongo_authentication >out.log 2>&1; then verdict=ok; else verdict=stopped; fi
            echo "{name}: $verdict state=$(cat mongo.state) calls=$(tr '\\n' ',' < calls.log) uri=$([ -f last-patch.txt ] && echo patched || echo untouched)"
            """
        )
    result = _bash(tmp_path, body)
    assert result.returncode == 0, result.stderr
    lines = dict(line.split(": ", 1) for line in result.stdout.splitlines() if ": " in line and "=" in line)
    # The upgrade: sign-in fails, the user is written, sign-in is checked, THEN the URI changes.
    assert lines["upgrade"] == "ok state=open-user calls=signin,write,signin,patch, uri=patched"
    # Idempotent: once the user signs in, nothing is written to the database again.
    assert lines["rerun"] == "ok state=open-user calls=signin,patch, uri=patched"
    assert lines["already"] == "ok state=auth-user calls=signin,patch, uri=patched"
    # Access control on, no user at all: the localhost exception still lets the user be created.
    assert lines["oldvolume"] == "ok state=auth-user calls=signin,write,signin,patch, uri=patched"
    # Refusals: the deploy stops and MONGO_URI is left as it was.
    assert lines["mismatch"] == "stopped state=auth-other calls=signin,write, uri=untouched"
    assert lines["nopod"] == "stopped state=open calls= uri=untouched"
    # A first install has no database yet: the image creates the user on the empty volume.
    assert lines["fresh"] == "ok state=none calls=patch, uri=patched"
    assert "PASSWORD_ON_ARGV" not in result.stdout and "NO_ENV" not in result.stdout
    # The last scenario's patch: the in-cluster host with the root user in front of it.
    patch = (tmp_path / "last-patch.txt").read_text(encoding="utf-8")
    wanted = "mongodb://" + "testlookup_root" + ":" + "fakepw123" + "@testlookup-mongo:27017/?authSource=admin"
    assert f'"MONGO_URI":"{wanted}"' in patch


@needs_bash
def test_a_custom_mongo_uri_is_left_alone(tmp_path: Path):
    (tmp_path / "secret.env").write_text(
        f"MONGO_URI={_b64('mongodb://mongo.example.test:27017/?tls=true')}\n", encoding="utf-8", newline="\n",
    )
    body = FAKE_MONGO_KUBECTL + textwrap.dedent(
        """
        MONGO_ROOT_USER=testlookup_root; MONGO_ROOT_PW=fakepw123
        MONGO_DEPLOYED=false; MONGO_POD_RUNNING=false; echo none > mongo.state; : > calls.log
        prepare_mongo_authentication
        echo "calls=$(cat calls.log)"
        """
    )
    result = _bash(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert "calls=\n" in result.stdout + "\n" and "patch" not in result.stdout.split("calls=")[1]
    assert "left unchanged" in result.stdout


def test_the_main_flow_prepares_the_stores_before_the_apply_and_checks_them_after():
    source = DEPLOY.read_text(encoding="utf-8")
    main = source[source.index("# ── Step 4: Generate and Apply Secrets"):]
    generate = main.index("ensure_datastore_credentials \\\n")
    prepare = main.index("prepare_mongo_authentication \\\n")
    migrate = main.index('bash "$REPO_ROOT/scripts/run-k8s-migrations.sh"')
    apply = main.index('kubectl apply -k "$RENDER_DIR"')
    verify = main.index("verify_datastore_authentication || DEPLOY_DEGRADED=true")
    assert generate < prepare < migrate < apply < verify
    # Both steps stop the script (error exits) rather than warn and carry on.
    for start in (generate, prepare):
        assert re.match(r"\S+ \\\n  \|\| error \"", main[start:]), main[start:start + 120]
    # The MongoDB password never appears as a mongosh argument in the script itself.
    assert not re.search(r"mongosh[^\n]*(\s-p\s|--password)", source)
    assert "process.env.TL_MONGO_PW" in source
