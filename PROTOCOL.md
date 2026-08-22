# hacc-Protokoll

Das Nachrichtenformat zwischen der Windows-App (`ha-computer-control`,
`hacc.core.ha_client`) und dieser Integration (`/api/hacc/ws`). Ab Step 5.2
ist diese Datei die Wahrheit für beide Seiten - eine Änderung hier zieht eine
passende Änderung in beiden Repos nach sich.

## Transport

- Ein Gerät koppelt sich einmalig über `POST /api/hacc/pair` (Kopplungs-Code
  gegen Geräte-Id/Geräte-Key, siehe README) und verbindet sich danach per
  WebSocket mit `GET /api/hacc/ws?device_id=<id>`, den Geräte-Key im
  `Authorization: Bearer <key>`-Header des Upgrade-Requests. Ein Request ohne diesen Header wird mit 401
  abgelehnt, auch mit einem `device_key`-Query-Parameter.
  Beide Endpunkte sind `requires_auth = False` - die Prüfung übernimmt der
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

| Richtung | Nachricht      | Zweck                                                         | Status                                                                   |
| -------- | -------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------ |
| PC → HA  | `hello`        | Protokollversion, Gerätename, App- und OS-Version             | lebt seit 5.1                                                            |
| HA → PC  | `hello_ok`     | Geräte-Id, HA-Version, Protokollversion                       | lebt seit 5.1                                                            |
| HA → PC  | `error`        | Ablehnung mit `code` und `message`                            | lebt seit 5.1                                                            |
| PC → HA  | `register`     | vollständiges Manifest der eigenen Entitäten                  | lebt seit 5.3                                                            |
| PC → HA  | `state`        | Zustandsänderungen (Sammelnachricht möglich)                  | lebt seit 5.3                                                            |
| PC → HA  | `event`        | Meldungen an HA (Notification, Aktion)                        | lebt seit 5.4 (HA wertet `pc_notification`/`pc_notification_action` aus) |
| PC → HA  | `call_service` | Service-Aufruf mit Antwort                                    | lebt seit 5.2 (Client) / 5.5 (HA antwortet, inkl. Freigabe-Prüfung)      |
| HA → PC  | `command`      | auszuführender Befehl mit Korrelations-Id                     | lebt seit 5.4                                                            |
| PC → HA  | `result`       | Ausgang eines Befehls (`command`) oder `call_service`-Aufrufs | lebt seit 5.4 (`command`) / 5.5 (`call_service`)                         |
| PC → HA  | `subscribe`    | gewünschte HA-Entitäten                                       | lebt seit 5.5                                                            |
| HA → PC  | `entity`       | Zustand einer abonnierten, freigegebenen HA-Entität           | lebt seit 5.5                                                            |
| PC → HA  | `catalog`      | Anfrage: freigegebene Entitäten/Services zur Auswahl          | lebt seit 5.5                                                            |
| HA → PC  | `catalog`      | Antwort auf obige Anfrage                                     | lebt seit 5.5                                                            |
| HA → PC  | `access`       | aktueller Stand der Freigaben (inkl. Such-Modus)              | lebt seit 5.5                                                            |

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
{
  "type": "hello_ok",
  "protocol_version": 2,
  "ha_version": "2024.7.0",
  "device_id": "a1b2…"
}
```

### `error` (HA → PC)

```json
{
  "type": "error",
  "id": null,
  "code": "protocol_version_mismatch",
  "message": "HA erwartet Protokollversion 2."
}
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
      "attributes": {},
      "min": null,
      "max": null,
      "step": null,
      "command": null,
      "command_data": {},
      "value_field": "value"
    }
  ]
}
```

Seit Step 5.4 kennt `domain` neben `sensor`/`binary_sensor` auch `button`,
`select`, `number` und `switch` - die "Art" einer Entität ist einfach ihre HA-
Plattform, kein eigenes Feld. Die sechs neuen Felder gelten nur für diese vier
bedienbaren Domains: `min`/`max`/`step` beschreiben den Wertebereich eines
`number`-Reglers; `command` nennt den Wire-Befehl (siehe unten), den eine
Interaktion (Knopfdruck, Auswahl, Regler, Schalter) auslöst - `null` heisst
reine Anzeige-Entität; `command_data` sind feste Zusatzfelder, die jede
Interaktion mitschickt (z.B. `{"direction": "output"}`, um zwischen Audio-
Ausgabe und -Eingabe zu unterscheiden); `value_field` nennt das Datenfeld, unter
dem der vom Nutzer gewählte Wert im `command` landet (`device` bei `set_audio`,
`level` bei `set_volume`, `mute` bei `set_mute`, `profile` bei `set_displays`).
Bei `select` reisen die wählbaren Optionen **nicht** über `register` (sie
ändern sich zur Laufzeit), sondern als dynamisches `state`-Attribut
`options` (siehe unten).

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
  "states": [{ "key": "heartbeat", "state": "ok", "attributes": {} }]
}
```

Für eine `select`-Entität steht die aktuell wählbare Optionsliste unter dem
Attribut-Schlüssel `options` (z.B. `{"options": ["Kopfhörer", "Monitor"]}`) -
dynamisch statt in `register`, weil sich z.B. angeschlossene Audiogeräte oder
gespeicherte Monitor-Profile zur Laufzeit ändern.

### `event` (PC → HA)

```json
{
  "type": "event",
  "event_type": "pc_notification",
  "data": { "title": "Download fertig" }
}
```

HA übersetzt bekannte `event_type`-Werte 1:1 in eigene Events auf `hass.bus`,
mit `device_id` ergänzt: `pc_notification` → `hacc_notification`,
`pc_notification_action` → `hacc_notification_action`. Unbekannte
`event_type`-Werte werden geloggt und ignoriert (Vorwärtskompatibilität, wie
bei `register`/`state`).

### `command` (HA → PC) / `result` (PC → HA)

Ausgelöst durch eine Interaktion mit einer bedienbaren Entität (`button`
gedrückt, `select`/`number`/`switch` geändert) oder durch einen der
zweckgebundenen Services (`hacc.notify`, `hacc.launch`, `hacc.set_displays`).

```json
{
  "type": "command",
  "id": 12,
  "command": "set_volume",
  "data": { "direction": "output", "level": 30 }
}
```

```json
{ "type": "result", "id": 12, "success": true, "error": null }
```

Bei Ablehnung: `"success": false, "error": { "code": "cancelled" | "failed" | "<reason>", "message": "..." }`.
`error.code` ist entweder `cancelled` (Nutzer hat das Countdown-Popup
abgebrochen) oder der eigentliche Fehlergrund - beides wird von der Integration
zu `hacc_command_result` (`result: executed | cancelled | failed`, `reason`)
zusammengefasst und auf `hass.bus` gefeuert, unabhängig davon, ob noch ein
Aufrufer (Entität oder Service) darauf wartet.

### `call_service` (PC → HA) / `result` (HA → PC)

```json
{
  "type": "call_service",
  "id": 7,
  "domain": "switch",
  "service": "turn_on",
  "service_data": { "brightness_pct": 60 },
  "target": { "entity_id": "light.buero" }
}
```

```json
{ "type": "result", "id": 7, "success": true, "error": null }
```

Bei Ablehnung: `"success": false, "error": { "code": "...", "message": "..." }`.
Dieselbe Form wie beim `command`/`result` oben - eine Nachricht mit `id`, eine
Antwort mit derselben `id`, in dieser Richtung aber PC-initiiert statt
HA-initiiert. Zwei `error.code`-Werte sind hier spezifisch für fehlende
Freigabe (siehe Abschnitt "Zugriffs-Freigaben" unten): `access_pending`
(noch keine Entscheidung in HA) und `access_denied` (abgelehnt) - beide
kommen sofort zurück, kein Timeout nötig, weil die Ablehnung schon feststeht.

## Zugriffs-Freigaben (Step 5.5)

Der PC darf per `subscribe`/`call_service` grundsätzlich um jede fremde
HA-Entität/jeden Service bitten - was tatsächlich fließt, entscheidet
ausschließlich HA (Options-Flow der Integration). Drei Zustände:
`granted`, `requested`, `denied`; kein Eintrag heißt "nie gefragt". Ein
abgelehnter Eintrag wird nie automatisch erneut angefragt - nur ein
**Widerruf** (der Eintrag verschwindet ganz, siehe Options-Flow) macht eine
künftige Anfrage wieder möglich.

### `subscribe` (PC → HA)

```json
{ "type": "subscribe", "entities": ["sensor.drucker_status", "light.buero"] }
```

Ersetzt den gesamten Wunsch bei jedem Aufruf (kein Diff, wie `register`) -
wird bei jedem (Re-)Connect direkt nach `register`/Ist-Stand neu geschickt
(HAs Verbindungs-Status ist nicht persistiert) und zusätzlich sofort, wenn
sich die gewünschte Menge zur Laufzeit ändert. Für jede noch nicht
freigegebene Entität legt HA automatisch eine `requested`-Anfrage an
(gedeckelt, dedupliziert - siehe unten).

### `entity` (HA → PC)

```json
{
  "type": "entity",
  "entity_id": "sensor.drucker_status",
  "state": "printing",
  "attributes": { "friendly_name": "Drucker Status" },
  "last_updated": 1699999999.0
}
```

Eine Nachricht pro geänderter Entität, nur für `read`-Status `granted`. Für
alles andere bleibt sie schlicht aus - kein Fake-Zustand; der Grund fürs
Fehlen kommt über `access` (App zeigt "wartet auf Freigabe" statt eines
stillen Nichts). Ein Widerruf beendet das Abo serverseitig sofort.

### `catalog` (Anfrage PC → HA, Antwort HA → PC)

```json
{ "type": "catalog", "id": 9 }
```

```json
{
  "type": "catalog",
  "id": 9,
  "entities": [
    {
      "entity_id": "sensor.drucker_status",
      "state": "printing",
      "attributes": {},
      "last_updated": 1699999999.0
    }
  ],
  "services": [
    {
      "domain": "light",
      "service": "turn_on",
      "name": "Einschalten",
      "description": "..."
    }
  ],
  "discoverable_entities": [{ "entity_id": "light.buero", "name": "Büro" }],
  "discoverable_services": [
    { "domain": "light", "service": "turn_on", "name": "Einschalten" }
  ]
}
```

Ersetzt `GET /api/states`/`GET /api/services` (entfallen ersatzlos).
`entities`/`services` sind ausschließlich bereits `granted` - das ist die
Auswahlliste in den App-Einstellungen (`ui/settings/pickers.py`,
`ui/settings/ha_catalog.py`), keine Möglichkeit, darüber etwas Neues
anzufragen. `discoverable_entities`/`discoverable_services` sind **name-only**
(kein `state`, keine Attribute außer dem Namen, keine `description`) und nur
enthalten, wenn der Nutzer für dieses Gerät im Options-Flow den Such-Modus
angeschaltet hat (Default aus) - reine Bezeichner zum Wiedererkennen des
richtigen `entity_id`/`domain.service`, keine Live-Daten. Lesen eines echten
Zustands bleibt immer an `granted` gebunden.

### `access` (HA → PC)

```json
{
  "type": "access",
  "read": { "sensor.drucker_status": "granted", "light.buero": "requested" },
  "call": { "light.turn_on": "granted", "switch.turn_on": "denied" },
  "discovery": false
}
```

Immer die volle aktuelle Tabelle dieses Geräts (keine Diffs) - geschickt nach
jedem verarbeiteten `subscribe`/`call_service` und nach jeder Options-Flow-
Änderung an einem gerade verbundenen Gerät (Freigaben **und** Such-Modus).
`discovery` spiegelt den aktuellen Stand des Such-Modus-Schalters.

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
