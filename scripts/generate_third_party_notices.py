#!/usr/bin/env python3
"""Generate THIRD_PARTY_NOTICES.md from the lockfiles and the license allowlist.

    python scripts/generate_third_party_notices.py          # rewrite the file
    python scripts/generate_third_party_notices.py --check  # exit 1 if stale

Inputs (all tracked):
  * frontend/package-lock.json, client/js/package-lock.json — every non-dev
    npm package with its declared license (what the web UI and the JS reporter
    ship);
  * scripts/license-allowlist.json — the SPDX license of every Python
    distribution the backend, MCP server, CLI and Python reporter declare;
  * deploy/images.manifest.txt — the container images the deployments run,
    with the licenses listed in IMAGE_LICENSES below.

Run it when dependencies or images change, and before every release (see
RELEASING.md). The quality gate's licenses.shipped-allowlist guard keeps the
inputs honest; this script only renders them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "THIRD_PARTY_NOTICES.md"
ALLOWLIST = ROOT / "scripts" / "license-allowlist.json"
IMAGES = ROOT / "deploy" / "images.manifest.txt"

# License and source of every third-party image name in deploy/images.manifest.txt.
# Matched on the image name without its tag; an image missing here is listed
# as "see upstream" and should be added.
IMAGE_LICENSES: dict[str, tuple[str, str]] = {
    "postgres": ("PostgreSQL License", "https://www.postgresql.org/about/licence/"),
    "valkey/valkey": ("BSD-3-Clause", "https://github.com/valkey-io/valkey"),
    "mongo": ("SSPL-1.0 (MongoDB Community Server)", "https://www.mongodb.com/legal/licensing/community-edition"),
    "ghcr.io/versity/versitygw": ("Apache-2.0", "https://github.com/versity/versitygw"),
    "busybox": ("GPL-2.0-only", "https://busybox.net/"),
    "ollama/ollama": ("MIT; the image also contains NVIDIA CUDA runtime libraries under the NVIDIA CUDA EULA", "https://github.com/ollama/ollama"),
    "chromadb/chroma": ("Apache-2.0", "https://github.com/chroma-core/chroma"),
    "mher/flower": ("BSD-3-Clause", "https://github.com/mher/flower"),
}

HEADER = """# Third-party notices

TestLookup is licensed under the Apache License 2.0 ([LICENSE](LICENSE)). It
uses and redistributes software written by others. This file lists that
software and its licenses; the notices that Apache-2.0 components require are
reproduced in [NOTICE](NOTICE).

**Generated** by `scripts/generate_third_party_notices.py` from the lockfiles,
`scripts/license-allowlist.json` and `deploy/images.manifest.txt`. Do not edit
the generated sections by hand. The quality gate refuses shipped dependencies
under GPL, AGPL, LGPL, SSPL, BUSL or non-commercial licenses
(`licenses.shipped-allowlist`).

The full text of each license named below is available from the
[SPDX license list](https://spdx.org/licenses/) and in each package's own
distribution (the backend, worker and MCP images keep every package's
license files under `site-packages/*.dist-info`).
"""

STATIC_TAIL = """
## Container base images

The TestLookup images are built on `python:3.11-slim` (Debian; backend,
workers, MCP server) and `nginx:alpine` (Alpine Linux; web UI), with a
`node:20-alpine` stage used only to build the web UI. These base layers contain
operating-system packages under their own licenses, including GPL and LGPL
packages (for example bash, coreutils, glibc and BusyBox) that are distributed
as separate programs. Their corresponding source is available from
<https://sources.debian.org> and <https://gitlab.alpinelinux.org/alpine/aports>
for the exact package versions listed in the SBOM attached to each release
(see RELEASING.md).

## Services you run alongside TestLookup

The deployment assets start the third-party services below as separate
programs; TestLookup talks to them over the network and does not link them.
They are listed for your own license review.

{images}

**MongoDB** is licensed under the Server Side Public License (SSPL-1.0), which
is not an OSI-approved license. TestLookup only connects to it with the
Apache-2.0 `pymongo`/`motor` drivers. The Compose files and Kubernetes
manifests reference the upstream `mongo` image, which you pull from MongoDB's
own distribution; the air-gapped offline bundle does **not** include it, so an
air-gapped operator mirrors it separately and accepts its license directly.

**BusyBox** (GPL-2.0) runs only as a Kubernetes init container that waits for
dependencies; its source is available from <https://busybox.net/downloads/>.

The optional monitoring add-on (`docker-compose.monitoring.yml`) uses
Prometheus (Apache-2.0), Grafana (AGPL-3.0) and Jaeger (Apache-2.0), pulled
from their upstream registries and never bundled; running Grafana unmodified
does not require you to publish anything.

## Models

TestLookup does not distribute model weights. With the local LLM add-on,
Ollama downloads the models you configure. The defaults are:

| Model | License | Commercial use |
|---|---|---|
| `qwen2.5:7b-instruct` (default chat/analysis model) | Apache-2.0 | Yes |
| `qwen2.5:14b-instruct-q5_K_M` (larger pinned model, `make pull-llm`) | Apache-2.0 | Yes |
| `nomic-embed-text:v1.5` (default embeddings) | Apache-2.0 | Yes |

If you choose another model, check its license: some popular models (for
example Qwen2.5 3B, Llama, DeepSeek) carry use or attribution restrictions.
Hosted providers (OpenAI, Gemini, Anthropic, OpenRouter) are governed by your
agreement with them.

## Trademarks

Jira, Slack, Microsoft Teams, GitHub, GitLab, Splunk, OpenShift and other
product names are trademarks of their owners and are used only to identify the
integrations. TestLookup is not affiliated with or endorsed by them.
"""


def _npm_section(lockfile: Path, title: str, overrides: dict[str, str]) -> str:
    if not lockfile.exists():
        return ""
    packages = json.loads(lockfile.read_text(encoding="utf-8")).get("packages", {})
    by_license: dict[str, list[str]] = defaultdict(list)
    for key, meta in packages.items():
        if not key or meta.get("dev") or meta.get("link"):
            continue
        name = key.rsplit("node_modules/", 1)[-1]
        license_expr = meta.get("license") or overrides.get(name, "UNKNOWN")
        if isinstance(license_expr, dict):
            license_expr = license_expr.get("type", "UNKNOWN")
        by_license[str(license_expr)].append(f"{name}@{meta.get('version', '?')}")
    if not by_license:
        return f"## {title}\n\nNo runtime dependencies.\n"
    total = sum(len(v) for v in by_license.values())
    lines = [f"## {title}", "", f"{total} packages, grouped by license.", ""]
    for license_expr in sorted(by_license, key=lambda k: (-len(by_license[k]), k)):
        names = sorted(set(by_license[license_expr]))
        lines.append(f"<details><summary><strong>{license_expr}</strong> ({len(names)})</summary>")
        lines.append("")
        lines.append(", ".join(f"`{n}`" for n in names))
        lines.append("")
        lines.append("</details>")
        lines.append("")
    return "\n".join(lines)


def _python_section(config: dict) -> str:
    licenses = {re.sub(r"[-_.]+", "-", k).lower(): v for k, v in config.get("python", {}).items()}
    used: dict[str, set[str]] = defaultdict(set)
    for rel in config.get("python_manifests", []):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if rel.endswith(".toml"):
            import tomllib

            project = tomllib.loads(text).get("project", {})
            deps = list(project.get("dependencies", []))
            for extra in project.get("optional-dependencies", {}).values():
                deps.extend(extra)
        else:
            deps = [line.split("#", 1)[0].strip() for line in text.splitlines()]
            deps = [d for d in deps if d and not d.startswith("-")]
        component = rel.split("/", 1)[0]
        for dep in deps:
            match = re.match(r"[A-Za-z0-9_.\-]+", dep)
            if match:
                used[re.sub(r"[-_.]+", "-", match.group(0)).lower()].add(component)
    lines = [
        "## Python dependencies (backend, workers, MCP server, CLI, Python reporter)",
        "",
        "Direct dependencies as declared; their transitive dependencies are under the",
        "same family of permissive licenses (MIT, BSD, Apache-2.0, PSF, ISC, MPL-2.0).",
        "",
        "| Package | License | Used by |",
        "|---|---|---|",
    ]
    for name in sorted(used):
        lines.append(f"| `{name}` | {licenses.get(name, 'not listed')} | {', '.join(sorted(used[name]))} |")
    lines.append("")
    return "\n".join(lines)


def _images_table() -> str:
    rows = ["| Image | License | Source |", "|---|---|---|"]
    seen: set[str] = set()
    for raw in IMAGES.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        image = line.split()[0]
        if image.startswith("testlookup/") or image in seen:
            continue
        seen.add(image)
        name = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
        license_name, source = IMAGE_LICENSES.get(name, ("see upstream", ""))
        rows.append(f"| `{image}` | {license_name} | {source} |")
    return "\n".join(rows)


def render() -> str:
    config = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    overrides = config.get("npm_overrides", {})
    parts = [
        HEADER,
        _npm_section(ROOT / "frontend" / "package-lock.json",
                     "Web UI (bundled into the frontend image)", overrides),
        _npm_section(ROOT / "client" / "js" / "package-lock.json",
                     "JavaScript reporter", overrides),
        "## Java reporter\n\nThe `-all` jar bundles and relocates Jackson core, databind and\n"
        "dataformat-yaml (Apache-2.0) and SnakeYAML (Apache-2.0); their NOTICE is in\n"
        "[NOTICE](NOTICE). JUnit 5 (EPL-2.0) and TestNG (Apache-2.0) are `provided`\n"
        "dependencies supplied by your test project, not bundled.\n\n"
        "## Go reporter\n\nStandard library only.\n",
        _python_section(config),
        STATIC_TAIL.format(images=_images_table()),
    ]
    return "\n".join(p.rstrip("\n") + "\n" for p in parts if p)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    args = parser.parse_args()
    text = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("THIRD_PARTY_NOTICES.md is stale: run scripts/generate_third_party_notices.py")
            return 1
        return 0
    OUT.write_bytes(text.encode("utf-8"))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
