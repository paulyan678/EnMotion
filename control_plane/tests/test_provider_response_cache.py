from __future__ import annotations

import os
import time

from app.services.provider_response_cache import ProviderResponseCache


def cache(tmp_path, *, ttl_seconds: int = 60) -> ProviderResponseCache:
    return ProviderResponseCache(
        database_url=f"sqlite:///{tmp_path / 'control.db'}",
        secret="test-session-secret-" + "x" * 40,
        ttl_seconds=ttl_seconds,
        max_content_bytes=1024 * 1024,
    )


def test_provider_response_cache_is_encrypted_owner_only_and_replayable(tmp_path) -> None:
    response_cache = cache(tmp_path)
    usage_id = "usage-cache-001"
    content = b'{"data":[{"b64_json":"aW1hZ2U="}]}'

    response_cache.store(
        usage_id,
        status_code=200,
        headers={"Content-Type": "application/json", "Authorization": "must-not-persist"},
        content=content,
    )

    path = response_cache.root / f"{usage_id}.bin"
    encrypted = path.read_bytes()
    assert content not in encrypted
    assert b"must-not-persist" not in encrypted
    assert path.stat().st_mode & 0o777 == 0o600
    restored = response_cache.load(usage_id)
    assert restored is not None
    assert restored.status_code == 200
    assert restored.headers == {"Content-Type": "application/json"}
    assert restored.content == content


def test_provider_response_cache_discards_tampered_and_expired_entries(tmp_path) -> None:
    response_cache = cache(tmp_path, ttl_seconds=1)
    response_cache.store(
        "usage-cache-tampered",
        status_code=200,
        headers={"Content-Type": "application/json"},
        content=b'{"data":[{"b64_json":"aW1hZ2U="}]}',
    )
    tampered = response_cache.root / "usage-cache-tampered.bin"
    tampered.write_bytes(tampered.read_bytes()[:-1] + b"x")
    assert response_cache.load("usage-cache-tampered") is None
    assert not tampered.exists()

    response_cache.store(
        "usage-cache-expired",
        status_code=200,
        headers={"Content-Type": "application/json"},
        content=b'{"data":[{"b64_json":"aW1hZ2U="}]}',
    )
    expired = response_cache.root / "usage-cache-expired.bin"
    old = time.time() - 5
    os.utime(expired, (old, old))
    assert response_cache.prune() == 1
    assert response_cache.load("usage-cache-expired") is None
