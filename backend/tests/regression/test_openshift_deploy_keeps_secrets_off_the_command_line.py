"""The OpenShift deploy script never puts a secret value on a command line.

Another local user can read every process's arguments while it runs.
``openshiftsetup/deploy-openshift-artifactory.sh`` passed the application
Secret's values as ``--from-literal=`` arguments, the Artifactory password as
``--docker-password=`` and ``curl -u``, the admin password as an ``env``
argument of ``oc exec``, and the Route's private key inside ``-p``.

The rule: a secret value reaches ``oc``/``kubectl``/``curl`` on stdin or in an
owner-only temporary file, never as an argument. The script still creates the
same Secrets with the same keys.
"""
from __future__ import annotations

import base64
import re

import pytest

from tests.regression.openshift_deploy_harness import SCRIPT, read, run_scenarios

pytestmark = pytest.mark.regression

APP_SECRET_KEYS = (
    "APP_SECRET_KEY", "JWT_SECRET_KEY", "DATABASE_URL", "POSTGRES_PASSWORD",
    "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MONGO_URI", "WEBHOOK_SECRET",
)
REGISTRY_PASSWORD = 'stub-registry "pass\\word'  # a quote and a backslash: both must survive
APP_VALUE = "stub-app-value-0001"
PATCH_VALUE = "stub-patched-value-0002"

BODY = r"""
scenario create
create_secret testlookup-secrets Opaque APP_SECRET_KEY "$APP_VALUE" MONGO_URI "stub-uri"
create_secret artifactory-pull kubernetes.io/dockerconfigjson .dockerconfigjson \
  "$(dockerconfig_json registry.example.test stub-user "$REGISTRY_PASSWORD" stub@example.test)"
scenario merge
mkdir -p "$FAKE_DIR/secret"
merge_secret_keys testlookup-secrets STUB_KEY "$PATCH_VALUE"
ls "$(dirname "$(mktemp -u)")" > "$FAKE_DIR/tmp-after.txt" 2>/dev/null || true
"""


def _code() -> str:
    """The script without its comment lines (a comment may name the old flags)."""
    return "\n".join(line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("pattern, what", [
    (r"--from-literal", "a Secret value as a --from-literal argument"),
    (r"--docker-password", "the registry password as a --docker-password argument"),
    (r"curl\b[^\n|]*\s-u\s", "the registry password as a curl -u argument"),
    (r"ADMIN_PASSWORD=\"\$ADMIN_PASSWORD\"", "the admin password as an env argument of exec"),
    (r"-p\s+\"\$PATCH_JSON\"", "the Route private key inside -p"),
])
def test_no_secret_is_passed_as_an_argument(pattern: str, what: str):
    found = re.findall(pattern, _code())
    assert not found, f"the script passes {what}: {found}"


def test_the_application_secret_still_has_every_key():
    """Positive case: the Secret a fresh install gets is unchanged in shape."""
    code = _code()
    call = code[code.index("create_secret testlookup-secrets Opaque"):][:900]
    missing = [key for key in APP_SECRET_KEYS if f" {key} " not in call]
    assert not missing, f"testlookup-secrets is no longer created with {missing}"


def test_the_registry_login_still_reads_the_password_from_stdin():
    assert "--password-stdin" in _code()


def test_the_admin_password_is_read_from_stdin_inside_the_pod():
    code = _code()
    exec_call = code[code.index("ADMIN_OUT=$("):][:700]
    assert "exec -i" in exec_call, "the exec that creates the admin does not take stdin"
    assert "read -r ADMIN_PASSWORD" in exec_call
    assert "create_admin.py" in exec_call, "the admin is no longer created"


def test_the_route_key_is_patched_from_a_file():
    code = _code()
    route_patch = code[code.index("patch route"):][:200]
    assert "--patch-file" in route_patch


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    work = tmp_path_factory.mktemp("openshift-secrets")
    result = run_scenarios(work, BODY, {
        "APP_VALUE": APP_VALUE, "REGISTRY_PASSWORD": REGISTRY_PASSWORD, "PATCH_VALUE": PATCH_VALUE,
    })
    assert result.returncode == 0, result.stderr
    return work


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def test_created_secrets_travel_on_stdin_only(run):
    argv = read(run / "create" / "argv.log")
    assert "create -f -" in argv
    for value in (APP_VALUE, REGISTRY_PASSWORD, _b64(APP_VALUE)):
        assert value not in argv, "a secret value is on the command line"
    app_manifest = read(run / "create" / "stdin.1")
    assert "kind: Secret" in app_manifest and "name: testlookup-secrets" in app_manifest
    assert f'"APP_SECRET_KEY": {_b64(APP_VALUE)}' in app_manifest


def test_the_pull_secret_carries_the_exact_registry_password(run):
    """Positive case: the cluster can still pull. The password round-trips byte for byte."""
    import json

    import yaml
    manifest = yaml.safe_load(read(run / "create" / "stdin.2"))
    assert manifest["type"] == "kubernetes.io/dockerconfigjson"
    config = json.loads(base64.b64decode(manifest["data"][".dockerconfigjson"]))
    entry = config["auths"]["registry.example.test"]
    assert entry["username"] == "stub-user" and entry["password"] == REGISTRY_PASSWORD
    assert base64.b64decode(entry["auth"]).decode() == f"stub-user:{REGISTRY_PASSWORD}"


def test_added_keys_travel_in_a_file_that_is_removed(run):
    argv = read(run / "merge" / "argv.log")
    assert "patch secret testlookup-secrets --type=merge --patch-file" in argv
    assert PATCH_VALUE not in argv and _b64(PATCH_VALUE) not in argv
    assert f'"STUB_KEY":"{_b64(PATCH_VALUE)}"' in read(run / "merge" / "patch.1.json")
    patch_path = argv.strip().rsplit(" ", 1)[1]
    assert patch_path.rsplit("/", 1)[1] not in read(run / "merge" / "tmp-after.txt"), (
        "the temporary patch file is left behind"
    )

