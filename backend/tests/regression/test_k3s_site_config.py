"""The single-node K3s path (k3ssetup/) carries no site of its own.

Until the OSS release the deploy scripts and k8s/overlays/k3s hard-coded
one owner's network: node IPs, an SSH user, a MetalLB range, the host name
the ingress answered on, a hosted-LLM posture and a well-known default admin
password. Every one of those now comes from a local site file
(``TESTLOOKUP_K3S_ENV``) that is never committed, and a first install
generates the admin password instead of shipping one.

These tests pin that contract, and the two promises an existing install
depends on: a deploy that did not load its site file cannot silently move the
ingress to another host, and an existing admin account is never modified.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.shell_utils import bash_environment

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "k3ssetup" / "deploy-k3s.sh"
BOOTSTRAP = REPO / "k3ssetup" / "bootstrap-k3s.sh"
LIB = REPO / "k3ssetup" / "lib-site-config.sh"
OVERLAY = REPO / "k8s" / "overlays" / "k3s"


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


def _function(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    match = re.search(rf"^([ \t]*){re.escape(name)}\(\) \{{$", source, re.M)
    assert match, f"{name}() is no longer in {path.name}"
    indent = match.group(1)
    lines = source[match.start():].splitlines()
    for index, line in enumerate(lines[1:], 1):
        if line == f"{indent}}}":
            return textwrap.dedent("\n".join(lines[: index + 1]))
    raise AssertionError(f"no closing brace for {name}()")


def _bash(tmp_path: Path, body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    runner = tmp_path / "runner.sh"
    runner.write_text("set -euo pipefail\n" + body, encoding="utf-8", newline="\n")
    assert BASH is not None
    environment = bash_environment(BASH)
    for key in [k for k in environment if k.startswith(("K3S_", "HOMELAB_"))
                or k in ("TESTLOOKUP_K3S_ENV", "TESTLOOKUP_HOMELAB_ENV")]:
        environment.pop(key)
    environment["HOME"] = str(tmp_path / "home")
    environment.pop("XDG_CONFIG_HOME", None)
    environment.update(env or {})
    return subprocess.run(
        [BASH, str(runner)], cwd=tmp_path, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=environment, timeout=60, check=False,
    )


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


# ── nothing site-specific is tracked ────────────────────────────────────────

_PRIVATE = re.compile(
    r"192\.168\.\d+\.\d+|(?<![\w.-])testlookup\.local(?![\w.-])|\blabadmin\b|k8s-node\d"
)


@pytest.mark.parametrize(
    "path",
    [DEPLOY, BOOTSTRAP, LIB, REPO / "k3ssetup" / "DEPLOY_TESTLOOKUP.md",
     REPO / "k3ssetup" / "k3s.env.example",
     *sorted(OVERLAY.glob("*.yaml"))],
    ids=lambda p: p.name,
)
def test_no_site_specific_value_is_tracked(path: Path):
    text = path.read_text(encoding="utf-8")
    hits = sorted(set(_PRIVATE.findall(text)))
    assert not hits, f"{path.name} still names a private site: {hits}"


def test_no_deploy_path_ships_a_default_admin_password():
    """The admin password is ADMIN_PASSWORD or generated; never a literal."""
    for path in (DEPLOY, REPO / "scripts" / "deploy-k8s.sh"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r'ADMIN_PASSWORD="\$\{ADMIN_PASSWORD:-[^}]', text), path.name
        assert re.search(r'ADMIN_PASSWORD="\$\(openssl rand ', text), (
            f"{path.name} must generate the admin password when none is given"
        )
    for path in (REPO / "scripts" / "createAdmin.py", REPO / "scripts" / "getUser.py"):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"\.get\('ADMIN_PASSWORD', ''\)", text), path.name
        assert not re.search(r"\.get\('ADMIN_PASSWORD', '[^']", text), path.name
        assert "secrets.token_urlsafe(" in text, path.name


def test_the_deploy_never_edits_the_tracked_overlay():
    text = DEPLOY.read_text(encoding="utf-8")
    assert "sed -i" not in "\n".join(
        line for line in text.splitlines() if "kustomization" in line
    ), "the deploy must render its own overlay, not rewrite the tracked one"
    assert 'kubectl apply -k "$RENDER_DIR"' in text
    tracked = (OVERLAY / "kustomization.yaml").read_text(encoding="utf-8")
    assert tracked.count("newTag: BUILD_TAG_PLACEHOLDER") == 3


def test_the_generic_overlay_keeps_ai_offline_by_default():
    """A hosted-LLM posture is a site decision (a site patch), never the default."""
    tracked = (OVERLAY / "kustomization.yaml").read_text(encoding="utf-8")
    for key in ("AI_OFFLINE_MODE", "LLM_PROVIDER", "LLM_MODEL", "CHAT_LLM_MODEL"):
        assert f"{key}:" not in tracked, key
    assert "host: testlookup.localhost" in (OVERLAY / "ingress-traefik.yaml").read_text(
        encoding="utf-8"
    )


# ── the site file loader ────────────────────────────────────────────────────

@needs_bash
def test_site_file_values_load_and_the_environment_wins(tmp_path: Path):
    site = tmp_path / "site.env"
    site.write_text(
        "# comment\n\n"
        "K3S_HOST=ci.example.test\n"
        "export K3S_REGISTRY='reg.example.test:30500'\n"
        'K3S_NAMESPACE="tl-ns"\n'
        "K3S_PATCHES_DIR=$(touch pwned)\n"
        "not a pair\n",
        encoding="utf-8", newline="\n",
    )
    body = (
        LIB.read_text(encoding="utf-8")
        + "\nload_site_config\n"
        + 'printf "%s|%s|%s|%s|%s\\n" "$K3S_HOST" "$K3S_REGISTRY" "$K3S_NAMESPACE" '
        + '"$K3S_PATCHES_DIR" "$SITE_CONFIG_FILE"\n'
    )
    result = _bash(tmp_path, body, {"TESTLOOKUP_K3S_ENV": str(site), "K3S_NAMESPACE": "from-env"})
    assert result.returncode == 0, result.stderr
    host, registry, namespace, patches, loaded = result.stdout.strip().split("|")
    assert (host, registry, namespace) == ("ci.example.test", "reg.example.test:30500", "from-env")
    assert patches == "$(touch pwned)", "the file must be read, never executed"
    assert not (tmp_path / "pwned").exists()
    assert loaded.endswith("site.env")


@needs_bash
def test_a_named_site_file_that_is_missing_stops_the_run(tmp_path: Path):
    body = LIB.read_text(encoding="utf-8") + "\nload_site_config\necho REACHED\n"
    result = _bash(tmp_path, body, {"TESTLOOKUP_K3S_ENV": str(tmp_path / "absent.env")})
    assert result.returncode != 0
    assert "REACHED" not in result.stdout


# ── installs from before the rename (homelabsetup/ -> k3ssetup/) keep working ──

@needs_bash
def test_the_old_variable_name_still_names_the_site_file(tmp_path: Path):
    site = tmp_path / "site.env"
    site.write_text("K3S_HOST=old.example.test\n", encoding="utf-8", newline="\n")
    body = LIB.read_text(encoding="utf-8") + '\nload_site_config\nprintf "%s\\n" "$K3S_HOST"\n'
    result = _bash(tmp_path, body, {"TESTLOOKUP_HOMELAB_ENV": str(site)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "old.example.test"


@needs_bash
@pytest.mark.parametrize(("files", "expected"), [
    ({"homelab.env": "legacy.example.test"}, "legacy.example.test"),
    ({"homelab.env": "legacy.example.test", "k3s.env": "new.example.test"}, "new.example.test"),
])
def test_the_old_default_site_file_is_read_when_the_new_one_is_absent(tmp_path: Path, files, expected):
    config = tmp_path / "home" / ".config" / "testlookup"
    config.mkdir(parents=True)
    for name, host in files.items():
        (config / name).write_text(f"K3S_HOST={host}\n", encoding="utf-8", newline="\n")
    body = LIB.read_text(encoding="utf-8") + '\nload_site_config\nprintf "%s\\n" "$K3S_HOST"\n'
    result = _bash(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def _credentials_file(tmp_path: Path, env: dict[str, str] | None = None) -> str:
    source = DEPLOY.read_text(encoding="utf-8")
    start = source.index("# ── Configuration ───")
    end = source.index('MINIO_ACCESS="${K3S_S3_ACCESS_KEY:-testlookup_minio}"')
    root = tmp_path / "repo"
    (root / "k3ssetup").mkdir(parents=True, exist_ok=True)
    shutil.copy(LIB, root / "k3ssetup" / LIB.name)
    block = source[start:end].replace(
        'REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"', f'REPO_ROOT="{_posix(root)}"'
    )
    if 'REPO_ROOT="' + _posix(root) not in block:
        block = f'REPO_ROOT="{_posix(root)}"\n' + block
    result = _bash(tmp_path, block + '\nprintf "%s\\n" "$CREDS_FILE"\n', env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@needs_bash
def test_an_install_from_before_the_rename_keeps_its_credentials_file(tmp_path: Path):
    """Writing a new file would record a fresh admin password the admin never got."""
    root = tmp_path / "repo"
    assert _credentials_file(tmp_path).endswith("k3ssetup/.k3s-credentials")
    legacy = root / "homelabsetup" / ".homelab-credentials"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("ADMIN_USERNAME=admin\n", encoding="utf-8")
    assert _credentials_file(tmp_path).endswith("homelabsetup/.homelab-credentials")
    current = root / "k3ssetup" / ".k3s-credentials"
    current.write_text("ADMIN_USERNAME=admin\n", encoding="utf-8")
    assert _credentials_file(tmp_path).endswith("k3ssetup/.k3s-credentials")
    named = _posix(tmp_path / "elsewhere.creds")
    assert _credentials_file(tmp_path, {"K3S_CREDENTIALS_FILE": named}) == named


@needs_bash
def test_the_old_node_password_variable_still_works(tmp_path: Path):
    body = (
        _function(BOOTSTRAP, "get_password")
        + '\nNODE_USER=ops; NODES_IP=(10.0.0.5)\nget_password 0\n'
    )
    result = _bash(tmp_path, body, {"HOMELAB_NODE1_PASS": "legacy-secret"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == "legacy-secret"
    result = _bash(tmp_path, body, {"HOMELAB_NODE1_PASS": "legacy-secret", "K3S_NODE1_PASS": "new-secret"})
    assert result.stdout == "new-secret"


@needs_bash
def test_no_site_file_means_the_public_defaults(tmp_path: Path):
    source = DEPLOY.read_text(encoding="utf-8")
    start = source.index("# ── Configuration ───")
    end = source.index('MINIO_ACCESS="${K3S_S3_ACCESS_KEY:-testlookup_minio}"')
    block = source[start:end].replace(
        'REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"', f'REPO_ROOT="{_posix(REPO)}"'
    )
    result = _bash(tmp_path, block + '\nprintf "%s|%s|%s\\n" "$K3S_HOST" "$REGISTRY" "$NAMESPACE"\n')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "testlookup.localhost|registry.local:30500|testlookup"


# ── an existing install keeps its host ──────────────────────────────────────

@needs_bash
@pytest.mark.parametrize(
    ("existing", "allow", "ok"),
    [("", "false", True), ("site.example.test", "false", True),
     ("old.example.test", "false", False), ("old.example.test", "true", True)],
)
def test_an_existing_install_keeps_its_host(tmp_path: Path, existing: str, allow: str, ok: bool):
    fake_kubectl = f'kubectl() {{ printf "%s" "{existing}"; }}\n'
    body = (
        'NAMESPACE=testlookup\n' + fake_kubectl + _function(DEPLOY, "check_existing_host")
        + '\ncheck_existing_host "site.example.test"\necho PASSED\n'
    )
    result = _bash(tmp_path, body, {"ALLOW_HOST_CHANGE": allow})
    assert ("PASSED" in result.stdout) is ok, result.stderr
    if not ok:
        assert "old.example.test" in result.stderr


def test_the_host_guard_runs_in_preflight_before_any_build():
    text = DEPLOY.read_text(encoding="utf-8")
    guard = text.index('check_existing_host "$K3S_HOST" ||')
    assert guard < text.index("Step 1 — Deploy Local Container Registry")
    assert guard < text.index("Step 2 — Build and Push Container Images")


# ── the rendered overlay ────────────────────────────────────────────────────

@needs_bash
def test_the_rendered_overlay_carries_the_site(tmp_path: Path):
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "10-ai.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: testlookup-config\n"
        "  namespace: testlookup\ndata:\n  LLM_PROVIDER: openrouter\n",
        encoding="utf-8",
    )
    render_dir = REPO / "k3ssetup" / ".render"
    body = (
        f'REPO_ROOT="{_posix(REPO)}"\nNAMESPACE=testlookup\n'
        'K3S_HOST=site.example.test\nREGISTRY=reg.example.test:30500\n'
        f'K3S_PATCHES_DIR="{_posix(patches)}"\n'
        'OVERLAY_IMAGE_PREFIX="registry.local:30500/testlookup"\n'
        + _function(DEPLOY, "render_overlay")
        + f'\nrender_overlay "{_posix(render_dir)}" build-20260101-000000\n'
    )
    try:
        result = _bash(tmp_path, body)
        assert result.returncode == 0, result.stderr
        kustomization = (render_dir / "kustomization.yaml").read_text(encoding="utf-8")
        assert "  - ../../k8s/overlays/k3s" in kustomization
        for image in ("backend", "frontend", "mcp"):
            assert f"newName: reg.example.test:30500/testlookup/{image}" in kustomization
        assert kustomization.count("newTag: build-20260101-000000") == 3
        assert "path: site-patch-10-ai.yaml" in kustomization
        assert "value: site.example.test" in (render_dir / "site-ingress-host.yaml").read_text(
            encoding="utf-8"
        )
        host_config = (render_dir / "site-config-host.yaml").read_text(encoding="utf-8")
        assert 'PUBLIC_BASE_URL: "http://site.example.test"' in host_config
        if shutil.which("kubectl"):
            rendered = subprocess.run(
                ["kubectl", "kustomize", str(render_dir)],
                capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            if rendered.returncode == 0:
                out = rendered.stdout
                assert "BUILD_TAG_PLACEHOLDER" not in out
                assert "image: reg.example.test:30500/testlookup/backend:build-20260101-000000" in out
                assert "host: site.example.test" in out
                assert "LLM_PROVIDER: openrouter" in out
    finally:
        shutil.rmtree(render_dir, ignore_errors=True)


# ── bootstrap needs a layout from the site file ─────────────────────────────

@needs_bash
def test_bootstrap_parses_nodes_and_refuses_an_empty_layout(tmp_path: Path):
    body = (
        _function(BOOTSTRAP, "parse_nodes")
        + '\nparse_nodes "srv=10.0.0.11 agent1=10.0.0.12"\n'
        + 'printf "%s %s %s\\n" "${#NODES_IP[@]}" "${NODES_NAME[0]}" "${NODES_IP[1]}"\n'
        + 'parse_nodes "" && echo EMPTY_ACCEPTED || echo EMPTY_REFUSED\n'
        + 'parse_nodes "10.0.0.13" && echo BARE_ACCEPTED || echo BARE_REFUSED\n'
    )
    result = _bash(tmp_path, body)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.split()
    assert lines[:3] == ["2", "srv", "10.0.0.12"]
    assert "EMPTY_REFUSED" in lines and "BARE_REFUSED" in lines


# ── createAdmin.py: no default password, never touches an existing admin ─────

def _load_create_admin():
    import sys

    spec = importlib.util.spec_from_file_location(
        "create_admin_under_test", REPO / "scripts" / "createAdmin.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO / "backend"))
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_create_admin_generates_a_password_when_none_is_given():
    module = _load_create_admin()
    first, generated, must_change = module.resolve_password({})
    second, _, _ = module.resolve_password({"ADMIN_PASSWORD": ""})
    assert generated and must_change
    assert len(first) >= 20 and first != second, "a fresh random password every time"


def test_create_admin_uses_a_supplied_password_and_never_prints_it():
    module = _load_create_admin()
    assert module.resolve_password({"ADMIN_PASSWORD": "chosen-by-operator"}) == (
        "chosen-by-operator", False, False,
    )
    assert module.resolve_password(
        {"ADMIN_PASSWORD": "generated-by-caller", "ADMIN_PASSWORD_GENERATED": "true"}
    ) == ("generated-by-caller", False, True)
    source = (REPO / "scripts" / "createAdmin.py").read_text(encoding="utf-8")
    printed = source[source.index("print('admin user created')"):]
    assert "if generated_here:" in printed, "only a password generated here may be printed"


def test_create_admin_leaves_an_existing_admin_alone():
    source = (REPO / "scripts" / "createAdmin.py").read_text(encoding="utf-8")
    existing = source[source.index("if existing is not None:"):]
    existing = existing[: existing.index("return") + len("return")]
    assert "hashed_password" not in existing and "commit" not in existing
