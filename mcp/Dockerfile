FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server source
COPY . .

# Run as non-root user
RUN adduser --disabled-password --gecos '' --uid 1000 mcp
USER mcp

# SSE transport by default when containerised
CMD ["python", "server.py", "--transport", "sse", "--port", "8002"]

EXPOSE 8002
