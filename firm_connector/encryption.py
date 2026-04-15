# =============================================================================
# firm_connector/encryption.py
# AES-256-GCM field-level encryption for sensitive firm data.
#
# SENSITIVE FIELDS:
#   - employees_firm.ssn          — most critical
#   - clients_firm.ssn_or_ein_federal
#   - Any other PII as required
#
# KEY MANAGEMENT (priority order):
#   1. AWS Secrets Manager (ENCRYPTION_SECRET_ARN env var)
#   2. ENCRYPTION_KEY env var (base64-encoded 32 bytes)
#   3. Ephemeral in-process key (dev/test only — warns loudly)
#
# SCHEME:
#   AES-256-GCM with a random 12-byte nonce per encryption call.
#   Ciphertext format: base64(nonce[12] || ciphertext || tag[16])
#   The nonce is prepended so each call produces a different output
#   even for identical plaintexts.
#
# INVARIANTS:
#   - Plaintext NEVER logged, even in DEBUG level
#   - Keys NEVER logged
#   - Encrypted values safe to store in DB and appear in audit logs
#     as "[ENCRYPTED]" placeholder
# =============================================================================

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

_active_key: Optional[bytes] = None   # cached in-process key


def _load_key_from_aws() -> Optional[bytes]:
    """
    Attempt to load the encryption key from AWS Secrets Manager.
    Returns None if boto3 is not installed or ARN is not configured.
    """
    arn = os.environ.get("ENCRYPTION_SECRET_ARN")
    if not arn:
        return None
    try:
        import boto3  # type: ignore[import]
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=arn)
        raw = response.get("SecretString") or base64.b64decode(response["SecretBinary"]).decode()
        key = base64.b64decode(raw)
        if len(key) != 32:
            raise ValueError("AWS Secrets Manager key must be exactly 32 bytes (AES-256)")
        return key
    except ImportError:
        logger.debug("boto3 not installed — skipping AWS Secrets Manager")
        return None
    except Exception as exc:
        logger.error("Failed to load encryption key from AWS Secrets Manager: %s", exc)
        return None


def _load_key_from_env() -> Optional[bytes]:
    """Load key from ENCRYPTION_KEY env var (base64-encoded 32 bytes)."""
    raw = os.environ.get("ENCRYPTION_KEY")
    if not raw:
        return None
    try:
        key = base64.b64decode(raw)
        if len(key) != 32:
            raise ValueError("ENCRYPTION_KEY must decode to exactly 32 bytes for AES-256")
        return key
    except Exception as exc:
        logger.error("Invalid ENCRYPTION_KEY env var: %s", exc)
        return None


def _generate_ephemeral_key() -> bytes:
    """
    Generate a random ephemeral key for dev/test environments.
    Data encrypted with this key is lost when the process restarts.
    """
    logger.warning(
        "SECURITY WARNING: No ENCRYPTION_KEY or ENCRYPTION_SECRET_ARN configured. "
        "Using a random ephemeral encryption key — encrypted data will be UNREADABLE "
        "after process restart. Set ENCRYPTION_KEY for persistence."
    )
    return os.urandom(32)


def get_encryption_key() -> bytes:
    """
    Return the active AES-256 encryption key.

    Resolution order:
      1. Cached in-process key (avoids repeated AWS calls)
      2. AWS Secrets Manager
      3. ENCRYPTION_KEY env var
      4. Ephemeral random key (dev/test only)
    """
    global _active_key
    if _active_key is not None:
        return _active_key

    key = _load_key_from_aws() or _load_key_from_env() or _generate_ephemeral_key()
    _active_key = key
    return key


def rotate_key(new_key_b64: str) -> None:
    """
    Replace the active encryption key (in-process only).
    In production, this is called after updating the AWS secret.

    NOTE: Data encrypted with the old key must be re-encrypted before
    calling this function. Re-encryption is a separate migration task.
    """
    global _active_key
    new_key = base64.b64decode(new_key_b64)
    if len(new_key) != 32:
        raise ValueError("New key must be exactly 32 bytes")
    _active_key = new_key
    logger.info("Encryption key rotated successfully")


# ---------------------------------------------------------------------------
# AES-256-GCM encryption / decryption
# ---------------------------------------------------------------------------

def encrypt_field(plaintext: str, key: Optional[bytes] = None) -> str:
    """
    Encrypt a plaintext string with AES-256-GCM.

    Returns a base64-encoded string: nonce(12) || ciphertext || tag(16).
    Each call produces a different output due to random nonce.

    Args:
        plaintext: The sensitive value to encrypt (SSN, EIN, etc.)
        key:       Optional key override; uses get_encryption_key() if None.

    Raises:
        ValueError: If plaintext is empty.
        RuntimeError: If the cryptography library is not installed.
    """
    if not plaintext:
        raise ValueError("Cannot encrypt empty plaintext")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "The 'cryptography' package is required for field encryption. "
            "Install it with: pip install cryptography"
        )

    active_key = key or get_encryption_key()
    nonce = os.urandom(12)                    # 96-bit random nonce
    aesgcm = AESGCM(active_key)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext_with_tag).decode("ascii")


def decrypt_field(encoded: str, key: Optional[bytes] = None) -> str:
    """
    Decrypt a value produced by encrypt_field().

    Args:
        encoded: The base64-encoded encrypted string.
        key:     Optional key override; uses get_encryption_key() if None.

    Returns:
        The original plaintext string.

    Raises:
        ValueError: If the ciphertext is malformed or authentication fails.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import]
    except ImportError:
        raise RuntimeError("The 'cryptography' package is required for field decryption.")

    try:
        raw = base64.b64decode(encoded)
        if len(raw) < 28:   # 12 nonce + 16 tag minimum
            raise ValueError("Ciphertext too short — likely corrupted")
        nonce, ciphertext_with_tag = raw[:12], raw[12:]
        active_key = key or get_encryption_key()
        aesgcm = AESGCM(active_key)
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        return plaintext_bytes.decode("utf-8")
    except Exception as exc:
        # Re-raise with generic message — do NOT include ciphertext in error
        raise ValueError(f"Decryption failed: {type(exc).__name__}") from exc


def mask_for_log(value: str) -> str:
    """
    Return a safe-to-log representation of a sensitive value.

    Examples:
      "123-45-6789" → "***-**-6789"
      "660123456"   → "*****3456"
    """
    if not value:
        return "[EMPTY]"
    visible = min(4, len(value) // 3)
    return "*" * (len(value) - visible) + value[-visible:]


def is_encrypted(value: str) -> bool:
    """
    Heuristic check: returns True if the value looks like an encrypt_field() output.
    Uses base64 length and decodability as signal — not a cryptographic guarantee.
    """
    try:
        decoded = base64.b64decode(value)
        return len(decoded) >= 28    # minimum: 12 nonce + 1 byte ciphertext + 15 tag
    except Exception:
        return False
