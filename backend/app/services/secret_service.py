from typing import Optional


VISIBLE_PREFIX_LEN = 4   # e.g. 'sk-a', 'ghp_'
VISIBLE_SUFFIX_LEN = 3   # e.g. '678'


def mask_secret(value: Optional[str]) -> str:
    """
    Mask secret values while preserving a small prefix and suffix.

    Behavior expected by tests:
    - None or empty -> ''.
    - Very short (len <= 4) -> '****'.
    - Otherwise:
      'sk-a12345678' -> 'sk-a...678'
      'ghp_abcdefxyz' -> 'ghp_...xyz'
    """
    if not value:
        return ""

    if len(value) <= VISIBLE_PREFIX_LEN:
        return "****"

    prefix = value[:VISIBLE_PREFIX_LEN]
    suffix = value[-VISIBLE_SUFFIX_LEN:]
    return f"{prefix}...{suffix}"


def mask_api_key(raw_key: Optional[str]) -> str:
    """
    Wrapper used by the rest of the codebase to mask API keys consistently.
    """
    return mask_secret(raw_key)
