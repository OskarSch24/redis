#!/usr/bin/env python3
"""
Job-Hunter Skill Installer
Legt alle Skill-Dateien unter ~/.claude/skills/job-hunter/ an.

Ausführen mit:
    python3 install.py            # Neuinstallation (bricht ab, wenn schon installiert)
    python3 install.py --force    # Update über bestehende Installation
                                  # (config.yaml wird NIE überschrieben → config.yaml.new)
"""

import base64
import json
import os
import sys
from pathlib import Path


def main():
    force = "--force" in sys.argv

    script_dir = Path(__file__).parent.resolve()
    manifest = script_dir / "install.json"

    if not manifest.exists():
        print("ERROR: install.json nicht gefunden. Bitte das komplette job-hunter-skill/ Verzeichnis kopieren.")
        sys.exit(1)

    with open(manifest, encoding="utf-8") as f:
        files = json.load(f)

    target = Path.home() / ".claude" / "skills" / "job-hunter"

    if (target / "run.py").exists() and not force:
        print(f"ABBRUCH: Unter {target} existiert bereits eine Installation.")
        print("Ein Überschreiben würde lokale Anpassungen (Adapter, Config, Token) zerstören.")
        print("Für ein bewusstes Update: python3 install.py --force")
        print("(config.yaml bleibt dabei unangetastet und landet als config.yaml.new daneben.)")
        sys.exit(2)

    print(f"Installiere Job-Hunter Skill nach: {target}")
    print(f"Anzahl Dateien: {len(files)}")
    print()

    for rel_path, encoded in files.items():
        dest = target / rel_path
        # Bestehende config.yaml (Profil + Token) niemals überschreiben
        if rel_path == "config.yaml" and dest.exists():
            dest = target / "config.yaml.new"
            print(f"  ⚠ config.yaml existiert — schreibe stattdessen {dest.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(encoded))
        print(f"  ✓ {rel_path}")

    # Create runtime directories
    for d in ["logs", "output", "data/raw"]:
        (target / d).mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 50)
    print("Installation abgeschlossen!")
    print()
    print("Nächste Schritte:")
    print(f"  1. cd {target}")
    print("  2. pip install -r requirements.txt")
    print("  3. nano config.yaml   ← Profil anpassen")
    print("  4. python run.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
