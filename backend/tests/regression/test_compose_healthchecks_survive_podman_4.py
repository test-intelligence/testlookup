"""Compose healthchecks must run as written under Podman 4's Docker API.

Podman 4 (Ubuntu 24.04's ``podman``, and the engine of the release harness in
``release-e2e.yml``) receives a compose healthcheck through its Docker-compatible
API, joins the ``test`` array with spaces, and splits an exec-form ``CMD`` check
again at every space. An argument that holds a space is torn apart: the MCP
check ``["CMD", "python", "-c", "import sys, urllib.request; ..."]`` ran
``python -c import``, the container never became healthy, and the v0.1.0
release run was blocked at ``compose up --wait`` (2026-11-14). ``CMD-SHELL``
is split once, after the keyword, and the rest reaches ``/bin/sh -c`` whole.

So an exec-form check may not put a space inside one argument. Use
``CMD-SHELL``, or, where a shell would defeat the check (``pgrep -f`` matches
its own ``sh -c`` line), keep ``CMD`` and write the space as ``[[:space:]]``.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = sorted(ROOT.glob("docker-compose*.yml"))


class _TagTolerantLoader(yaml.SafeLoader):
    """Reads the compose overrides that use ``!reset`` / ``!override``."""


def _construct_tagged(loader, _suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_TagTolerantLoader.add_multi_constructor("!", _construct_tagged)


def podman4_argv(test: list[str]) -> list[str]:
    """The command Podman 4 runs for a compose healthcheck ``test``.

    Its compat API joins the array with spaces. A ``CMD`` check is then split
    with ``strings.Fields`` (every run of whitespace); a ``CMD-SHELL`` check
    is split once, after the keyword, and run by ``/bin/sh -c``.
    """
    joined = " ".join(test)
    keyword, _, rest = joined.partition(" ")
    if keyword.upper() == "CMD":
        return joined.split()[1:]
    if keyword.upper() == "CMD-SHELL":
        return ["/bin/sh", "-c", rest]
    raise AssertionError(f"unexpected healthcheck keyword {keyword!r} in {test!r}")


def _services(path: Path) -> dict:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_TagTolerantLoader) or {}
    return data.get("services") or {}


def _healthchecks():
    for path in COMPOSE_FILES:
        for name, service in _services(path).items():
            test = ((service or {}).get("healthcheck") or {}).get("test")
            # A plain string is CMD-SHELL to Compose; ["NONE"] disables the check.
            if isinstance(test, list) and test and test[0].upper() != "NONE":
                yield path.name, name, test


HEALTHCHECKS = list(_healthchecks())


def test_the_model_reproduces_the_release_run_failure():
    probe = (
        "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen("
        + "'http://127.0.0.1:8002/health/live', timeout=5).status == 200 else 1)"
    )
    old_mcp_check = ["CMD", "python", "-c", probe]
    assert podman4_argv(old_mcp_check)[:3] == ["python", "-c", "import"]
    shell_check = ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U user -d db"]
    assert podman4_argv(shell_check) == ["/bin/sh", "-c", "pg_isready -h 127.0.0.1 -U user -d db"]


def test_the_release_compose_healthchecks_are_all_checked():
    checked = {(compose, service) for compose, service, _ in HEALTHCHECKS}
    for service in ("backend", "frontend", "mcp", "beat", "chromadb"):
        assert ("docker-compose.release.yml", service) in checked, service


@pytest.mark.parametrize(
    "compose,service,test",
    HEALTHCHECKS,
    ids=[f"{compose}:{service}" for compose, service, _ in HEALTHCHECKS],
)
def test_podman_4_runs_the_healthcheck_as_written(compose, service, test):
    keyword = test[0].upper()
    if keyword == "CMD":
        assert podman4_argv(test) == test[1:], (
            f"{compose} {service}: an exec-form healthcheck argument holds a space, and "
            f"Podman 4 would run {podman4_argv(test)!r}. Use CMD-SHELL, or write the "
            "space as [[:space:]] where a shell would defeat the check."
        )
    else:
        assert keyword == "CMD-SHELL", f"{compose} {service}: {test!r}"
        script = podman4_argv(test)[2]
        shlex.split(script)  # the line /bin/sh receives must at least tokenize


def test_the_beat_pattern_still_matches_beat_and_not_a_worker():
    beat = _services(ROOT / "docker-compose.release.yml")["beat"]
    test = beat["healthcheck"]["test"]
    assert test[:3] == ["CMD", "pgrep", "-f"], "exec form: under CMD-SHELL pgrep matches its own sh -c line"
    # pgrep reads the pattern as a POSIX extended regex.
    pattern = re.compile(test[3].replace("[[:space:]]", r"\s"))
    assert pattern.search(beat["command"]), (test[3], beat["command"])
    worker = _services(ROOT / "docker-compose.release.yml")["worker"]["command"]
    assert not pattern.search(worker if isinstance(worker, str) else " ".join(worker))
