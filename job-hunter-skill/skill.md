# job-hunter

Automatisierte Job-Recherche für den EU-Raum. Aggregiert 1.000–5.000 Stellenangebote aus >20 Quellen, dedupliziert, scored und exportiert sie in eine Excel-Datei.

## Wann verwenden

Wenn der User sagt: "job-hunter", "starte die Job-Recherche", "suche Jobs", "job search starten" oder ähnliches.

## Workflow

1. Prüfe ob `~/.claude/skills/job-hunter/config.yaml` existiert und bewerber-spezifisch konfiguriert ist
2. Falls nicht: frage den User nach den wichtigsten Parametern und generiere die config.yaml
3. Stelle sicher dass alle Python-Dependencies installiert sind (`pip install -r requirements.txt`)
4. Führe `python run.py` im Skill-Verzeichnis aus
5. Zeige Progress und Log-Output
6. Am Ende: Pfad zur Excel-Datei anzeigen und Summary der gefundenen Jobs

## Konfiguration

Die Datei `config.yaml` im Skill-Verzeichnis enthält das Bewerber-Profil:
- Skills mit Level (expert/applied/basic)
- Sprachen
- Gehaltsvorstellungen
- Standortpräferenzen
- Arbeitszeit
- Branchen-Filter

## Ausführung

```bash
cd ~/.claude/skills/job-hunter
pip install -r requirements.txt
python run.py [--config config.yaml] [--max-jobs 5000] [--output output/jobs.xlsx] [--resume]
```

## Optionen

- `--config PATH`: Pfad zur config.yaml (default: config.yaml im Skill-Verzeichnis)
- `--max-jobs N`: Maximale Anzahl Jobs (default: 5000)
- `--output PATH`: Output-Pfad für Excel
- `--resume`: Setze unterbrochenen Lauf fort (nutzt gecachte Rohdaten)
- `--sources LIST`: Kommaseparierte Liste von Quellen (default: alle)
- `--top N`: Nur Top-N Jobs verifizieren (default: 100)
- `--no-verify`: Verifizierungs-Schritt überspringen

## Ausgabe-Struktur

```
output/
  jobs_TIMESTAMP.xlsx     ← Haupt-Output mit 6 Sheets
logs/
  run_TIMESTAMP.log       ← Vollständiges Log
data/raw/
  SOURCE_TIMESTAMP.json   ← Rohdaten-Backup pro Quelle
```

## Hinweis für Claude

Starte die Ausführung mit einer kurzen Zusammenfassung der Konfiguration.
Überwache den Progress und berichte bei Fehlern einzelner Quellen (die anderen laufen weiter).
Am Ende: zeige Top-10 Jobs nach Score als Vorschau.
