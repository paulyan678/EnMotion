from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import MODEL_CAPABILITIES


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    role: Literal["user", "admin"]
    active: bool
    available_credits: int
    reserved_credits: int
    created_at: datetime
    updated_at: datetime


class SessionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_label: str | None
    access_expires_at: datetime
    refresh_expires_at: datetime
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    device_label: str | None = Field(default=None, max_length=120)


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=32, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=32, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: datetime
    refresh_expires_at: datetime
    csrf_token: str
    user: UserPublic


class SessionResponse(BaseModel):
    user: UserPublic
    session: SessionPublic


class MessageResponse(BaseModel):
    message: str
    reauthentication_required: bool = False


class BalanceResponse(BaseModel):
    available_credits: int
    reserved_credits: int
    total_credits: int


class UsagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    idempotency_key: str
    operation: str
    model: str
    status: str
    reserved_units: int
    settled_units: int
    upstream_task_id: str | None
    upstream_status: int | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    settled_at: datetime | None


class UsagePage(BaseModel):
    items: list[UsagePublic]
    next_cursor: str | None


class AdminUsagePublic(UsagePublic):
    user_id: str


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["user", "admin"] = "user"
    initial_credits: int = Field(default=0, ge=0, le=2_000_000_000)


class UserStatusRequest(BaseModel):
    active: bool


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class RevokeSessionsRequest(BaseModel):
    except_session_id: str | None = None


class CreditAdjustmentRequest(BaseModel):
    delta: int = Field(ge=-2_000_000_000, le=2_000_000_000)
    reason: str = Field(min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=128)


class LedgerPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    actor_user_id: str | None
    usage_request_id: str | None
    entry_type: str
    delta_available: int
    delta_reserved: int
    available_after: int
    reserved_after: int
    reason: str
    idempotency_key: str
    created_at: datetime


ALLOWED_RATE_SELECTORS = {
    "size",
    "quality",
    "duration",
    "resolution",
    "ratio",
    "generation_mode",
    "generate_audio",
}


class RateCardCreate(BaseModel):
    operation: Literal[
        "chat.completions",
        "images.generations",
        "images.edits",
        "video.generations",
    ]
    model: str
    unit_cost: int = Field(ge=0, le=2_000_000_000)
    selectors: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=-10_000, le=10_000)
    active: bool = True

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if value not in MODEL_CAPABILITIES:
            raise ValueError("unsupported provider model")
        return value

    @field_validator("selectors")
    @classmethod
    def validate_selectors(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = set(value) - ALLOWED_RATE_SELECTORS
        if unknown:
            raise ValueError("unsupported rate selectors: " + ", ".join(sorted(unknown)))
        return value


class RateCardUpdate(BaseModel):
    unit_cost: int | None = Field(default=None, ge=0, le=2_000_000_000)
    selectors: dict[str, Any] | None = None
    priority: int | None = Field(default=None, ge=-10_000, le=10_000)
    active: bool | None = None

    @field_validator("selectors")
    @classmethod
    def validate_selectors(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value
        unknown = set(value) - ALLOWED_RATE_SELECTORS
        if unknown:
            raise ValueError("unsupported rate selectors: " + ", ".join(sorted(unknown)))
        return value


class RateCardPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    operation: str
    model: str
    unit_cost: int
    selectors: dict[str, Any]
    priority: int
    active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class ReconcileUsageRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=240)
    charged_units: int | None = Field(default=None, ge=0, le=2_000_000_000)


class AuditPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_user_id: str | None
    action: str
    target_type: str
    target_id: str | None
    detail: dict[str, Any]
    ip_address: str | None
    created_at: datetime


class ProviderModelStatus(BaseModel):
    model: str
    capability: Literal["chat", "image", "video"]
    configured: bool


class ProviderConfigPublic(BaseModel):
    version: int = Field(ge=0)
    source: Literal["environment", "managed"]
    base_url: str
    writable: bool
    updated_at: datetime | None
    models: list[ProviderModelStatus]


class ProviderConfigUpdate(BaseModel):
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    credentials: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("credentials")
    @classmethod
    def validate_credentials(
        cls,
        value: dict[str, str | None],
    ) -> dict[str, str | None]:
        unknown = set(value) - set(MODEL_CAPABILITIES)
        if unknown:
            raise ValueError("unsupported provider model")
        if len(value) > len(MODEL_CAPABILITIES):
            raise ValueError("too many provider credentials")
        for credential in value.values():
            if credential is not None and (
                not credential.strip()
                or len(credential) > 16_384
                or "\r" in credential
                or "\n" in credential
            ):
                raise ValueError("provider credential is empty or invalid")
        return value


class ReleaseSessionRequest(BaseModel):
    target: Literal["darwin", "windows"]
    arch: Literal["aarch64", "x86_64"]
    current_version: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[0-9A-Za-z._+-]+$",
    )
    channel: Literal["stable"] = "stable"


class ReleaseSessionResponse(BaseModel):
    manifest_url: str


class IdempotentReplayResponse(BaseModel):
    idempotent_replay: Literal[True] = True
    usage_request: UsagePublic
