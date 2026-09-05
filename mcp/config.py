"""MCP server configuration loaded from environment variables or .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # TestLookup backend
    api_url: str = "http://localhost:8000"
    username: str = ""
    password: str = ""

    # HTTP client
    request_timeout: float = 120.0  # seconds; AI analysis can take 60+s
    auth_timeout: float = 5.0
    auth_max_connections: int = 50

    # DNS-rebinding protection for network transports. Values are comma-separated
    # exact hosts/origins; ``:*`` is supported for local ephemeral ports.
    mcp_allowed_hosts: str = "127.0.0.1:*,localhost:*,[::1]:*"
    mcp_allowed_origins: str = ""

    model_config = SettingsConfigDict(
        env_prefix="TESTLOOKUP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
