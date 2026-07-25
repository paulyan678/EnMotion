from __future__ import annotations

import pytest

from src.apps.server.security import (
    CredentialValidationError,
    digest_token,
    generate_opaque_token,
    hash_password,
    normalize_username,
    verify_password,
)


def test_argon2id_password_hashing_and_verification():
    encoded = hash_password("a sufficiently long password")
    assert encoded.startswith("$argon2id$")
    assert "a sufficiently long password" not in encoded
    assert verify_password(encoded, "a sufficiently long password") is True
    assert verify_password(encoded, "incorrect password") is False


def test_password_policy_rejects_short_password():
    with pytest.raises(CredentialValidationError, match="at least 12"):
        hash_password("too-short")


def test_username_normalization_and_validation():
    assert normalize_username("  Alice.Example ") == "alice.example"
    with pytest.raises(CredentialValidationError):
        normalize_username("not allowed!")


def test_opaque_tokens_are_random_and_only_stable_after_digest():
    first = generate_opaque_token()
    second = generate_opaque_token()
    assert first != second
    assert len(first) >= 40
    assert digest_token(first, "x" * 32) == digest_token(first, "x" * 32)
    assert digest_token(first, "x" * 32) != digest_token(second, "x" * 32)
