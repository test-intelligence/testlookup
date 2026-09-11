"""The lease floor's error says what 0 means: the built-in 60, not "the default"
(the default IS 60; b45 r3 LOW)."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_the_lease_error_names_the_built_in_value():
    with pytest.raises(ValidationError) as raised:
        Settings(LLM_CLUSTER_SLOT_LEASE_SECONDS=5)
    message = str(raised.value)
    assert "0 (use the built-in 60)" in message
    assert "0 (default)" not in message
    assert Settings.model_fields["LLM_CLUSTER_SLOT_LEASE_SECONDS"].default == 60
