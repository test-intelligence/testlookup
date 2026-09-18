"""CLI error handling — maps HTTP errors to user-friendly messages and exit codes."""
import httpx

# Stable exit codes for CI automation
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_VALIDATION = 2
EXIT_AUTH = 3
EXIT_PERMISSION = 4
EXIT_NOT_FOUND = 5
EXIT_TIMEOUT = 6


class CLIError(Exception):
    """Base CLI error with exit code."""
    def __init__(self, message: str, exit_code: int = EXIT_ERROR):
        super().__init__(message)
        self.exit_code = exit_code


def exit_code_for(exc: Exception) -> int:
    """Preserve stable automation codes while generic failures remain code 1."""
    return exc.exit_code if isinstance(exc, CLIError) else EXIT_ERROR


def map_http_error(status_code: int, detail: str = "") -> CLIError:
    """Map an HTTP status code to a CLIError with appropriate exit code."""
    if status_code == 401:
        return CLIError(f"Authentication failed. Run 'testlookup auth login' first. {detail}".strip(), EXIT_AUTH)
    if status_code == 403:
        return CLIError(f"Permission denied. {detail}".strip(), EXIT_PERMISSION)
    if status_code == 404:
        return CLIError(f"Not found. {detail}".strip(), EXIT_NOT_FOUND)
    if status_code in (400, 422):
        return CLIError(f"Validation error. {detail}".strip(), EXIT_VALIDATION)
    if status_code == 408 or status_code == 504:
        return CLIError(f"Request timed out. {detail}".strip(), EXIT_TIMEOUT)
    return CLIError(f"Server error ({status_code}). {detail}".strip(), EXIT_ERROR)


def map_connection_error(exc: Exception, base_url: str = "") -> CLIError:
    """Map a low-level transport failure to a friendly, actionable CLIError.

    These fire *before* any HTTP response exists — the server is down, the URL
    is wrong, DNS does not resolve, or the request timed out — so
    ``map_http_error`` (which needs a status code) never sees them. Without this
    a fresh self-hoster whose server is not up yet, or who mistyped the URL,
    gets a bare ``[Errno 111] Connection refused`` (or an empty message) at the
    highest-friction moment of adoption instead of a hint about what to do.

    Timeouts keep the dedicated ``EXIT_TIMEOUT`` code (matching the 408/504
    mapping in ``map_http_error``); every other transport failure is a plain
    ``EXIT_ERROR``.
    """
    where = f" at {base_url}" if base_url else ""
    if isinstance(exc, httpx.TimeoutException):
        return CLIError(
            f"Request to the TestLookup server{where} timed out — it may be "
            "overloaded or still starting up.",
            EXIT_TIMEOUT,
        )
    return CLIError(
        f"Cannot reach the TestLookup server{where}. Is it running? "
        "Check the URL with 'testlookup auth login --url <url>' "
        "(or the TESTLOOKUP_URL env var).",
        EXIT_ERROR,
    )
