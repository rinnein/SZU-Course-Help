"""Card-key signing and verification.

Version 3 card keys use an Ed25519 signature over a small canonical JSON
payload.  The previous implementation encrypted a non-secret student ID with
a master key embedded in the source code.  This implementation keeps signing
authority in a locally generated private key and needs only the public key for
verification.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

CARDKEY_VERSION = 3
TOKEN_PREFIX = "SZU3"
STUDENT_ID_PATTERN = re.compile(r"^\d{6,12}$")
MAX_CARD_KEY_LENGTH = 2048
_KEY_PAIR_LOCK = threading.Lock()


class KeyManagementError(RuntimeError):
    """Raised when the local signing identity is missing or inconsistent."""


def _key_dir() -> Path:
    configured = os.getenv("COURSE_SELECT_KEY_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _private_key_path() -> Path:
    return _key_dir() / "card_signing_private.pem"


def _public_key_path() -> Path:
    return _key_dir() / "card_signing_public.pem"


def _private_key_passphrase() -> str | None:
    value = os.getenv("COURSE_SELECT_KEY_PASSPHRASE", "")
    return value or None


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        os.chmod(path, mode)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def _load_private_key(path: Path) -> ECC.EccKey:
    passphrase = _private_key_passphrase()
    try:
        key = ECC.import_key(path.read_text(encoding="utf-8"), passphrase=passphrase)
    except (ValueError, IndexError, TypeError) as exc:
        hint = "，请检查 COURSE_SELECT_KEY_PASSPHRASE" if passphrase else ""
        raise KeyManagementError(f"无法读取卡密私钥{hint}") from exc
    if not key.has_private() or key.curve != "Ed25519":
        raise KeyManagementError("卡密私钥格式不正确，必须是 Ed25519 私钥")
    return key


def _load_public_key_object() -> ECC.EccKey | None:
    path = _public_key_path()
    if not path.exists():
        return None
    try:
        key = ECC.import_key(path.read_text(encoding="utf-8"))
    except (ValueError, IndexError, TypeError) as exc:
        raise KeyManagementError("无法读取卡密公钥") from exc
    if key.curve != "Ed25519":
        raise KeyManagementError("卡密公钥格式不正确，必须是 Ed25519 公钥")
    return key.public_key()


def _fingerprint(key: ECC.EccKey) -> str:
    public_der = key.public_key().export_key(format="DER")
    return hashlib.sha256(public_der).hexdigest()[:16]


def _export_private_key(key: ECC.EccKey) -> str:
    passphrase = _private_key_passphrase()
    if passphrase:
        return key.export_key(
            format="PEM",
            passphrase=passphrase,
            protection="PBKDF2WithHMAC-SHA512AndAES256-CBC",
        )
    return key.export_key(format="PEM")


def _get_or_create_key_pair_unlocked() -> ECC.EccKey:
    """Load the Ed25519 signing key, or create a new installation identity."""
    private_path = _private_key_path()
    public_path = _public_key_path()

    if private_path.exists():
        private_key = _load_private_key(private_path)
        expected_public = private_key.public_key()
        stored_public = _load_public_key_object()
        if stored_public is None:
            _atomic_write(public_path, expected_public.export_key(format="PEM"), 0o644)
        elif not hmac.compare_digest(_fingerprint(stored_public), _fingerprint(expected_public)):
            raise KeyManagementError("卡密公私钥不匹配，请恢复原始密钥文件")
        return private_key

    if public_path.exists():
        raise KeyManagementError("检测到卡密公钥但缺少私钥；为避免旧卡密失效，程序不会自动覆盖密钥")

    print("  [安全] 首次运行，正在生成 Ed25519 卡密签名密钥...")
    private_key = ECC.generate(curve="Ed25519")
    _atomic_write(private_path, _export_private_key(private_key), 0o600)
    _atomic_write(public_path, private_key.public_key().export_key(format="PEM"), 0o644)
    print(f"  [安全] 新签名身份已创建，指纹: {_fingerprint(private_key)}")
    if not _private_key_passphrase():
        print("  [安全] 提示: 可设置 COURSE_SELECT_KEY_PASSPHRASE 加密私钥文件")
    return private_key


def get_or_create_key_pair() -> ECC.EccKey:
    """Load or create the signing identity once across concurrent callers."""
    with _KEY_PAIR_LOCK:
        return _get_or_create_key_pair_unlocked()


def load_public_key() -> str | None:
    """Return the local Ed25519 public key in PEM format, if present."""
    path = _public_key_path()
    return path.read_text(encoding="utf-8") if path.exists() else None


def get_public_key_fingerprint() -> str | None:
    """Return the short fingerprint used by issued card keys."""
    key = _load_public_key_object()
    return _fingerprint(key) if key else None


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url data")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if _b64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url data")
    return decoded


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def generate_card_key(student_id: str, private_key: ECC.EccKey | None = None) -> str:
    """Issue a permanent version 3 card key bound to ``student_id``."""
    student_id = str(student_id).strip()
    if not STUDENT_ID_PATTERN.fullmatch(student_id):
        raise ValueError("学号必须是 6 至 12 位数字")

    private_key = private_key or get_or_create_key_pair()
    if not private_key.has_private() or private_key.curve != "Ed25519":
        raise KeyManagementError("签发卡密需要 Ed25519 私钥")

    payload = {
        "iat": int(time.time()),
        "kid": _fingerprint(private_key),
        "nonce": secrets.token_urlsafe(12),
        "sid": student_id,
        "v": CARDKEY_VERSION,
    }
    payload_bytes = _canonical_payload(payload)
    signature = eddsa.new(private_key, "rfc8032").sign(payload_bytes)
    return f"{TOKEN_PREFIX}.{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}"


def verify_card_key(student_id: str, card_key: str) -> bool:
    """Verify a version 3 card key and its student-number binding."""
    student_id = str(student_id).strip()
    card_key = str(card_key).strip()
    if (
        not STUDENT_ID_PATTERN.fullmatch(student_id)
        or not card_key
        or len(card_key) > MAX_CARD_KEY_LENGTH
    ):
        return False

    try:
        prefix, encoded_payload, encoded_signature = card_key.split(".")
        if prefix != TOKEN_PREFIX:
            return False

        payload_bytes = _b64url_decode(encoded_payload)
        signature = _b64url_decode(encoded_signature)
        public_key = _load_public_key_object()
        if public_key is None:
            return False

        eddsa.new(public_key, "rfc8032").verify(payload_bytes, signature)
        payload = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict) or _canonical_payload(payload) != payload_bytes:
            return False

        expected_kid = _fingerprint(public_key)
        return (
            payload.get("v") == CARDKEY_VERSION
            and isinstance(payload.get("iat"), int)
            and payload["iat"] > 0
            and isinstance(payload.get("nonce"), str)
            and 8 <= len(payload["nonce"]) <= 64
            and isinstance(payload.get("sid"), str)
            and hmac.compare_digest(payload["sid"], student_id)
            and isinstance(payload.get("kid"), str)
            and hmac.compare_digest(payload["kid"], expected_kid)
        )
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return False


__all__ = [
    "CARDKEY_VERSION",
    "KeyManagementError",
    "generate_card_key",
    "get_or_create_key_pair",
    "get_public_key_fingerprint",
    "load_public_key",
    "verify_card_key",
]
