.PHONY: install start sse

install: ## Install MCP server Python dependencies
	pip install -r requirements.txt

start: ## Start MCP server (stdio mode)
	python server.py --transport stdio

sse: ## Start MCP server (SSE mode on port 8002)
	python server.py --transport sse --port 8002
