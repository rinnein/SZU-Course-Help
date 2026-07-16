"""Compatibility wrapper for the school's legacy password protocol."""

from __future__ import annotations

from cus_base64 import CustomBase64
from desencode import str_enc

_SCHOOL_DES_KEYS = ("this", "password", "is")
_BASE64_ENCODER = CustomBase64()


def encrypt_school_password(password: str) -> str:
    """Return the exact ``loginPwd`` value expected by the school endpoint."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    password_hash = str_enc(password, *_SCHOOL_DES_KEYS)
    return _BASE64_ENCODER.encode(password_hash)


__all__ = ["encrypt_school_password"]
