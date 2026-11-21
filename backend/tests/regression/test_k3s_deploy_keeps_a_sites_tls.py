"""A K3s site that serves TLS keeps it across deploys, and the app says https.

Security review 2026-11-20, TL-03. ``k3ssetup/deploy-k3s.sh`` wrote
``http://<host>`` as the application's public URL and CORS origin whatever the
site served, and every deploy deleted the Ingress ``testlookup-traefik-redirect``,
the Middleware ``redirect-https`` and the Secret ``testlookup-tls-cert``. A site
that had added a certificate and an HTTP-to-HTTPS redirect under those names
was put back on plain HTTP by its next deploy, with the application still
handing out ``http://`` links.

The rule:

* ``K3S_PUBLIC_SCHEME`` is ``http`` (default, what the bundled ingress serves)
  or ``https``; anything else stops the deploy;
* every URL the script writes or prints for the site uses that scheme;
* with ``https`` nothing of the site's TLS setup is deleted;
* a TLS Secret (a private key) is never deleted by a deploy.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from tests.regression.test_k3s_build_authority import BASH, _run_script

pytestmark = pytest.mark.regression

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "k3ssetup" / "deploy-k3s.sh"
EXAMPLE = REPO / "k3ssetup" / "k3s.env.example"


def _code() -> str:
    lines = DEPLOY.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_no_site_url_is_written_with_a_fixed_scheme():
    fixed = re.findall(r"https?://\$\{?K3S_HOST\}?", _code())
    assert not fixed, f"the script still writes a fixed scheme for the site: {fixed}"
    code = _code()
    assert 'CORS_ORIGINS: "${K3S_PUBLIC_SCHEME:-http}://${K3S_HOST}"' in code
    assert 'PUBLIC_BASE_URL: "${K3S_PUBLIC_SCHEME:-http}://${K3S_HOST}"' in code


def test_a_deploy_never_deletes_a_tls_secret():
    deletions = re.findall(r"delete secret[^\n]*tls[^\n]*", _code(), re.IGNORECASE)
    assert not deletions, deletions


def _prune_block() -> str:
    code = _code()
    start = code.index('if [ "$K3S_PUBLIC_SCHEME" = "https" ]; then\n  log "K3S_PUBLIC_SCHEME=https: leaving')
    return code[start: code.index("\nfi", start) + 3]


@pytest.mark.skipif(BASH is None, reason="requires POSIX bash")
@pytest.mark.parametrize(("scheme", "deleted"), [
    ("https", []),
    ("http", ["ingress testlookup-traefik-redirect", "middleware redirect-https"]),
])
def test_the_redirect_cleanup_runs_only_for_a_plain_http_site(tmp_path: Path, scheme: str, deleted: list[str]):
    body = textwrap.dedent(
        f"""
        NAMESPACE=testlookup
        K3S_PUBLIC_SCHEME={scheme}
        log() {{ :; }}
        kubectl() {{ shift 2; [ "$1" = delete ] && echo "$2 $3" >> deleted.log; return 0; }}
        """
    ) + _prune_block() + "\n"
    result = _run_script(tmp_path, body)

    assert result.returncode == 0, result.stderr
    log = tmp_path / "deleted.log"
    assert (log.read_text().splitlines() if log.exists() else []) == deleted


@pytest.mark.skipif(BASH is None, reason="requires POSIX bash")
@pytest.mark.parametrize(("value", "accepted"), [
    ("", True), ("http", True), ("https", True), ("HTTPS", False), ("ftp", False), ("https://x", False),
])
def test_the_scheme_is_http_or_https_and_nothing_else(tmp_path: Path, value: str, accepted: bool):
    source = DEPLOY.read_text(encoding="utf-8")
    start = source.index('K3S_PUBLIC_SCHEME="${K3S_PUBLIC_SCHEME:-http}"')
    block = source[start: source.index("esac", start) + 4]
    result = _run_script(tmp_path, f"K3S_PUBLIC_SCHEME='{value}'\n{block}\necho \"SCHEME=$K3S_PUBLIC_SCHEME\"\n")

    assert (result.returncode == 0) is accepted, result.stderr
    if accepted:
        assert result.stdout.strip() == f"SCHEME={value or 'http'}"
    else:
        assert "K3S_PUBLIC_SCHEME must be http or https" in result.stderr


def test_the_example_site_file_documents_the_setting():
    assert "#K3S_PUBLIC_SCHEME=https" in EXAMPLE.read_text(encoding="utf-8")
