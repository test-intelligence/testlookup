"""MCP server configuration, loaded from environment variables and one env file.

The env file is ``.env`` next to ``server.py``, or, when ``TESTLOOKUP_ENV_FILE``
is set, only the file that variable names. It is never looked up in the working
directory: an MCP client starts the stdio server with whatever working directory
it likes (often the workspace the user has open), and a ``.env`` planted there
could point ``TESTLOOKUP_API_URL`` at another host and collect the user's
TestLookup username and password. Real environment variables win over the file.
"""

import os
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

#: The directory that holds ``server.py`` (and this file).
SERVER_DIR = Path(__file__).resolve().parent
ENV_FILE_VARIABLE = "TESTLOOKUP_ENV_FILE"


def env_file() -> Path:
    """The one env file the server reads; it does not have to exist.

    A relative ``TESTLOOKUP_ENV_FILE`` is taken from the server's directory,
    not from the working directory.
    """
    named = os.environ.get(ENV_FILE_VARIABLE, "").strip()
    if not named:
        return SERVER_DIR / ".env"
    chosen = Path(named).expanduser()
    return chosen if chosen.is_absolute() else SERVER_DIR / chosen


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
        # No relative default here: pydantic-settings resolves a relative
        # ``env_file`` against the working directory. ``__init__`` passes the
        # resolved path instead.
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __init__(self, **values: Any) -> None:
        values.setdefault("_env_file", env_file())
        super().__init__(**values)


settings = Settings()
