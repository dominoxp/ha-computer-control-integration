"""Constants for the HA Computer Control integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "hacc"

# Wird bei jedem hello/hello_ok gegeneinander geprüft (Step 5.2 füllt das Protokoll).
PROTOCOL_VERSION = 1

# Kopplungscode: kurz und abtippbar, aber ohne leicht verwechselbare Zeichen.
PAIRING_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # ohne 0/O/1/I/L
PAIRING_CODE_LENGTH = 8
PAIRING_CODE_TTL = timedelta(minutes=10)

# Globale Rate-Grenze für /api/hacc/pair: schützt alle gerade offenen Codes
# gemeinsam gegen Raten, ohne dass ein Angreifer gezielt den Code eines anderen
# Geräts durch Falschraten verbrennen kann.
PAIR_RATE_LIMIT_WINDOW = timedelta(minutes=5)
PAIR_RATE_LIMIT_MAX_FAILURES = 10

DEVICE_KEY_BYTES = 32

HELLO_TIMEOUT_SECONDS = 10
WS_HEARTBEAT_SECONDS = 30

ERROR_INVALID_OR_EXPIRED = "invalid_or_expired"
ERROR_PROTOCOL_VERSION = "protocol_version_mismatch"
ERROR_INVALID_MESSAGE = "invalid_message"

PAIR_PATH = "/api/hacc/pair"
WS_PATH = "/api/hacc/ws"

DATA_DEVICE_ID = "device_id"
DATA_DEVICE_KEY_HASH = "device_key_hash"
DATA_DEVICE_NAME = "device_name"
DATA_PAIRING_CODE_HASH = "pairing_code_hash"
DATA_PAIRING_CODE_EXPIRES_AT = "pairing_code_expires_at"
