#!/usr/bin/env bash
# ==============================================================================
# NEXO 2.0 Atlantis — Lanzador Todo-en-Uno
# Uso: ./start.sh [--tunnel] [--background]
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXO_DIR="$SCRIPT_DIR/nexo"
VENV_DIR="$NEXO_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"

echo "================================================================"
echo "⚡ INICIANDO NEXO 2.0 ATLANTIS (IA & DEVELOPER HUB)"
echo "================================================================"

# 1. Verificar entorno virtual Python
if [[ ! -d "$VENV_DIR" ]]; then
    echo "📦 Creando entorno virtual en $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# 2. Verificar dependencias
if ! "$PYTHON_BIN" -c "import fastapi, uvicorn, websockets" 2>/dev/null; then
    echo "⬇️  Instalando dependencias necesarias en el entorno..."
    "$PIP_BIN" install -r "$NEXO_DIR/requirements.txt"
fi

# 3. Lanzar túnel si se solicita con --tunnel
if [[ "${1:-}" == "--tunnel" || "${2:-}" == "--tunnel" ]]; then
    echo "🌐 Iniciando túnel público para tu compañero..."
    "$SCRIPT_DIR/tunnel.sh" start || true
fi

echo "🚀 Arrancando servidor NEXO..."
exec "$PYTHON_BIN" "$NEXO_DIR/server.py"
