# CLAUDE.md

Home-Assistant-Custom-Integration `hacc` (Windows-App-Gegenstück). Die Details stehen
in den Docs - **vor Änderungen lesen, nicht raten**:

- [README.md](README.md) - Überblick, Kopplung, Umstieg vom Token-Weg, Abschnitt
  **Entwicklung** (Checks/Tests, WSL-Setup, bekannte Stolpersteine).
- [PROTOCOL.md](PROTOCOL.md) - Nachrichtenformat auf dem WebSocket-Kanal. Ist die
  Wahrheit für App *und* Integration; Änderungen an Wire-Feldern zuerst dort.

## Tests und Checks

- Immer `.\check.cmd --fix` direkt in PowerShell ausführen (kapselt WSL intern, ca. 90 s). Nie
  `pytest` direkt unter Windows starten: HA Core importiert `fcntl` und läuft dort
  nicht (`ModuleNotFoundError: No module named 'fcntl'`). Siehe README, "Entwicklung".
- Nicht über einen `cmd /c`-Wrapper aufrufen und die Ausgabe nicht per
  Select-Object/head/tail o.ä. kürzen - die volle Ausgabe lesen.
- In `.venv-wsl` nie `pip install -e ".[dev]"` ausführen (bricht den Custom-Component-Scan).
- Nach jeder Änderung an `custom_components/hacc/` `check.cmd` laufen lassen und bei
  Bugfixes einen Regressionstest in `tests/` ergänzen.

## Git

- Commit-Nachrichten **ohne** `Co-Authored-By`-Zeile (und ohne sonstige Attribution) schreiben.

## Konventionen

- Kommentare, Log-Meldungen und Doku auf Deutsch, Stil der umgebenden Dateien übernehmen.
- Entitäten entstehen dynamisch aus `register` (siehe [sensor.py](custom_components/hacc/sensor.py),
  [entity.py](custom_components/hacc/entity.py)); Zustand kommt per Push als **String**
  über `state`-Nachrichten.
- Wire-Strings müssen vor dem Setzen von `native_value` zum Typ der `device_class`
  passen: `timestamp` braucht ein zeitzonenbehaftetes `datetime`, `date` ein `date`
  (sonst wirft HA beim Zustandsschreiben `ValueError: Invalid datetime`). Das gilt
  auch für den Restore-Pfad (`_async_restore_last_state`).
- Unbekannte oder kaputte Wire-Werte (device_class, state_class, Zeitstempel) dürfen
  eine Entität nie crashen lassen - tolerant parsen und loggen (siehe `parse_enum`).
- Schreibende dispatcher-Handler brauchen `@callback`, sonst laufen sie im Executor-Thread.
- Keine Klartext-Codes/-Keys loggen oder in der ConfigEntry speichern - nur Hashes.
