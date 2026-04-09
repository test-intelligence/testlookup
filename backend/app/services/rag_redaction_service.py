"""RAG privacy and redaction controls (RAG-13).

Reuses existing redaction patterns from audit_dashboard_service.
Applied pre-prompt-injection to strip PII/secrets from chunk text
before sending to LLM.
"""
from __future__ import annotations

import re

# Patterns that match sensitive content
_SENSITIVE_KEY_PATTERNS = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|bearer|credential|private[_-]?key)",
    re.IGNORECASE,
)

_SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),  # base64 tokens
    re.compile(r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+"),  # JWT
    re.compile(r"[a-f0-9]{32,64}"),  # hex hashes that look like API keys
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # emails
]

# Max file sizes by type (bytes)
ALLOWED_DOC_TYPES = {"pdf", "docx", "doc", "md", "txt", "markdown", "rst"}
MAX_DOC_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# URL restrictions
BLOCKED_SCHEMES = {"ftp", "file", "data", "javascript"}


def redact_prompt(text: str, classification: str = "internal") -> tuple[str, bool]:
    """
    Apply redaction rules to prompt text before LLM injection.
    Returns (redacted_text, was_redacted).
    """
    if not text:
        return text, False

    was_redacted = False
    result = text

    for pattern in _SENSITIVE_VALUE_PATTERNS:
        new_result = pattern.sub("[REDACTED]", result)
        if new_result != result:
            was_redacted = True
            result = new_result

    # For confidential/restricted, apply more aggressive redaction
    if classification in ("confidential", "restricted"):
        # Redact anything after sensitive-looking keys
        lines = result.split("\n")
        redacted_lines = []
        for line in lines:
            if _SENSITIVE_KEY_PATTERNS.search(line):
                # Redact the value part (after : or =)
                redacted_line = re.sub(r"([:=]\s*).+", r"\1[REDACTED]", line)
                if redacted_line != line:
                    was_redacted = True
                redacted_lines.append(redacted_line)
            else:
                redacted_lines.append(line)
        result = "\n".join(redacted_lines)

    return result, was_redacted


def redact_chunk_text(chunk_text: str, classification: str = "internal") -> str:
    """Strip PII-pattern matches from chunk text before prompt injection."""
    redacted, _ = redact_prompt(chunk_text, classification)
    return redacted


def validate_document_upload(filename: str, size_bytes: int) -> None:
    """Validate uploaded document type and size. Raises ValueError on rejection."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_DOC_TYPES:
        raise ValueError(
            f"File type '.{ext}' is not allowed. Supported: {', '.join(sorted(ALLOWED_DOC_TYPES))}"
        )
    if size_bytes > MAX_DOC_SIZE_BYTES:
        raise ValueError(
            f"File size ({size_bytes / 1024 / 1024:.1f} MB) exceeds maximum ({MAX_DOC_SIZE_BYTES / 1024 / 1024:.0f} MB)"
        )


def validate_url_scheme(url: str) -> None:
    """Block dangerous URL schemes."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme.lower() in BLOCKED_SCHEMES:
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed")
