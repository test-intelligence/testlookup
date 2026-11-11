FROM mcr.microsoft.com/playwright:v1.64.1-noble@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0
WORKDIR /release
RUN test "$(node --version)" = v26.13.0 && test "$(python3 --version)" = "Python 3.12.3" \
    && apt-get update && apt-get install --no-install-recommends -y python3-venv \
    && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/release-venv
ENV PATH="/opt/release-venv/bin:$PATH" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci --no-audit --no-fund
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
