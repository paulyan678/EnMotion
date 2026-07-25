"""Password and session-secret primitives."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024

# OWASP's minimum Argon2id profile: 19 MiB, two iterations, one lane.
PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


class CredentialValidationError(ValueError):
    pass


def normalize_username(username: str) -> str:
    value = username.strip()
    if not USERNAME_PATTERN.fullmatch(value):
        raise CredentialValidationError(
            "Username must be 3-64 characters and contain only letters, numbers, ., _, or -"
        )
    return value.casefold()


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise CredentialValidationError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise CredentialValidationError(
            f"Password must not exceed {PASSWORD_MAX_LENGTH} characters"
        )


def hash_password(password: str) -> str:
    validate_password(password)
    return str(PASSWORD_HASHER.hash(password))


def verify_password(password_hash: str, candidate: str) -> bool:
    if len(candidate) > PASSWORD_MAX_LENGTH:
        return False
    try:
        return bool(PASSWORD_HASHER.verify(password_hash, candidate))
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return bool(PASSWORD_HASHER.check_needs_rehash(password_hash))
    except InvalidHashError:
        return True


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def digest_token(token: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
