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
            {"provider_config_master_key": "dG9vLXNob3J0"},
            "exactly 32 bytes",
        ),
        (
            {"provider_config_master_key": "A" * 42 + "!"},
            "URL-safe base64",
        ),
    ],
)
def test_unsafe_production_configuration_is_rejected(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        settings(**overrides)
