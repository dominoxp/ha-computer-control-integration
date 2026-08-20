# HA Computer Control (hacc)

Home-Assistant-Custom-Integration für [HA Computer Control](https://github.com/dominoxp/ha-computer-control) -
die Windows-App, die einen PC bidirektional mit Home Assistant verbindet.

Diese Integration ist die einzige Schnittstelle zwischen App und HA: kein
Long-Lived Access Token, sondern eine Kopplung per kurzem Einmalcode und
danach ein Geräte-Key, der ausschließlich als Hash in Home Assistant landet.

Das Nachrichtenformat auf dem WebSocket-Kanal steht in [PROTOCOL.md](PROTOCOL.md) -
das ist die Wahrheit für beide Seiten (App und Integration).

> Status: Kopplung, WebSocket-Kanal, echte Entitäten je PC
> (`sensor`/`binary_sensor`/`button`/`select`/`number`/`switch`) und Befehle
> (Entität-Interaktionen plus die Services `hacc.notify`/`hacc.launch`/
> `hacc.set_displays`). Zugriffs-Freigaben folgen in einem weiteren Schritt -
> siehe [planning.md des App-Repos](https://github.com/dominoxp/ha-computer-control/blob/master/planning.md).

## Installation über HACS (Custom Repository)

1. HACS öffnen -> die drei Punkte oben rechts -> **Benutzerdefinierte Repositories**.
2. Als Repository-URL diese Adresse eintragen, Kategorie **Integration** wählen.
3. "HA Computer Control" installieren und Home Assistant neu starten.

## Einrichtung / Kopplung

1. **Einstellungen -> Geräte & Dienste -> Integration hinzufügen** -> "HA Computer
   Control" suchen.
2. Home Assistant zeigt einen kurzen Kopplungscode (8 Zeichen, einmalig
   gültig, läuft nach wenigen Minuten ab).
3. Den Code in der HA Computer Control-App eintragen (Einstellungen ->
   Verbindung -> "Koppeln"). Die App bekommt daraufhin eine Geräte-Id und
   einen Geräte-Key, den nur sie kennt.
4. Neuen Code jederzeit erzeugen: auf der Integration **Konfigurieren**
   anklicken.

In der ConfigEntry steht zu keinem Zeitpunkt der Klartext-Code oder -Key -
Home Assistant speichert ausschließlich Hashes.

## Entwicklung

Alle Qualitäts-Checks (Formatierung, Lint, Tests) laufen über einen Befehl - von
Windows aus aufgerufen, aber unter WSL ausgeführt:

```
check.cmd
check.cmd --fix
```

Grund: Home Assistant Core importiert beim Start bedingungslos das
POSIX-only-Modul `fcntl` und startet unter nativem Windows-Python gar nicht
erst. `check.cmd` ruft deshalb `check.sh` unter WSL (Distribution `Ubuntu`)
auf; `check.sh` legt sich sein eigenes `.venv-wsl` beim ersten Lauf selbst an
(ruff + `pytest-homeassistant-custom-component`, jeweils zur verfügbaren
Python-Version passend - aktuelle Versionen brauchen Python >= 3.13, teils
>= 3.14; unter Ubuntu ggf. per `deadsnakes`-PPA nachinstallieren). WSL selbst
muss vorhanden sein (`wsl --install`), sonst bricht `check.cmd` mit einer
klaren Meldung ab.

Direkt in WSL aufrufen geht genauso: `bash check.sh` / `bash check.sh --fix`.

Wichtig beim manuellen Nacharbeiten in `.venv-wsl`: **nie**
`pip install -e ".[dev]"` (Self-Install dieses Repos) verwenden - der dabei
entstehende `__editable__...`-Eintrag auf `sys.path` bringt Home Assistants
Custom-Component-Scan mit einem `FileNotFoundError` zum Absturz. Nur die
Werkzeuge selbst installieren (`ruff`, `pytest-homeassistant-custom-component`),
nie das eigene Projekt.

Ein einzelner, harmloser Cleanup-Hinweis ist bekannt und wird von `check.sh`
gezielt toleriert (nicht stumm ignoriert - er wird immer mit ausgegeben): Der
strenge Thread-Leak-Check der (Python-3.12-kompatiblen) Testharness meldet
beim ersten `http`-Komponenten-Setup im Testprozess einen aiohttp-internen
`_run_safe_shutdown_loop`-Thread als Fehler - ein Artefakt dieser älteren
Harness-Version, kein Bug in diesem Code, reproduzierbar auch mit einem
einzelnen isolierten Test. Jede andere Testfehlermeldung lässt `check.sh`
weiterhin scheitern.

## Lizenz

MIT
