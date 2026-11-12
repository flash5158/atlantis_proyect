#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/nexo/.venv/bin/python}"
OUT="$ROOT/desktop/extension/runtime"
mkdir -p "$OUT"
"$PYTHON" -m PyInstaller --noconfirm --clean --onefile --name atlantis-runtime \
  --paths "$ROOT/nexo" --hidden-import atlantis_hub.app --hidden-import atlantis_hub.models \
  "$ROOT/nexo/atlantis_runtime.py"
cp "$ROOT/dist/atlantis-runtime" "$OUT/atlantis-runtime"
chmod +x "$OUT/atlantis-runtime"
echo "Runtime Linux creado en $OUT/atlantis-runtime"
echo "Para macOS/Windows ejecuta este script en la plataforma destino; no mezcles binarios entre sistemas."
