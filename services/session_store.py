"""Encrypted persistence for the local school session.

The files live below ``COURSE_SELECT_DATA_DIR`` and are only used to restore a
session after a development reload or a normal application restart.  They are
not a replacement for the school's own session expiry rules.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES

from project_paths import data_dir

_MAGIC = b"SZU_SESSION_V1\0"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_TAG_BYTES = 16


class SessionStoreError(RuntimeError):
    """Raised when the encrypted local session cannot be read or written."""


def _key_path() -> Path:
    return data_dir() / "session_state.key"


def _state_path() -> Path:
    return data_dir() / "session_state.bin"


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        os.chmod(path, mode)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temporary_name)
        raise


def _load_or_create_key() -> bytes:
    path = _key_path()
    try:
        if path.exists():
            key = path.read_bytes()
            if len(key) != _KEY_BYTES:
                raise SessionStoreError("本地会话密钥长度异常")
            return key
        key = os.urandom(_KEY_BYTES)
        _atomic_write(path, key)
        return key
    except OSError as exc:
        raise SessionStoreError("无法访问本地会话密钥") from exc


def save(payload: dict[str, Any]) -> None:
    """Encrypt and atomically persist the current session payload."""
    try:
        plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        nonce = os.urandom(_NONCE_BYTES)
        cipher = AES.new(_load_or_create_key(), AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        _atomic_write(_state_path(), _MAGIC + nonce + tag + ciphertext)
    except (OSError, TypeError, ValueError) as exc:
        raise SessionStoreError("无法保存本地会话") from exc


def load() -> dict[str, Any] | None:
    """Load and authenticate the persisted session, if one exists."""
    path = _state_path()
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        minimum_size = len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES
        if len(raw) <= minimum_size or not raw.startswith(_MAGIC):
            raise SessionStoreError("本地会话文件格式异常")
        offset = len(_MAGIC)
        nonce = raw[offset : offset + _NONCE_BYTES]
        offset += _NONCE_BYTES
        tag = raw[offset : offset + _TAG_BYTES]
        ciphertext = raw[offset + _TAG_BYTES :]
        cipher = AES.new(_load_or_create_key(), AES.MODE_GCM, nonce=nonce)
        payload = json.loads(cipher.decrypt_and_verify(ciphertext, tag).decode())
        if not isinstance(payload, dict):
            raise SessionStoreError("本地会话内容异常")
        return payload
    except SessionStoreError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionStoreError("本地会话校验失败") from exc


def clear() -> None:
    """Remove only the encrypted session payload; retain its local key."""
    with contextlib.suppress(FileNotFoundError):
        _state_path().unlink()


__all__ = ["SessionStoreError", "clear", "load", "save"]
