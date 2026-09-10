#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$ROOT/nexo/.venv/bin/python}"
WORKSPACE="${1:-$ROOT}"
PORT="${ATLANTIS_PORT:-8790}"
if [[ ! -x "$PYTHON" ]]; then
  echo "Falta el entorno Python. Ejecuta: $ROOT/nexo/.venv/bin/python -m venv $ROOT/nexo/.venv" >&2
  exit 2
fi
exec env PYTHONPATH="$ROOT/nexo${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m atlantis_runtime --workspace "$WORKSPACE" --port "$PORT" --name "${ATLANTIS_NAME:-Daniel}"
