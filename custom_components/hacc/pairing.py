"""Pairing-code lifecycle and secret hashing.

Plaintext secrets (pairing code, device key) only ever exist long enough to be
generated and immediately hashed, or to be shown/returned to the caller once.
ConfigEntry.data and hass.data only ever hold hashes.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from homeassistant.util import dt as dt_util

from .connection import DomainData
from .const import (
    DATA_PAIRING_CODE_EXPIRES_AT,
    DATA_PAIRING_CODE_HASH,
    DEVICE_KEY_BYTES,
    PAIR_RATE_LIMIT_MAX_FAILURES,
    PAIR_RATE_LIMIT_WINDOW,
    PAIRING_CODE_ALPHABET,
    PAIRING_CODE_LENGTH,
    PAIRING_CODE_TTL,
)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_pairing_code() -> str:
    """Return a fresh, human-typeable pairing code."""
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def hash_pairing_code(code: str) -> str:
    """Hash a pairing code the same way regardless of casing/whitespace."""
    return _sha256_hex(code.strip().upper())


def generate_device_key() -> str:
    """Return a fresh, high-entropy device key. Guessing it is not the threat model."""
    return secrets.token_urlsafe(DEVICE_KEY_BYTES)


def hash_device_key(key: str) -> str:
    """Hash a device key. Unlike the pairing code, this is case-sensitive."""
    return _sha256_hex(key)


def new_pairing_entry_data() -> tuple[str, dict[str, str]]:
    """Generate a code and the ConfigEntry.data fragment that stores its hash."""
    code = generate_pairing_code()
    expires_at = dt_util.utcnow() + PAIRING_CODE_TTL
    return code, {
        DATA_PAIRING_CODE_HASH: hash_pairing_code(code),
        DATA_PAIRING_CODE_EXPIRES_AT: expires_at.isoformat(),
    }


@dataclass(slots=True)
class PairingCodeState:
    """A pending pairing code as read from one ConfigEntry."""

    entry_id: str
    code_hash: str
    expires_at_iso: str

    def is_expired(self) -> bool:
        expires_at = dt_util.parse_datetime(self.expires_at_iso)
        return expires_at is None or dt_util.utcnow() >= expires_at


def register_pair_failure(domain_data: DomainData) -> None:
    """Record a failed /pair call for the sliding-window rate limit."""
    now = dt_util.utcnow()
    domain_data.pair_failure_times = [
        t for t in domain_data.pair_failure_times if now - t < PAIR_RATE_LIMIT_WINDOW
    ]
    domain_data.pair_failure_times.append(now)


def is_pair_rate_limited(domain_data: DomainData) -> bool:
    """Whether /pair has seen too many failures in the current window."""
    now = dt_util.utcnow()
    domain_data.pair_failure_times = [
        t for t in domain_data.pair_failure_times if now - t < PAIR_RATE_LIMIT_WINDOW
    ]
    return len(domain_data.pair_failure_times) >= PAIR_RATE_LIMIT_MAX_FAILURES
