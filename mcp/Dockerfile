FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server source
COPY . .

# SSE transport by default when containerised
CMD ["python", "server.py", "--transport", "sse", "--port", "8002"]

EXPOSE 8002
