#!/usr/bin/env python3
"""
Job-Hunter Skill Installer
Legt alle Skill-Dateien unter ~/.claude/skills/job-hunter/ an.

Ausführen mit:
    python3 install.py
"""

import base64
import json
import os
import sys
from pathlib import Path


def main():
    script_dir = Path(__file__).parent.resolve()
    manifest = script_dir / "install.json"

    if not manifest.exists():
        print("ERROR: install.json nicht gefunden. Bitte das komplette job-hunter-skill/ Verzeichnis kopieren.")
        sys.exit(1)

    with open(manifest, encoding="utf-8") as f:
        files = json.load(f)

    target = Path.home() / ".claude" / "skills" / "job-hunter"

    print(f"Installiere Job-Hunter Skill nach: {target}")
    print(f"Anzahl Dateien: {len(files)}")
    print()

    for rel_path, encoded in files.items():
        dest = target / rel_path
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
