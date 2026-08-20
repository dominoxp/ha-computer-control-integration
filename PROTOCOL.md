# hacc-Protokoll

Das Nachrichtenformat zwischen der Windows-App (`ha-computer-control`,
`hacc.core.ha_client`) und dieser Integration (`/api/hacc/ws`). Ab Step 5.2
ist diese Datei die Wahrheit für beide Seiten - eine Änderung hier zieht eine
passende Änderung in beiden Repos nach sich.

## Transport

- Ein Gerät koppelt sich einmalig über `POST /api/hacc/pair` (Kopplungs-Code
  gegen Geräte-Id/Geräte-Key, siehe README) und verbindet sich danach per
  WebSocket mit `GET /api/hacc/ws?device_id=<id>&device_key=<key>`. Beide
  Endpunkte sind `requires_auth = False` - die Prüfung übernimmt der
  Geräte-Key selbst (`hmac.compare_digest` gegen den gespeicherten Hash).
- Nach dem Upgrade ist jede Nachricht ein JSON-Text-Frame mit einem Feld
  `type`. Anfrage/Antwort-Nachrichten tragen zusätzlich eine vom Client
  vergebene `id` (aufsteigende Ganzzahl); die Antwort trägt dieselbe `id`
  zurück. Alle übrigen Nachrichten sind Fire-and-Forget - dafür gibt es keine
  Anwendungs-Quittung.
- `PROTOCOL_VERSION = 2` steht als Literal auf beiden Seiten (App:
  `hacc.core.ha_client.PROTOCOL_VERSION`, Integration:
  `custom_components/hacc/const.py`). Ändert sich das Format inkompatibel,
  steigt die Zahl auf beiden Seiten gemeinsam - zuletzt in Step 5.3, als
  `state` von `entity_id` auf `key` umgestellt wurde.
- Der WebSocket-Ping (aiohttp `heartbeat=`/`autoping=True`) hält die
  Verbindung am Leben; er ist unabhängig von den Nachrichten unten.

## Nachrichtenübersicht

| Richtung | Nachricht      | Zweck                                              | Status |
| -------- | -------------- | --------------------------------------------------- | ------ |
| PC → HA  | `hello`        | Protokollversion, Gerätename, App- und OS-Version    | lebt seit 5.1 |
| HA → PC  | `hello_ok`     | Geräte-Id, HA-Version, Protokollversion              | lebt seit 5.1 |
| HA → PC  | `error`        | Ablehnung mit `code` und `message`                   | lebt seit 5.1 |
| PC → HA  | `register`     | vollständiges Manifest der eigenen Entitäten         | lebt seit 5.3 |
| PC → HA  | `state`        | Zustandsänderungen (Sammelnachricht möglich)         | lebt seit 5.3 |
| PC → HA  | `event`        | Meldungen an HA (Notification, Aktion)               | wird seit 5.2 gesendet, HA wertet noch nicht aus (5.3+) |
| PC → HA  | `call_service` | Service-Aufruf mit Antwort                           | Client sendet seit 5.2, HA antwortet erst ab 5.5 (bis dahin Timeout) |
| HA → PC  | `result`       | Ausgang eines Befehls oder `call_service`-Aufrufs    | spezifiziert, noch nicht gesendet (5.4/5.5) |
| PC → HA  | `subscribe`    | gewünschte HA-Entitäten                              | spezifiziert, noch nicht implementiert (5.5) |
| HA → PC  | `entity`       | Zustand einer abonnierten HA-Entität                 | spezifiziert, noch nicht implementiert (5.5) |
| PC → HA  | `catalog`      | verfügbare Entitäten/Services zur Auswahl            | spezifiziert, noch nicht implementiert (5.5) |
| HA → PC  | `command`      | auszuführender Befehl mit Korrelations-Id             | spezifiziert, noch nicht implementiert (5.4) |
| HA → PC  | `access`       | aktueller Stand der Freigaben                        | spezifiziert, noch nicht implementiert (5.5) |

## Payloads

### `hello` (PC → HA)

```json
{
  "type": "hello",
  "protocol_version": 2,
  "device_name": "buero_pc",
  "app_version": "0.5.0",
  "os_version": "Windows-11-10.0.22631"
}
```

Trägt keine `id` - die Antwort ist die nächste (und einzige) Nachricht auf
dieser frischen Verbindung.

### `hello_ok` (HA → PC)

```json
{ "type": "hello_ok", "protocol_version": 2, "ha_version": "2024.7.0", "device_id": "a1b2…" }
```

### `error` (HA → PC)

```json
{ "type": "error", "id": null, "code": "protocol_version_mismatch", "message": "HA erwartet Protokollversion 2." }
```

`id` ist `null`, solange der Fehler die `hello`-Nachricht selbst betrifft
(sie trägt keine eigene `id`); bei einer abgelehnten Anfrage-Nachricht trägt
`error` deren `id`. Bekannte `code`-Werte bisher: `invalid_message`,
`protocol_version_mismatch`.

### `register` (PC → HA)

Einmal direkt nach `hello_ok`, danach erneut bei jedem Reconnect (voller
Ist-Stand, kein Diff). Ein Eintrag pro Entität, die dieser PC führt - inhaltlich
deckungsgleich mit `hacc.core.entities.EntityDef`. `attributes` sind statische
Extra-Attribute der Definition (selten genutzt); `icon`/`unit`/`device_class`/
`state_class` und der aus `device_name` + `name` gebildete Anzeigename werden zu
echten HA-Entity-Eigenschaften - sie reisen deshalb nur hier, nicht mehr über
`state`.

```json
{
  "type": "register",
  "device_name": "buero_pc",
  "entities": [
    {
      "key": "heartbeat",
      "name": "Heartbeat",
      "domain": "sensor",
      "icon": "mdi:heart-pulse",
      "unit": null,
      "device_class": null,
      "state_class": null,
      "attributes": {}
    }
  ]
}
```

HA legt daraus ein Gerät je PC an (`identifiers = (DOMAIN, device_id)`,
Hersteller/Modell/Software-Version aus `hello`) und pro Eintrag eine Entität mit
`unique_id = f"{device_id}_{key}"`. Die **entity_id** überlässt HA vollständig
sich selbst (Standard-Ableitung aus Gerätename + `name`, initial an `key`
ausgerichtet) - sie darf im HA-Frontend umbenannt werden, ohne dass ein
späteres `register`/`state` das rückgängig macht. `key` ist also die stabile
Wire-Identität, `entity_id` gehört HA und dem Nutzer. Ein Key, den ein neues
`register` nicht mehr nennt, wird aus der Entity-Registry entfernt (Modul
abgeschaltet, überwachte App entfernt); ein Verbindungsabbruch allein löscht
nichts, nur `available` wird `false`.

### `state` (PC → HA)

Einmal mit dem Ist-Stand direkt nach `register`, danach bei jeder Änderung.
Immer eine Liste, auch bei genau einem Eintrag - eine Sammelnachricht für
mehrere gleichzeitige Änderungen ist zulässig, aber nicht vorgeschrieben.
Identifiziert wird über `key` (siehe oben), nicht über `entity_id` - der wäre
nach einer Umbenennung in HA nicht mehr aktuell. `attributes` enthält nur noch
echte Laufzeitwerte (z.B. `pid` einer laufenden App), keine der Basisfelder aus
`register`.

```json
{
  "type": "state",
  "states": [
    { "key": "heartbeat", "state": "ok", "attributes": {} }
  ]
}
```

### `event` (PC → HA)

```json
{ "type": "event", "event_type": "pc_notification", "data": { "title": "Download fertig" } }
```

### `call_service` (PC → HA) / `result` (HA → PC)

```json
{ "type": "call_service", "id": 7, "domain": "switch", "service": "turn_on", "service_data": { "brightness_pct": 60 }, "target": { "entity_id": "light.buero" } }
```

```json
{ "type": "result", "id": 7, "success": true, "error": null }
```

Bei Ablehnung: `"success": false, "error": { "code": "...", "message": "..." }`.
Dieselbe Form ist für die spätere `command`/`result`-Korrelation (Step 5.4)
vorgesehen - eine Nachricht mit `id`, eine Antwort mit derselben `id`.

## Fehlerbehandlung

- `requires_auth = False` heißt: beide Endpunkte behandeln jede Eingabe als
  feindlich. Kein Geräte-Key und kein Kopplungs-Code landet im Log; Fehler
  von `/api/hacc/pair` sind absichtlich generisch (`invalid_or_expired`) -
  sie verraten nicht, ob ein Code nie existierte, schon benutzt oder nur
  abgelaufen ist.
- Ein WebSocket-Upgrade mit falschem oder fehlendem Geräte-Key bekommt HTTP
  401, bevor HA irgendetwas anlegt.
- Passt die Protokollversion nicht, antwortet HA mit `error` und schließt die
  Verbindung; der Client zeigt das als eigenen Verbindungszustand
  (`pairing_failed`) statt es mit einem abgelehnten Geräte-Key zu verwechseln.
