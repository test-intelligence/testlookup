"""First-run bootstrap never puts a secret on a command line or into its output.

Security review 2026-11-20, TL-08. On a first deploy:

* ``k3ssetup/deploy-k3s.sh`` created the application Secret with
  ``--from-literal=KEY=value`` arguments, printed the database and object-store
  credentials, passed the admin password as an ``env`` argument of
  ``kubectl exec`` and printed it in a banner;
* ``scripts/deploy-k8s.sh`` passed the admin password the same way, created
  the generated-password Secret with ``--from-literal``, and printed the
  password on a terminal;
* ``openshiftsetup/deploy-openshift-artifactory.sh`` printed a generated admin
  password in its summary;
* ``backend/scripts/create_admin.py``, run by the release stack's
  ``db-migrate`` service with no password set, printed a generated password
  into that service's log.

Every local user can read a running process's arguments, and deploy output is
routinely kept (a CI log, a ticket, a shared terminal).

The rule: a secret value reaches ``kubectl``/``oc`` on stdin or in an
owner-only temporary file, and is never echoed. A generated admin password is
kept in a Kubernetes Secret or the owner-only credentials file, and the output
says where. ``create_admin.py`` shows a password it generated only on a
terminal; without one it creates no admin.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.regression

REPO = Path(__file__).resolve().parents[3]
K3S = REPO / "k3ssetup" / "deploy-k3s.sh"
GENERIC = REPO / "scripts" / "deploy-k8s.sh"
OPENSHIFT = REPO / "openshiftsetup" / "deploy-openshift-artifactory.sh"
CREATE_ADMIN = REPO / "backend" / "scripts" / "create_admin.py"

#: Shell variables that hold a secret value in these scripts.
SECRET_VARIABLES = (
    "ADMIN_PASSWORD", "PG_PASSWORD", "MINIO_SECRET", "APP_SECRET", "JWT_SECRET", "WEBHOOK_SECRET",
    "DATABASE_URL", "REDIS_PW", "MONGO_ROOT_PW",
)


def _code(path: Path) -> str:
    """The script without comment lines and without the usage text it prints for the operator."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(lines)


def _expands(line: str, variable: str) -> bool:
    return re.search(r"\$\{?" + variable + r"\b", line) is not None


@pytest.mark.parametrize("script", [K3S, GENERIC, OPENSHIFT], ids=lambda p: p.name)
def test_no_secret_variable_is_printed(script: Path):
    """No ``echo``/``log``/``warn``/``printf`` to the screen expands a secret variable."""
    printed = []
    for line in _code(script).splitlines():
        stripped = line.strip()
        if not re.match(r"(echo|log|warn|error|header)\b", stripped):
            continue
        if any(_expands(stripped, variable) for variable in SECRET_VARIABLES):
            printed.append(stripped)
    assert not printed, f"{script.name} prints a secret value: {printed}"


@pytest.mark.parametrize("script", [K3S, GENERIC, OPENSHIFT], ids=lambda p: p.name)
def test_no_secret_variable_is_an_argument_of_a_cluster_command(script: Path):
    """``--from-literal=...$SECRET``, ``env SECRET=$SECRET`` and ``-p '{...}'`` are all arguments."""
    code = _code(script)
    offending = []
    for line in code.splitlines():
        if "--from-literal" in line and any(_expands(line, variable) for variable in SECRET_VARIABLES):
            offending.append(line.strip())
        if re.search(r'ADMIN_PASSWORD="\$\{?ADMIN_PASSWORD\}?"\s*\\?$', line.strip()):
            offending.append(line.strip())
        if re.search(r"patch secret\b.*\s-p\s", line):
            offending.append(line.strip())
    assert not offending, f"{script.name} passes a secret as an argument: {offending}"


@pytest.mark.parametrize("script", [K3S, GENERIC, OPENSHIFT], ids=lambda p: p.name)
def test_the_admin_password_reaches_the_pod_on_stdin(script: Path):
    code = _code(script)
    start = code.index("exec -i \"$BACKEND_POD\"")
    call = code[max(0, start - 200): start + 700]
    assert "printf '%s\\n' \"$ADMIN_PASSWORD\" |" in call, "the password is not piped to the exec"
    assert "read -r ADMIN_PASSWORD" in call
    assert 'sh -c "python /app/scripts/create_admin.py"' in call, "the admin is no longer created"


def test_k3s_creates_the_application_secret_from_stdin_with_every_key():
    code = _code(K3S)
    helper = code[code.index("create_app_secret() {"):][:500]
    assert "create -f -" in helper and "base64" in helper
    call = code[code.index("create_app_secret \\"):][:600]
    for key in ("APP_SECRET_KEY", "JWT_SECRET_KEY", "DATABASE_URL", "POSTGRES_PASSWORD",
                "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MONGO_URI", "WEBHOOK_SECRET"):
        assert f'"{key}=' in call, f"testlookup-secrets is no longer created with {key}"
    patch = code[code.index("patch_app_secret() {"):][:600]
    assert "--patch-file" in patch and "umask 077" in patch


@pytest.mark.parametrize("script", [GENERIC, OPENSHIFT], ids=lambda p: p.name)
def test_a_generated_admin_password_is_kept_in_a_secret_and_the_output_says_where(script: Path):
    code = _code(script)
    assert "testlookup-initial-admin" in code
    assert "get secret testlookup-initial-admin -o jsonpath='{.data.password}' | base64 -d" in code


def test_k3s_says_where_the_generated_admin_password_is():
    code = _code(K3S)
    banner = code[code.index("ADMIN LOGIN"):][:900]
    assert "${CREDS_FILE}" in banner
    # The file the banner points at is created owner-only before it is written.
    write = code[: code.index("ADMIN_PASSWORD=${ADMIN_PASSWORD}")][-400:]
    assert 'umask 077; touch "$CREDS_FILE"' in write


# ── create_admin.py ──────────────────────────────────────────────────────────


def _load_create_admin():
    spec = importlib.util.spec_from_file_location("create_admin_tl08", CREATE_ADMIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Session:
    def __init__(self) -> None:
        self.added: list = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _row) -> None:
        return None


def _run_create_admin(monkeypatch, capsys, *, terminal: bool, environ: dict[str, str]):
    import app.core.security as security
    import app.db.postgres as postgres

    module = _load_create_admin()
    session = _Session()
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: session, raising=False)
    monkeypatch.setattr(security, "get_password_hash", lambda password: "hashed:" + password)
    monkeypatch.setattr(module, "output_is_a_terminal", lambda: terminal)
    for key in ("ADMIN_PASSWORD", "ADMIN_PASSWORD_GENERATED", "ADMIN_USERNAME", "ADMIN_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    asyncio.run(module.main())
    sys.modules.pop("create_admin_tl08", None)
    return session, capsys.readouterr().out


def test_without_a_password_and_without_a_terminal_no_admin_is_created(monkeypatch, capsys):
    """The release stack's db-migrate with INITIAL_ADMIN_PASSWORD empty: its output is a log."""
    session, out = _run_create_admin(monkeypatch, capsys, terminal=False, environ={})

    assert session.added == [] and session.committed is False
    assert out.startswith("admin user not created")
    assert "password:" not in out.lower().replace("no password was given", "")


def test_without_a_password_on_a_terminal_the_generated_one_is_shown_once(monkeypatch, capsys):
    session, out = _run_create_admin(monkeypatch, capsys, terminal=True, environ={})

    (admin,) = session.added
    assert out.startswith("admin user created")
    shown = re.search(r"password: (\S+)", out).group(1)
    assert admin.hashed_password == "hashed:" + shown
    assert admin.must_change_password is True


@pytest.mark.parametrize("terminal", [True, False])
def test_a_supplied_password_creates_the_admin_and_is_never_printed(monkeypatch, capsys, terminal):
    supplied = "supplied-Pw-7f3a91"
    session, out = _run_create_admin(
        monkeypatch, capsys, terminal=terminal,
        environ={"ADMIN_PASSWORD": supplied, "ADMIN_PASSWORD_GENERATED": "true"},
    )

    (admin,) = session.added
    assert admin.hashed_password == "hashed:" + supplied
    assert out.startswith("admin user created")
    assert supplied not in out
