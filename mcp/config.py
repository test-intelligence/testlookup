"""MCP server configuration loaded from environment variables or .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # TestLookup backend
    api_url: str = "http://localhost:8000"
    username: str = ""
    password: str = ""

    # HTTP client
    request_timeout: float = 120.0  # seconds; AI analysis can take 60+s

    model_config = SettingsConfigDict(
        env_prefix="TESTLOOKUP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
