"""Explicit instance retention contracts for routine authentication events."""
from __future__ import annotations

from typing import Literal
import uuid

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

ROUTINE_EVENT_TYPES = ("MFA_VERIFY_SUCCESS", "SSO_LOGIN", "SSO_LOGOUT")
AUTHORITY_EVENT_TYPES = ("SCIM_USER_CREATED",)
RETENTION_MIN_DAYS, RETENTION_MAX_DAYS, RETENTION_DEFAULT_DAYS = 365, 3650, 2555
MAX_BATCH_SIZE = 500
MAX_HOLDS = 100


class IdentityRetentionHold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)
    placed_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    audit_id: uuid.UUID


class IdentityRetentionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    enabled: StrictBool = False
    retention_days: StrictInt = Field(default=RETENTION_DEFAULT_DAYS, ge=RETENTION_MIN_DAYS, le=RETENTION_MAX_DAYS)
    revision: StrictInt = Field(default=0, ge=0)
    approved_at: AwareDatetime | None = None
    eligible_event_types: list[str] = Field(default_factory=lambda: list(ROUTINE_EVENT_TYPES))
    holds: list[IdentityRetentionHold] = Field(default_factory=list, max_length=MAX_HOLDS)

    @model_validator(mode="after")
    def check_authority(self):
        if self.eligible_event_types != list(ROUTINE_EVENT_TYPES):
            raise ValueError("identity record classes need explicit policy migration")
        if self.enabled and self.approved_at is None:
            raise ValueError("enabled identity retention has no approval")
        if len({hold.id for hold in self.holds}) != len(self.holds):
            raise ValueError("duplicate identity hold")
        return self


class IdentityRetentionRead(IdentityRetentionDocument):
    scope: Literal["instance"] = "instance"
    protected_authority_event_types: list[str] = Field(default_factory=lambda: list(AUTHORITY_EVENT_TYPES))
    protected_identity_events: str = "All other identity event types, including unknown future types, remain retained."
    active_hold_count: int = 0


class IdentityRetentionPreview(BaseModel):
    observed_at: AwareDatetime
    cutoff: AwareDatetime
    retention_days: int
    enabled: bool
    active_hold_count: int
    counts: dict[str, int]
    max_batch_size: int = MAX_BATCH_SIZE
    preview_hash: str
    enable_confirmation: str
    purge_confirmation: str


class IdentityRetentionApproval(BaseModel):
    retention_days: StrictInt = Field(ge=RETENTION_MIN_DAYS, le=RETENTION_MAX_DAYS)
    observed_at: AwareDatetime
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: str
    reason: str = Field(min_length=3, max_length=500)


class IdentityRetentionReason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class IdentityRetentionHoldCreate(IdentityRetentionReason):
    expires_at: AwareDatetime | None = None


class IdentityRetentionHoldRelease(IdentityRetentionReason):
    confirmation: str


class IdentityRetentionPurge(BaseModel):
    observed_at: AwareDatetime
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: str
    reason: str = Field(min_length=3, max_length=500)
    batch_size: StrictInt = Field(default=MAX_BATCH_SIZE, ge=1, le=MAX_BATCH_SIZE)


class IdentityRetentionPurgeResult(BaseModel):
    status: Literal["completed"]
    deleted: int
    batch_size: int
    batch_full: bool
    cutoff: AwareDatetime
    policy_revision: int
