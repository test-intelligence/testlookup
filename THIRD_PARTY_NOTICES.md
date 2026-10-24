# Third-party notices

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

## Web UI (bundled into the frontend image)

370 packages, grouped by license.

<details><summary><strong>MIT</strong> (289)</summary>

`@antfu/install-pkg@1.1.0`, `@braintree/sanitize-url@7.1.2`, `@floating-ui/core@1.8.0`, `@floating-ui/dom@1.8.0`, `@floating-ui/react-dom@2.1.9`, `@floating-ui/utils@0.2.12`, `@iconify/types@2.0.0`, `@iconify/utils@3.1.4`, `@mermaid-js/parser@1.2.1`, `@radix-ui/number@1.1.3`, `@radix-ui/primitive@1.1.4`, `@radix-ui/primitive@1.1.7`, `@radix-ui/react-arrow@1.1.15`, `@radix-ui/react-collection@1.1.15`, `@radix-ui/react-compose-refs@1.1.3`, `@radix-ui/react-compose-refs@1.1.5`, `@radix-ui/react-context@1.1.4`, `@radix-ui/react-context@1.2.2`, `@radix-ui/react-dialog@1.1.17`, `@radix-ui/react-direction@1.1.4`, `@radix-ui/react-dismissable-layer@1.1.13`, `@radix-ui/react-dismissable-layer@1.1.19`, `@radix-ui/react-dropdown-menu@2.1.24`, `@radix-ui/react-focus-guards@1.1.4`, `@radix-ui/react-focus-guards@1.1.6`, `@radix-ui/react-focus-scope@1.1.10`, `@radix-ui/react-focus-scope@1.1.16`, `@radix-ui/react-id@1.1.2`, `@radix-ui/react-id@1.1.4`, `@radix-ui/react-menu@2.1.24`, `@radix-ui/react-popper@1.3.7`, `@radix-ui/react-portal@1.1.12`, `@radix-ui/react-portal@1.1.17`, `@radix-ui/react-presence@1.1.10`, `@radix-ui/react-presence@1.1.6`, `@radix-ui/react-primitive@2.1.10`, `@radix-ui/react-primitive@2.1.6`, `@radix-ui/react-roving-focus@1.1.19`, `@radix-ui/react-select@2.3.7`, `@radix-ui/react-slot@1.3.0`, `@radix-ui/react-slot@1.3.3`, `@radix-ui/react-tabs@1.1.21`, `@radix-ui/react-tooltip@1.2.16`, `@radix-ui/react-use-callback-ref@1.1.2`, `@radix-ui/react-use-callback-ref@1.1.4`, `@radix-ui/react-use-controllable-state@1.2.3`, `@radix-ui/react-use-controllable-state@1.2.6`, `@radix-ui/react-use-effect-event@0.0.3`, `@radix-ui/react-use-effect-event@0.0.5`, `@radix-ui/react-use-escape-keydown@1.1.2`, `@radix-ui/react-use-is-hydrated@0.1.3`, `@radix-ui/react-use-layout-effect@1.1.2`, `@radix-ui/react-use-layout-effect@1.1.4`, `@radix-ui/react-use-previous@1.1.4`, `@radix-ui/react-use-rect@1.1.4`, `@radix-ui/react-use-size@1.1.4`, `@radix-ui/react-visually-hidden@1.2.11`, `@radix-ui/rect@1.1.3`, `@reduxjs/toolkit@2.12.0`, `@standard-schema/spec@1.1.0`, `@standard-schema/utils@0.3.0`, `@types/d3-array@3.2.2`, `@types/d3-axis@3.0.6`, `@types/d3-brush@3.0.6`, `@types/d3-chord@3.0.6`, `@types/d3-color@3.1.3`, `@types/d3-contour@3.0.6`, `@types/d3-delaunay@6.0.4`, `@types/d3-dispatch@3.0.7`, `@types/d3-drag@3.0.7`, `@types/d3-dsv@3.0.7`, `@types/d3-ease@3.0.2`, `@types/d3-fetch@3.0.7`, `@types/d3-force@3.0.10`, `@types/d3-format@3.0.4`, `@types/d3-geo@3.1.0`, `@types/d3-hierarchy@3.1.7`, `@types/d3-interpolate@3.0.4`, `@types/d3-path@3.1.1`, `@types/d3-polygon@3.0.2`, `@types/d3-quadtree@3.0.6`, `@types/d3-random@3.0.3`, `@types/d3-scale-chromatic@3.1.0`, `@types/d3-scale@4.0.9`, `@types/d3-selection@3.0.11`, `@types/d3-shape@3.1.8`, `@types/d3-time-format@4.0.3`, `@types/d3-time@3.0.4`, `@types/d3-timer@3.0.2`, `@types/d3-transition@3.0.9`, `@types/d3-zoom@3.0.8`, `@types/d3@7.4.3`, `@types/debug@4.1.13`, `@types/estree-jsx@1.0.5`, `@types/estree@1.0.8`, `@types/geojson@7946.0.16`, `@types/hast@3.0.4`, `@types/mdast@4.0.4`, `@types/ms@2.1.0`, `@types/react-dom@19.2.5`, `@types/react@19.2.18`, `@types/trusted-types@2.0.7`, `@types/unist@2.0.11`, `@types/unist@3.0.3`, `@types/use-sync-external-store@0.0.6`, `@upsetjs/venn.js@2.0.0`, `agent-base@6.0.2`, `aria-hidden@1.2.6`, `asynckit@0.4.0`, `axios@1.20.0`, `bail@2.0.2`, `call-bind-apply-helpers@1.0.2`, `ccount@2.0.1`, `character-entities-html4@2.1.0`, `character-entities-legacy@3.0.0`, `character-entities@2.0.2`, `character-reference-invalid@2.0.1`, `clsx@2.1.1`, `combined-stream@1.0.8`, `comma-separated-tokens@2.0.3`, `commander@7.2.0`, `commander@8.3.0`, `cookie@1.1.1`, `cose-base@1.0.3`, `cose-base@2.2.0`, `csstype@3.2.3`, `cytoscape-cose-bilkent@4.1.0`, `cytoscape-fcose@2.2.0`, `cytoscape@3.34.2`, `dagre-d3-es@7.0.14`, `date-fns@4.4.0`, `dayjs@1.11.23`, `debug@4.4.3`, `decimal.js-light@2.5.1`, `decode-named-character-reference@1.3.0`, `delayed-stream@1.0.0`, `dequal@2.0.3`, `detect-node-es@1.1.0`, `devlop@1.1.0`, `dunder-proto@1.0.1`, `es-define-property@1.0.1`, `es-errors@1.3.0`, `es-object-atoms@1.1.2`, `es-set-tostringtag@2.1.0`, `es-toolkit@1.48.1`, `escape-string-regexp@5.0.0`, `estree-util-is-identifier-name@3.0.0`, `eventemitter3@5.0.4`, `extend@3.0.2`, `fastdom@1.0.12`, `fflate@0.8.3`, `follow-redirects@1.16.1`, `form-data@4.0.6`, `function-bind@1.1.2`, `get-intrinsic@1.3.0`, `get-nonce@1.0.1`, `get-proto@1.0.1`, `goober@2.1.18`, `gopd@1.2.0`, `hachure-fill@0.5.2`, `has-symbols@1.1.0`, `has-tostringtag@1.0.2`, `hasown@2.0.4`, `hast-util-to-jsx-runtime@2.3.6`, `hast-util-whitespace@3.0.0`, `html-url-attributes@3.0.1`, `https-proxy-agent@5.0.1`, `iconv-lite@0.6.3`, `immer@11.1.17`, `import-meta-resolve@4.2.0`, `inline-style-parser@0.2.7`, `is-alphabetical@2.0.1`, `is-alphanumerical@2.0.1`, `is-decimal@2.0.1`, `is-hexadecimal@2.0.1`, `is-plain-obj@4.1.0`, `katex@0.18.4`, `khroma@2.1.0`, `layout-base@1.0.2`, `layout-base@2.0.1`, `lodash-es@4.18.1`, `longest-streak@3.1.0`, `markdown-table@3.0.4`, `marked@16.4.2`, `math-intrinsics@1.1.0`, `mdast-util-find-and-replace@3.0.2`, `mdast-util-from-markdown@2.0.3`, `mdast-util-gfm-autolink-literal@2.0.1`, `mdast-util-gfm-footnote@2.1.0`, `mdast-util-gfm-strikethrough@2.0.0`, `mdast-util-gfm-table@2.0.0`, `mdast-util-gfm-task-list-item@2.0.0`, `mdast-util-gfm@3.1.0`, `mdast-util-mdx-expression@2.0.1`, `mdast-util-mdx-jsx@3.2.0`, `mdast-util-mdxjs-esm@2.0.1`, `mdast-util-phrasing@4.1.0`, `mdast-util-to-hast@13.2.1`, `mdast-util-to-markdown@2.1.2`, `mdast-util-to-string@4.0.0`, `mermaid@11.17.2`, `micromark-core-commonmark@2.0.3`, `micromark-extension-gfm-autolink-literal@2.1.0`, `micromark-extension-gfm-footnote@2.1.0`, `micromark-extension-gfm-strikethrough@2.1.0`, `micromark-extension-gfm-table@2.1.1`, `micromark-extension-gfm-tagfilter@2.0.0`, `micromark-extension-gfm-task-list-item@2.1.0`, `micromark-extension-gfm@3.0.0`, `micromark-factory-destination@2.0.1`, `micromark-factory-label@2.0.1`, `micromark-factory-space@2.0.1`, `micromark-factory-title@2.0.1`, `micromark-factory-whitespace@2.0.1`, `micromark-util-character@2.1.1`, `micromark-util-chunked@2.0.1`, `micromark-util-classify-character@2.0.1`, `micromark-util-combine-extensions@2.0.1`, `micromark-util-decode-numeric-character-reference@2.0.2`, `micromark-util-decode-string@2.0.1`, `micromark-util-encode@2.0.1`, `micromark-util-html-tag-name@2.0.1`, `micromark-util-normalize-identifier@2.0.1`, `micromark-util-resolve-all@2.0.1`, `micromark-util-sanitize-uri@2.0.1`, `micromark-util-subtokenize@2.1.0`, `micromark-util-symbol@2.0.1`, `micromark-util-types@2.0.2`, `micromark@4.0.2`, `mime-db@1.52.0`, `mime-types@2.1.35`, `ms@2.1.3`, `package-manager-detector@1.8.0`, `parse-entities@4.0.2`, `path-data-parser@0.1.0`, `points-on-curve@0.2.0`, `points-on-path@0.2.1`, `property-information@7.1.0`, `proxy-from-env@2.1.0`, `react-dom@19.2.8`, `react-hot-toast@2.6.0`, `react-is@17.0.2`, `react-markdown@10.1.0`, `react-redux@9.3.0`, `react-remove-scroll-bar@2.3.8`, `react-remove-scroll@2.7.2`, `react-router-dom@7.18.3`, `react-router@7.18.3`, `react-style-singleton@2.2.3`, `react@19.2.8`, `recharts@3.10.1`, `redux-thunk@3.1.0`, `redux@5.0.1`, `remark-gfm@4.0.1`, `remark-parse@11.0.0`, `remark-rehype@11.1.2`, `remark-stringify@11.0.0`, `reselect@5.2.0`, `roughjs@4.6.6`, `safer-buffer@2.1.2`, `scheduler@0.27.0`, `set-cookie-parser@2.7.2`, `space-separated-tokens@2.0.2`, `strictdom@1.0.1`, `stringify-entities@4.0.4`, `style-to-js@1.1.21`, `style-to-object@1.0.14`, `stylis@4.4.0`, `swr@2.5.1`, `three@0.186.0`, `tiny-invariant@1.3.3`, `tinyexec@1.1.2`, `trim-lines@3.0.1`, `trough@2.2.0`, `ts-dedent@2.3.0`, `unified@11.0.5`, `unist-util-is@6.0.1`, `unist-util-position@5.0.0`, `unist-util-stringify-position@4.0.0`, `unist-util-visit-parents@6.0.2`, `unist-util-visit@5.1.0`, `use-callback-ref@1.3.3`, `use-sidecar@1.1.3`, `use-sync-external-store@1.6.0`, `uuid@14.0.2`, `vfile-message@4.0.3`, `vfile@6.0.3`, `zustand@5.0.15`, `zwitch@2.0.4`

</details>

<details><summary><strong>ISC</strong> (36)</summary>

`@ungap/structured-clone@1.3.0`, `d3-array@3.2.4`, `d3-axis@3.0.0`, `d3-brush@3.0.0`, `d3-chord@3.0.1`, `d3-color@3.1.0`, `d3-contour@4.0.2`, `d3-delaunay@6.0.4`, `d3-dispatch@3.0.1`, `d3-drag@3.0.0`, `d3-dsv@3.0.1`, `d3-fetch@3.0.1`, `d3-force@3.0.0`, `d3-format@3.1.2`, `d3-geo@3.1.1`, `d3-hierarchy@3.1.2`, `d3-interpolate@3.0.1`, `d3-path@3.1.0`, `d3-polygon@3.0.1`, `d3-quadtree@3.0.1`, `d3-random@3.0.1`, `d3-scale-chromatic@3.1.0`, `d3-scale@4.0.2`, `d3-selection@3.0.0`, `d3-shape@3.2.0`, `d3-time-format@4.1.0`, `d3-time@3.1.0`, `d3-timer@3.0.1`, `d3-transition@3.0.1`, `d3-zoom@3.0.0`, `d3@7.9.0`, `delaunator@5.0.1`, `internmap@1.0.1`, `internmap@2.0.3`, `lucide-react@1.41.0`, `qrcode.react@4.2.0`

</details>

<details><summary><strong>BSD-3-Clause</strong> (7)</summary>

`d3-array@2.12.1`, `d3-ease@3.0.1`, `d3-path@1.0.9`, `d3-sankey@0.12.3`, `d3-shape@1.3.7`, `rw@1.3.3`, `zrender@6.1.0`

</details>

<details><summary><strong>0BSD</strong> (2)</summary>

`tslib@2.3.0`, `tslib@2.8.1`

</details>

<details><summary><strong>Apache-2.0</strong> (3)</summary>

`@chevrotain/types@11.1.2`, `echarts@6.1.0`, `web-vitals@6.2.1`

</details>

<details><summary><strong>(MPL-2.0 OR Apache-2.0)</strong> (1)</summary>

`dompurify@3.4.17`

</details>

<details><summary><strong>MIT AND ISC</strong> (1)</summary>

`victory-vendor@37.3.6`

</details>

<details><summary><strong>Unlicense</strong> (1)</summary>

`robust-predicates@3.0.2`

</details>

## JavaScript reporter

No runtime dependencies.

## Java reporter

The `-all` jar bundles and relocates Jackson core, databind and
dataformat-yaml (Apache-2.0) and SnakeYAML (Apache-2.0); their NOTICE is in
[NOTICE](NOTICE). JUnit 5 (EPL-2.0) and TestNG (Apache-2.0) are `provided`
dependencies supplied by your test project, not bundled.

## Go reporter

Standard library only.

## Python dependencies (backend, workers, MCP server, CLI, Python reporter)

Direct dependencies as declared; their transitive dependencies are under the
same family of permissive licenses (MIT, BSD, Apache-2.0, PSF, ISC, MPL-2.0).

| Package | License | Used by |
|---|---|---|
| `aioboto3` | Apache-2.0 | backend |
| `aiohttp` | Apache-2.0 AND MIT | backend |
| `aiosmtplib` | MIT | backend |
| `aiosqlite` | MIT | backend |
| `alembic` | MIT | backend |
| `anyio` | MIT | backend |
| `asyncpg` | Apache-2.0 | backend |
| `beautifulsoup4` | MIT | backend |
| `celery` | BSD-3-Clause | backend |
| `chromadb` | Apache-2.0 | backend |
| `click` | BSD-3-Clause | backend |
| `cryptography` | Apache-2.0 OR BSD-3-Clause | cli |
| `defusedxml` | PSF-2.0 | backend |
| `email-validator` | Unlicense | backend |
| `factory-boy` | MIT | backend |
| `fastapi` | MIT | backend |
| `flower` | BSD-3-Clause | backend |
| `freezegun` | Apache-2.0 | backend |
| `gunicorn` | MIT | backend |
| `httpcore` | BSD-3-Clause | backend |
| `httpx` | BSD-3-Clause | backend, cli, client, mcp |
| `joblib` | BSD-3-Clause | backend |
| `kombu` | BSD-3-Clause | backend |
| `langchain` | MIT | backend |
| `langchain-anthropic` | MIT | backend |
| `langchain-google-genai` | MIT | backend |
| `langchain-ollama` | MIT | backend |
| `langchain-openai` | MIT | backend |
| `langgraph` | MIT | backend |
| `lxml` | BSD-3-Clause | backend |
| `mcp` | MIT | mcp |
| `motor` | Apache-2.0 | backend |
| `mypy` | MIT | backend |
| `narwhals` | MIT | backend |
| `numpy` | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | backend |
| `openpyxl` | MIT | backend |
| `opentelemetry-api` | Apache-2.0 | backend |
| `opentelemetry-exporter-otlp-proto-http` | Apache-2.0 | backend |
| `opentelemetry-instrumentation-fastapi` | Apache-2.0 | backend |
| `opentelemetry-instrumentation-httpx` | Apache-2.0 | backend |
| `opentelemetry-instrumentation-redis` | Apache-2.0 | backend |
| `opentelemetry-instrumentation-sqlalchemy` | Apache-2.0 | backend |
| `opentelemetry-sdk` | Apache-2.0 | backend |
| `passlib` | BSD-3-Clause | backend |
| `platformdirs` | MIT | cli |
| `prometheus-client` | Apache-2.0 AND BSD-2-Clause | backend |
| `prometheus-fastapi-instrumentator` | ISC | backend |
| `pydantic` | MIT | backend, cli |
| `pydantic-settings` | MIT | backend, mcp |
| `pyjwt` | MIT | backend, mcp |
| `pymongo` | Apache-2.0 | backend |
| `pyotp` | MIT | backend |
| `pypdf` | BSD-3-Clause | backend |
| `pytest` | MIT | backend |
| `pytest-asyncio` | Apache-2.0 | backend |
| `pytest-cov` | MIT | backend |
| `pytest-mock` | MIT | backend |
| `python-dateutil` | Apache-2.0 OR BSD-3-Clause | backend |
| `python-docx` | MIT | backend |
| `python-dotenv` | BSD-3-Clause | backend, mcp |
| `python-multipart` | Apache-2.0 | backend |
| `pytz` | MIT | backend |
| `pyyaml` | MIT | backend, cli, client |
| `redis` | MIT | backend |
| `reportlab` | BSD-3-Clause | backend |
| `rich` | MIT | cli |
| `ruff` | MIT | backend |
| `scikit-learn` | BSD-3-Clause | backend |
| `scipy` | BSD-3-Clause | backend |
| `signxml` | Apache-2.0 | backend |
| `slowapi` | MIT | backend |
| `sqlalchemy` | MIT | backend |
| `structlog` | MIT OR Apache-2.0 | backend |
| `tenacity` | Apache-2.0 | backend |
| `threadpoolctl` | BSD-3-Clause | backend |
| `typer` | MIT | cli |
| `types-python-dateutil` | Apache-2.0 | backend |
| `uvicorn` | BSD-3-Clause | backend |


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

| Image | License | Source |
|---|---|---|
| `postgres:16-alpine` | PostgreSQL License | https://www.postgresql.org/about/licence/ |
| `valkey/valkey:8.1-alpine` | BSD-3-Clause | https://github.com/valkey-io/valkey |
| `mongo:7` | SSPL-1.0 (MongoDB Community Server) | https://www.mongodb.com/legal/licensing/community-edition |
| `ghcr.io/versity/versitygw:v1.8.0` | Apache-2.0 | https://github.com/versity/versitygw |
| `busybox:1.36` | GPL-2.0-only | https://busybox.net/ |
| `ollama/ollama:0.5.4` | MIT; the image also contains NVIDIA CUDA runtime libraries under the NVIDIA CUDA EULA | https://github.com/ollama/ollama |
| `chromadb/chroma:1.5.9` | Apache-2.0 | https://github.com/chroma-core/chroma |
| `mher/flower:2.0` | BSD-3-Clause | https://github.com/mher/flower |

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
