FROM python:3.11-slim

WORKDIR /app

# Don't write .pyc files — the k8s pod runs with readOnlyRootFilesystem: true,
# so any bytecode write to /app would fail.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server source
COPY . .

# Run as non-root user (UID 1000 matches the k8s securityContext)
RUN adduser --disabled-password --gecos '' --uid 1000 mcp
USER mcp

EXPOSE 8002

# SSE transport by default when containerised
CMD ["python", "server.py", "--transport", "sse", "--port", "8002"]
