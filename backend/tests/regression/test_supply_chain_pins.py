"""Pins that keep known-vulnerable or floating dependencies out of the images.

Found by the 2026-11-12 open-source repository review (PR-2, supply chain):

* starlette 0.52.1 (held below 1.0 by prometheus-fastapi-instrumentator 7.0.0)
  carried CVE-2026-48710 (the Host header rewrote ``request.url.path``),
  CVE-2026-54283 (urlencoded form limits ignored), CVE-2026-48818,
  CVE-2026-48817 and CVE-2026-54282; 1.3.1 fixes all five;
* the backend installed 75 direct pins and let ~140 transitive packages float
  at build time, invisible to Dependabot; the MCP image installed open ranges
  with no constraints at all. Both now install a hashed lock;
* mcp allowed PyJWT 2.13.0 and the CLI allowed cryptography 43-48 and
  pydantic 2.0-2.3, each with published advisories;
* the monitoring add-on ran Grafana 11.3.0 (three high advisories) and
  installed a plugin unpinned on every start;
* the frontend's nginx stage was the floating ``nginx:alpine``, and no base
  image was pinned by digest;
* ``make build`` tagged ``testlookup/<image>:latest``, a Docker Hub short name;
* the frontend lockfile resolved zod 4.4.3 (GHSA-mrhx-c7xc-m3w5, fixed 4.6.6).
"""
from __future__ import annotations

import json
import re
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
#: First starlette with all five advisories above fixed (CVE-2026-54283).
STARLETTE_FLOOR = (1, 3, 1)
#: prometheus-fastapi-instrumentator 8.0.1 is the first that accepts starlette 1.x.
INSTRUMENTATOR_FLOOR = (8, 0, 1)
#: First Grafana 13 release with GHSA-3w66-95m3-8jxg, GHSA-9g84-39mm-q4p3 and
#: GHSA-f74p-cwhp-x2wx fixed.
GRAFANA_FLOOR = (13, 0, 2)
_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\]+)", re.M)
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def _name(raw: str) -> str:
    return re.sub(r"[-_.]+", "-", raw).lower()


def _pins(relative: str) -> dict[str, str]:
    text = (ROOT / relative).read_text(encoding="utf-8")
    return {_name(name): pinned for name, pinned in _PIN.findall(text)}


def test_starlette_is_pinned_past_the_known_advisories():
    direct = _pins("backend/requirements.txt")
    locked = _pins("backend/requirements-lock.txt")
    for where, pins in (("requirements.txt", direct), ("requirements-lock.txt", locked)):
        assert "starlette" in pins, f"backend/{where} does not pin starlette"
        assert _version(pins["starlette"]) >= STARLETTE_FLOOR, (
            f"backend/{where}: starlette {pins['starlette']} predates 1.3.1 (CVE-2026-54283, "
            "CVE-2026-48710 and three more)"
        )
    assert _version(direct["prometheus-fastapi-instrumentator"]) >= INSTRUMENTATOR_FLOOR, (
        "prometheus-fastapi-instrumentator below 8.0.1 caps starlette below 1.0"
    )


def test_the_starlette_this_suite_runs_on_is_past_the_floor():
    """CI installs requirements-lock.txt, so this is the starlette the image ships."""
    assert _version(installed_version("starlette")) >= STARLETTE_FLOOR


@pytest.mark.parametrize("component", ["backend", "mcp"])
def test_the_lock_pins_every_requirement_with_hashes(component):
    """``make lock-python`` writes the lock from requirements.txt. A bump to
    requirements.txt that skips the re-lock would otherwise reach CI as a
    ``--require-hashes`` install failure, or worse, go unnoticed."""
    lock_text = (ROOT / component / "requirements-lock.txt").read_text(encoding="utf-8")
    assert "make lock-python" in lock_text.splitlines()[1], "regenerate it with `make lock-python`"
    locked = _pins(f"{component}/requirements-lock.txt")
    assert len(locked) >= (150 if component == "backend" else 25), len(locked)
    stale = {
        name: (pinned, locked.get(name))
        for name, pinned in _pins(f"{component}/requirements.txt").items()
        if locked.get(name) != pinned
    }
    assert not stale, f"{component}/requirements-lock.txt is stale against requirements.txt: {stale}"
    for name, req in _requirement_names(f"{component}/requirements.txt").items():
        assert name in locked, f"{component}: {req} is not in requirements-lock.txt"
    entries = re.split(r"\n(?=[A-Za-z0-9])", lock_text.split("\n", 2)[2])
    unhashed = [e.split()[0] for e in entries if e.strip() and "--hash=sha256:" not in e]
    assert not unhashed, f"lock entries without a hash: {unhashed}"


def _requirement_names(relative: str) -> dict[str, str]:
    names = {}
    for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        match = re.match(r"[A-Za-z0-9_.\-]+", line)
        if line and not line.startswith("-") and match:
            names[_name(match.group(0))] = line
    return names


@pytest.mark.parametrize("dockerfile", ["backend/Dockerfile", "mcp/Dockerfile"])
def test_the_python_images_install_the_hashed_lock(dockerfile):
    text = (ROOT / dockerfile).read_text(encoding="utf-8")
    installs = re.findall(r"pip install [^\n&]*-r \S+", text)
    assert any("--require-hashes -r requirements-lock.txt" in i for i in installs), installs
    unlocked = [i for i in installs if re.search(r"-r requirements\.txt\b", i)]
    assert not unlocked, f"{dockerfile} installs the unlocked requirements.txt: {unlocked}"


def test_the_mcp_and_cli_floors_exclude_advised_releases():
    mcp = (ROOT / "mcp/requirements.txt").read_text(encoding="utf-8")
    floor = re.search(r"^PyJWT>=([\d.]+)", mcp, re.M)
    assert floor and _version(floor.group(1)) >= (2, 14, 0), "PyJWT 2.13.0 has two high advisories"
    cli = (ROOT / "cli/pyproject.toml").read_text(encoding="utf-8")
    for package, minimum in (("cryptography", (49,)), ("pydantic", (2, 4))):
        found = re.search(rf'"{package}>=([\d.]+)"', cli)
        assert found and _version(found.group(1)) >= minimum, f"cli: raise the {package} floor"


def _monitoring_service(name: str) -> dict:
    doc = yaml.safe_load((ROOT / "docker-compose.monitoring.yml").read_text(encoding="utf-8"))
    return doc["services"][name]


def test_grafana_is_pinned_by_version_and_digest_past_the_advisories():
    image = _monitoring_service("grafana")["image"]
    match = re.fullmatch(r"grafana/grafana:(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}", image)
    assert match, f"pin Grafana as grafana/grafana:<x.y.z>@sha256:<digest>, not {image!r}"
    assert _version(match.group(1)) >= GRAFANA_FLOOR, f"Grafana {match.group(1)} predates 13.0.2"


def test_grafana_downloads_no_plugins_at_start():
    env = _monitoring_service("grafana")["environment"]
    assert "GF_INSTALL_PLUGINS" not in env, "an unpinned plugin install on every start"
    assert str(env.get("GF_PLUGINS_PREINSTALL_DISABLED")).lower() == "true", (
        "Grafana 13 fetches its preinstalled plugins at their newest version unless this is set"
    )
    # Its replacement for the old plugin line: the bundled Jaeger data source.
    datasources = (ROOT / "infra/monitoring/grafana/provisioning/datasources").glob("*.y*ml")
    types = {d["type"] for f in datasources for d in yaml.safe_load(f.read_text(encoding="utf-8"))["datasources"]}
    assert {"prometheus", "jaeger"} <= types


@pytest.mark.parametrize("dockerfile", ["backend/Dockerfile", "mcp/Dockerfile", "frontend/Dockerfile"])
def test_every_external_base_image_is_pinned_by_digest(dockerfile):
    froms = re.findall(r"^FROM\s+(\S+)", (ROOT / dockerfile).read_text(encoding="utf-8"), re.M)
    stages = set(re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", (ROOT / dockerfile).read_text(encoding="utf-8"), re.M))
    external = [image for image in froms if image not in stages]
    assert external, dockerfile
    floating = [image for image in external if not _DIGEST.search(image)]
    assert not floating, f"{dockerfile}: pin by digest (Dependabot moves it): {floating}"


def test_the_nginx_stage_names_a_version():
    froms = re.findall(r"^FROM\s+(nginx\S*)", (ROOT / "frontend/Dockerfile").read_text(encoding="utf-8"), re.M)
    assert froms and all(re.match(r"nginx:\d+\.\d+\.\d+-alpine", f) for f in froms), froms


def test_make_build_tags_the_published_image_names():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    build = makefile.split("\nbuild:", 1)[1].split("\n\n", 1)[0]
    tags = re.findall(r"docker build -t (\S+)", build)
    assert len(tags) == 3, tags
    assert all(t.startswith("$(LOCAL_IMAGE_PREFIX)/") for t in tags), tags
    assert "LOCAL_IMAGE_PREFIX ?= ghcr.io/test-intelligence/testlookup" in makefile
    assert not re.search(r"-t testlookup/\w+:latest", makefile), "a Docker Hub short name"


def test_the_frontend_lock_resolves_a_fixed_zod():
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    zods = {k: v["version"] for k, v in lock["packages"].items() if k.endswith("node_modules/zod")}
    assert zods, "zod left the lockfile: drop this test"
    old = {k: v for k, v in zods.items() if _version(v) < (4, 6, 6)}
    assert not old, f"zod below 4.6.6 (GHSA-mrhx-c7xc-m3w5): {old}"
