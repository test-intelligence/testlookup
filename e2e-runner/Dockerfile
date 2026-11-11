FROM mcr.microsoft.com/playwright:v1.64.1-noble@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0 AS reporter-build
WORKDIR /release
ENV CYPRESS_CACHE_FOLDER=/opt/cypress PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN apt-get update && apt-get install --no-install-recommends -y openjdk-17-jdk-headless maven golang-go
COPY client/js ./client/js
COPY e2e-runner/reporters/prepare.py ./e2e-runner/reporters/
COPY e2e-runner/reporters/js/package*.json ./e2e-runner/reporters/js/
RUN python3 e2e-runner/reporters/prepare.py javascript
COPY client/go ./client/go
COPY e2e-runner/reporters/go ./e2e-runner/reporters/go
RUN python3 e2e-runner/reporters/prepare.py go
COPY client/java ./client/java
COPY e2e-runner/reporters/java ./e2e-runner/reporters/java
RUN python3 e2e-runner/reporters/prepare.py java

FROM mcr.microsoft.com/playwright:v1.64.1-noble@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0
WORKDIR /release
ARG OS_PATCH_DAY=unset
RUN test "$(node --version)" = v26.13.0 && test "$(python3 --version)" = "Python 3.12.3" \
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && apt-get install --no-install-recommends -y python3-venv openjdk-17-jre-headless xvfb xauth libgtk-3-0 libnotify4 libxtst6 \
    && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/release-venv
ENV PATH="/opt/release-venv/bin:$PATH" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
ENV CYPRESS_CACHE_FOLDER=/opt/cypress
COPY --from=reporter-build /opt/cypress /opt/cypress
COPY --from=reporter-build /release/client/js/package.json ./client/js/package.json
COPY --from=reporter-build /release/client/js/dist ./client/js/dist
COPY --from=reporter-build /release/e2e-runner/reporters/js/node_modules ./e2e-runner/reporters/js/node_modules
COPY --from=reporter-build /release/e2e-runner/reporters/prepared ./e2e-runner/reporters/prepared
COPY frontend/package.json frontend/package-lock.json ./frontend/
# The suite invokes the locked Playwright CLI with node. npm is build-only.
RUN cd frontend && npm ci --no-audit --no-fund && npm cache clean --force \
    && rm -rf /usr/lib/node_modules/npm && rm -f /usr/bin/npm /usr/bin/npx
COPY cli ./cli
COPY mcp ./mcp
COPY e2e-runner/requirements.txt e2e-runner/constraints.txt ./e2e-runner/
RUN pip install --no-cache-dir -c e2e-runner/constraints.txt -r e2e-runner/requirements.txt -r mcp/requirements.txt ./cli
COPY e2e-runner ./e2e-runner
COPY samples ./samples
COPY client/*.py ./client/
COPY scripts/release/release_manifest.py ./scripts/release/
COPY frontend/playwright.release.config.ts ./frontend/
COPY frontend/tests/release ./frontend/tests/release/
ENTRYPOINT ["python", "e2e-runner/run.py"]
