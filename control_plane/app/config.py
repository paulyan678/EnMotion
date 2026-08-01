from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

MODEL_CAPABILITIES: dict[str, str] = {
    "gpt-image-2": "image",
    "doubao-seedance-2-0-260128": "video",
    "doubao-seedance-2-0-fast-260128": "video",
    "doubao-seedance-2-0-mini-260615": "video",
    "deepseek-v4-flash": "chat",
    "qwen3.7-max": "chat",
    "deepseek-v4-pro": "chat",
}
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class ConfigurationError(RuntimeError):
    """Raised when production configuration is incomplete or unsafe."""


def decode_provider_config_master_key(value: str) -> bytes:
    """Decode one dedicated 256-bit AES key without accepting weak fallbacks."""

    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise ConfigurationError(
            "ENMOTION_PROVIDER_CONFIG_MASTER_KEY must be URL-safe base64"
        ) from exc
    if len(decoded) != 32:
        raise ConfigurationError("ENMOTION_PROVIDER_CONFIG_MASTER_KEY must encode exactly 32 bytes")
    return decoded


def validate_provider_base_url(value: str, *, allow_insecure: bool) -> str:
    normalized = value.strip().rstrip("/")
    provider = urlparse(normalized)
    allowed_schemes = {"http", "https"} if allow_insecure else {"https"}
    if provider.scheme not in allowed_schemes:
        raise ConfigurationError("provider base URL must use HTTPS")
    if not provider.hostname:
        raise ConfigurationError("provider base URL must include a hostname")
    if (
        provider.username
        or provider.password
        or provider.params
        or provider.query
        or provider.fragment
    ):
        raise ConfigurationError(
            "provider base URL must not contain credentials, parameters, a query, or a fragment"
        )
    return normalized


def validate_public_origin(
    name: str,
    value: str,
    *,
    allow_insecure: bool,
) -> str:
    normalized = value.strip().rstrip("/")
    public = urlparse(normalized)
    allowed_schemes = {"http", "https"} if allow_insecure else {"https"}
    try:
        port = public.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a secure absolute origin") from exc
    if (
        public.scheme not in allowed_schemes
        or not public.hostname
        or public.username
        or public.password
        or public.path not in {"", "/"}
        or public.params
        or public.query
        or public.fragment
    ):
        raise ConfigurationError(f"{name} must be a secure absolute origin")
    default_port = 443 if public.scheme == "https" else 80
    authority = public.hostname.lower()
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{public.scheme.lower()}://{authority}"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _parse_json_object(name: str, default: dict[str, str] | None = None) -> dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return dict(default or {})
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{name} must be a JSON object") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
    ):
        raise ConfigurationError(f"{name} must map string keys to string values")
    return {key.strip(): value.strip() for key, value in parsed.items() if value.strip()}


@dataclass(frozen=True)
class Settings:
    database_url: str
    session_hmac_secret: str
    provider_base_url: str = "https://api.example.invalid/v1"
    provider_credentials: dict[str, str] = field(default_factory=dict, repr=False)
    provider_config_master_key: str = field(default="", repr=False)
    release_manifest_path: str = ""
    release_allowed_hosts: tuple[str, ...] = ()
    release_source_credentials: dict[str, str] = field(default_factory=dict, repr=False)
    public_base_url: str = ""
    public_base_url_aliases: tuple[str, ...] = ()
    cookie_secure: bool = True
    access_ttl_seconds: int = 15 * 60
    refresh_ttl_seconds: int = 7 * 24 * 60 * 60
    max_request_body_bytes: int = 40 * 1024 * 1024
    provider_connect_timeout_seconds: float = 10.0
    provider_read_timeout_seconds: float = 900.0
    provider_submission_attempts: int = 4
    provider_retry_backoff_seconds: float = 0.5
    allow_insecure_upstreams: bool = False
    environment: str = "production"
    auto_create_schema: bool = False
    login_attempts_per_minute: int = 10
    release_session_ttl_seconds: int = 2 * 60 * 60
    app_version: str = "0.1.0"

    def __post_init__(self) -> None:
        if not self.database_url.startswith("sqlite:///"):
            raise ConfigurationError("ENMOTION_DATABASE_URL must use the supported SQLite database")
        if self.environment == "production" and self.database_url == "sqlite:///:memory:":
            raise ConfigurationError("production requires file-backed SQLite")
        if len(self.session_hmac_secret) < 32:
            raise ConfigurationError(
                "ENMOTION_SESSION_HMAC_SECRET must contain at least 32 characters"
            )
        unknown = set(self.provider_credentials) - set(MODEL_CAPABILITIES)
        if unknown:
            raise ConfigurationError(
                "Provider credentials contain unsupported model IDs: " + ", ".join(sorted(unknown))
            )
        unknown_release_hosts = set(self.release_source_credentials) - set(
            self.release_allowed_hosts
        )
        if unknown_release_hosts:
            raise ConfigurationError(
                "Release source credentials contain non-allowlisted hosts: "
                + ", ".join(sorted(unknown_release_hosts))
            )
        invalid_release_hosts = [
            host for host in self.release_allowed_hosts if not _HOSTNAME.fullmatch(host)
        ]
        if invalid_release_hosts:
            raise ConfigurationError(
                "Release allowlist entries must be exact lowercase hostnames: "
                + ", ".join(sorted(invalid_release_hosts))
            )
        validate_provider_base_url(
            self.provider_base_url,
            allow_insecure=self.allow_insecure_upstreams,
        )
        if self.provider_config_master_key:
            decode_provider_config_master_key(self.provider_config_master_key)
        public_origins = []
        if self.public_base_url:
            public_origins.append(("ENMOTION_PUBLIC_BASE_URL", self.public_base_url))
        public_origins.extend(
            ("ENMOTION_PUBLIC_BASE_URL_ALIASES", alias) for alias in self.public_base_url_aliases
        )
        for name, origin in public_origins:
            normalized = validate_public_origin(
                name,
                origin,
                allow_insecure=self.allow_insecure_upstreams,
            )
            if normalized != origin:
                raise ConfigurationError(f"{name} must use a normalized origin")
        if self.access_ttl_seconds < 60:
            raise ConfigurationError("access token lifetime must be at least 60 seconds")
        if self.refresh_ttl_seconds <= self.access_ttl_seconds:
            raise ConfigurationError("refresh token lifetime must exceed access token lifetime")
        if self.max_request_body_bytes < 1024 * 1024:
            raise ConfigurationError("request body limit must be at least 1 MiB")
        for name, timeout in (
            ("provider connect timeout", self.provider_connect_timeout_seconds),
            ("provider read timeout", self.provider_read_timeout_seconds),
        ):
            if not math.isfinite(timeout) or not 0 < timeout <= 3600:
                raise ConfigurationError(f"{name} must be between 0 and 3600 seconds")
        if not 1 <= self.provider_submission_attempts <= 10:
            raise ConfigurationError("provider submission attempts must be between 1 and 10")
        if (
            not math.isfinite(self.provider_retry_backoff_seconds)
            or not 0 <= self.provider_retry_backoff_seconds <= 30
        ):
            raise ConfigurationError("provider retry backoff must be between 0 and 30 seconds")
        if self.login_attempts_per_minute < 1:
            raise ConfigurationError("login attempt limit must be positive")
        if not 300 <= self.release_session_ttl_seconds <= 24 * 60 * 60:
            raise ConfigurationError(
                "release session lifetime must be between 5 minutes and 24 hours"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("ENMOTION_ENV", "production").strip().lower()
        secret = os.getenv("ENMOTION_SESSION_HMAC_SECRET", "").strip()
        if not secret:
            raise ConfigurationError("ENMOTION_SESSION_HMAC_SECRET is required")
        database_url = os.getenv(
            "ENMOTION_DATABASE_URL",
            "sqlite:////var/lib/enmotion-control/control.db",
        ).strip()
        allowed_hosts = tuple(
            item.strip().lower()
            for item in os.getenv("ENMOTION_RELEASE_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        )
        public_aliases = tuple(
            item.strip().rstrip("/")
            for item in os.getenv("ENMOTION_PUBLIC_BASE_URL_ALIASES", "").split(",")
            if item.strip()
        )
        return cls(
            database_url=database_url,
            session_hmac_secret=secret,
            provider_base_url=os.getenv(
                "ENMOTION_PROVIDER_BASE_URL", "https://api.example.invalid/v1"
            ).rstrip("/"),
            provider_credentials=_parse_json_object("ENMOTION_PROVIDER_CREDENTIALS_JSON"),
            provider_config_master_key=os.getenv("ENMOTION_PROVIDER_CONFIG_MASTER_KEY", "").strip(),
            release_manifest_path=os.getenv("ENMOTION_RELEASE_MANIFEST_PATH", "").strip(),
            release_allowed_hosts=allowed_hosts,
            release_source_credentials={
                host.lower(): credential
                for host, credential in _parse_json_object(
                    "ENMOTION_RELEASE_SOURCE_CREDENTIALS_JSON"
                ).items()
            },
            public_base_url=os.getenv("ENMOTION_PUBLIC_BASE_URL", "").strip().rstrip("/"),
            public_base_url_aliases=public_aliases,
            cookie_secure=_env_bool("ENMOTION_COOKIE_SECURE", environment == "production"),
            access_ttl_seconds=_env_int("ENMOTION_ACCESS_TTL_SECONDS", 15 * 60),
            refresh_ttl_seconds=_env_int("ENMOTION_REFRESH_TTL_SECONDS", 7 * 24 * 60 * 60),
            max_request_body_bytes=_env_int("ENMOTION_MAX_REQUEST_BODY_BYTES", 40 * 1024 * 1024),
            provider_connect_timeout_seconds=float(
                os.getenv("ENMOTION_PROVIDER_CONNECT_TIMEOUT_SECONDS", "10")
            ),
            provider_read_timeout_seconds=float(
                os.getenv("ENMOTION_PROVIDER_READ_TIMEOUT_SECONDS", "900")
            ),
            provider_submission_attempts=_env_int("ENMOTION_PROVIDER_SUBMISSION_ATTEMPTS", 4),
            provider_retry_backoff_seconds=float(
                os.getenv("ENMOTION_PROVIDER_RETRY_BACKOFF_SECONDS", "0.5")
            ),
            allow_insecure_upstreams=_env_bool("ENMOTION_ALLOW_INSECURE_UPSTREAMS", False),
            environment=environment,
            auto_create_schema=_env_bool("ENMOTION_AUTO_CREATE_SCHEMA", False),
            login_attempts_per_minute=_env_int("ENMOTION_LOGIN_ATTEMPTS_PER_MINUTE", 10),
            release_session_ttl_seconds=_env_int(
                "ENMOTION_RELEASE_SESSION_TTL_SECONDS", 2 * 60 * 60
            ),
            app_version=os.getenv("ENMOTION_CONTROL_PLANE_VERSION", "0.1.0").strip(),
        )

    @property
    def release_manifest(self) -> Path | None:
        return Path(self.release_manifest_path).expanduser() if self.release_manifest_path else None
