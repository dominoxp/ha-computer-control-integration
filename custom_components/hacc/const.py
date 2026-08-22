"""Constants for the HA Computer Control integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "hacc"

# Wird bei jedem hello/hello_ok gegeneinander geprüft. Auf 2 seit Step 5.3: die
# state-Nachricht identifiziert Entitäten jetzt über "key" statt "entity_id"
# (siehe PROTOCOL.md) - inkompatibel zum bisherigen Format.
PROTOCOL_VERSION = 2

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
]

DEVICE_MANUFACTURER = "HA Computer Control"

# .format(entry_id). Payload: list[str] der Manifest-Keys ohne lebende Entität -
# Plattformen legen daraus neue Entitäten an (auch wenn der Key schon einmal
# existierte, aber z.B. in HA gelöscht wurde).
SIGNAL_ENTITY_REGISTERED = "hacc_entity_registered_{}"
# .format(entry_id). Kein Payload - stößt nur async_write_ha_state() an, damit
# `available` (liest ConnectionState.connected live) neu ausgewertet wird.
SIGNAL_CONNECTION_STATE = "hacc_connection_state_{}"

# Auf hass.bus gefeuerte Events (Step 5.4) - device_id ist bei allen dabei.
EVENT_COMMAND_RESULT = "hacc_command_result"
EVENT_NOTIFICATION = "hacc_notification"
EVENT_NOTIFICATION_ACTION = "hacc_notification_action"

# Wire-event_type -> HA-Event (siehe PROTOCOL.md, Abschnitt "event").
EVENT_TYPE_MAP = {
    "pc_notification": EVENT_NOTIFICATION,
    "pc_notification_action": EVENT_NOTIFICATION_ACTION,
}

# Services, ein PC-Befehl pro Service statt eines generischen Notausgangs -
# bessere UX im Web-Editor (echte Felder statt einem freien data-Objekt).
SERVICE_NOTIFY = "notify"
SERVICE_LAUNCH = "launch"
SERVICE_SET_DISPLAYS = "set_displays"

DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0
ATTR_DEVICE_ID = "device_id"

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
# Step 5.5: call_service ohne (noch) ausreichende Freigabe (siehe access.py).
ERROR_ACCESS_PENDING = "access_pending"
ERROR_ACCESS_DENIED = "access_denied"

# Deckel für offene (requested) Freigabe-Anfragen zusammen (lesen+schalten) -
# eine kaputte Widget-Konfiguration darf den Options-Flow nicht unbenutzbar
# machen (siehe access.py).
MAX_PENDING_ACCESS_REQUESTS = 50

# Deckel für die targets-Liste einer einzelnen offenen (requested) CallGrant -
# ohne ihn könnte ein Gerät durch call_service-Aufrufe mit vielen
# verschiedenen Ziel-Entity-Ids diese eine Liste unbegrenzt wachsen lassen,
# obwohl MAX_PENDING_ACCESS_REQUESTS nur unterschiedliche Keys deckelt
# (siehe access.py).
MAX_PENDING_CALL_TARGETS = 50

PAIR_PATH = "/api/hacc/pair"
WS_PATH = "/api/hacc/ws"

DATA_DEVICE_ID = "device_id"
DATA_DEVICE_KEY_HASH = "device_key_hash"
DATA_DEVICE_NAME = "device_name"
DATA_PAIRING_CODE_HASH = "pairing_code_hash"
DATA_PAIRING_CODE_EXPIRES_AT = "pairing_code_expires_at"
