#!/usr/bin/env python3
"""Genera ~/.atlantis/dist/atlantis-studio.zip — el paquete que la landing ofrece.

Uso:
    python3 scripts/build_dist.py

Exclusiones (igual que el zip de release): .git, node_modules, __pycache__,
.venv, dist, *.AppImage. Imprime ruta + tamaño.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_ZIP = Path.home() / ".atlantis" / "dist" / "atlantis-studio.zip"

EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist"}
EXCLUDE_SUFFIXES = {".AppImage"}


def main() -> int:
    DIST_ZIP.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(DIST_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(PROJECT_ROOT.rglob("*")):
            rel = p.relative_to(PROJECT_ROOT)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if p.is_file() and p.suffix in EXCLUDE_SUFFIXES:
                continue
            if p.is_dir():
                continue
            zf.write(p, rel.as_posix())
            count += 1
    size = DIST_ZIP.stat().st_size
    print(f"OK: {DIST_ZIP}")
    print(f"Archivos: {count} | Tamaño: {size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
