"""CLI error handling — maps HTTP errors to user-friendly messages and exit codes."""

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


def map_http_error(status_code: int, detail: str = "") -> CLIError:
    """Map an HTTP status code to a CLIError with appropriate exit code."""
    if status_code == 401:
        return CLIError(f"Authentication failed. Run 'testlookup auth login' first. {detail}".strip(), EXIT_AUTH)
    if status_code == 403:
        return CLIError(f"Permission denied. {detail}".strip(), EXIT_PERMISSION)
    if status_code == 404:
        return CLIError(f"Not found. {detail}".strip(), EXIT_NOT_FOUND)
    if status_code == 422:
        return CLIError(f"Validation error. {detail}".strip(), EXIT_VALIDATION)
    if status_code == 408 or status_code == 504:
        return CLIError(f"Request timed out. {detail}".strip(), EXIT_TIMEOUT)
    return CLIError(f"Server error ({status_code}). {detail}".strip(), EXIT_ERROR)
