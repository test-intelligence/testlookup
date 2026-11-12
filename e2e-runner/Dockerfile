FROM mcr.microsoft.com/playwright:v1.65.0-noble@sha256:9beb546099e11f52d62e505537914870e01a67e0e6b00ae41da4775324e73dbe AS ubuntu-packages
ARG OS_PATCH_DAY=unset
ARG UBUNTU_ARCHIVE_HOST=archive.ubuntu.com
# The hosted build must receive complete signed indexes, including security updates.
RUN sed -i -e "s|http://azure.archive.ubuntu.com/ubuntu|https://${UBUNTU_ARCHIVE_HOST}/ubuntu|g" \
           -e "s|http://security.ubuntu.com/ubuntu|https://${UBUNTU_ARCHIVE_HOST}/ubuntu|g" \
           /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update

FROM ubuntu-packages AS reporter-build
WORKDIR /release
ENV CYPRESS_CACHE_FOLDER=/opt/cypress PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN apt-get install -o Acquire::Retries=3 --no-install-recommends -y openjdk-17-jdk-headless maven golang-go
COPY client/js ./client/js
COPY e2e-runner/reporters/prepare.py ./e2e-runner/reporters/
COPY e2e-runner/reporters/js/package*.json ./e2e-runner/reporters/js/
RUN python3 e2e-runner/reporters/prepare.py javascript
COPY client/go ./client/go
COPY e2e-runner/reporters/go ./e2e-runner/reporters/go
# The reporter embeds its compiler's standard library in the runtime binary.
RUN GOTOOLCHAIN=go1.27.4 python3 e2e-runner/reporters/prepare.py go
COPY client/java ./client/java
COPY e2e-runner/reporters/java ./e2e-runner/reporters/java
RUN python3 e2e-runner/reporters/prepare.py java

FROM ubuntu-packages
WORKDIR /release
RUN test "$(node --version)" = v26.13.0 && test "$(python3 --version)" = "Python 3.12.3" \
    && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=3 upgrade -y \
    && apt-get install -o Acquire::Retries=3 --no-install-recommends -y python3-venv openjdk-17-jre-headless xvfb xauth libgtk-3-0 libnotify4 libxtst6 \
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
