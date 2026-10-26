"""Argon2id password hashing."""

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

try:
    from argon2.exceptions import InvalidHashError as InvalidHashType
except ImportError:
    from argon2.exceptions import InvalidHash as InvalidHashType

MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashType):
        return False


def dummy_password_hash() -> str:
    """A real Argon2id hash used only so unknown-email logins still verify."""

    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("timing-dummy")
    return _dummy_hash