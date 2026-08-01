from __future__ import annotations

import pytest
from app.config import ConfigurationError, Settings


def settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite:////tmp/enmotion-test.db",
        "session_hmac_secret": "x" * 48,
        "provider_base_url": "https://provider.test/v1",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"database_url": "postgresql://database.test/enmotion"},
            "supported SQLite",
        ),
        (
            {"provider_base_url": ("https://user:secret@provider.test/v1?redirect=attacker")},
            "must not contain credentials",
        ),
        (
            {"release_allowed_hosts": ("https://downloads.test",)},
            "exact lowercase hostnames",
        ),
        (
            {"provider_read_timeout_seconds": float("nan")},
            "between 0 and 3600",
        ),
        (
            {"provider_submission_attempts": 0},
            "submission attempts",
        ),
        (
            {"provider_retry_backoff_seconds": float("inf")},
            "retry backoff",
        ),
        (
            {"provider_config_master_key": "dG9vLXNob3J0"},
            "exactly 32 bytes",
        ),
        (
            {"provider_config_master_key": "A" * 42 + "!"},
            "URL-safe base64",
        ),
        (
            {"public_base_url_aliases": ("https://user:secret@legacy.test",)},
            "ENMOTION_PUBLIC_BASE_URL_ALIASES",
        ),
        (
            {"public_base_url_aliases": ("https://legacy.test/path",)},
            "ENMOTION_PUBLIC_BASE_URL_ALIASES",
        ),
    ],
)
def test_unsafe_production_configuration_is_rejected(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        settings(**overrides)


def test_public_base_url_aliases_are_parsed_as_normalized_origins(monkeypatch) -> None:
    monkeypatch.setenv("ENMOTION_SESSION_HMAC_SECRET", "x" * 48)
    monkeypatch.setenv("ENMOTION_PUBLIC_BASE_URL", "https://control.test/")
    monkeypatch.setenv(
        "ENMOTION_PUBLIC_BASE_URL_ALIASES",
        " https://legacy-one.test/,https://legacy-two.test:9443 ",
    )

    configured = Settings.from_env()

    assert configured.public_base_url == "https://control.test"
    assert configured.public_base_url_aliases == (
        "https://legacy-one.test",
        "https://legacy-two.test:9443",
    )
    assert configured.provider_read_timeout_seconds == 900
    assert configured.provider_submission_attempts == 4
