#!/usr/bin/env bash
# ==============================================================================
# NEXO 2.0 — Quick Tunnel Automático (Cloudflare Quick Tunnels)
# Uso: ./tunnel.sh {start|stop|status|url|logs}
#
# Genera una URL pública gratuita https://xxxx.trycloudflare.com sin necesidad
# de crear cuenta en Cloudflare, sin certificados y sin abrir puertos.
# ==============================================================================

set -euo pipefail

PORT="${NEXO_PORT:-8787}"
PID_FILE="/tmp/nexo_tunnel.pid"
LOG_FILE="/tmp/nexo_tunnel.log"
URL_FILE="/tmp/nexo_tunnel.url"
BIN_DIR="$HOME/.local/bin"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$BIN_DIR/cloudflared}"

instalar_cloudflared() {
    if command -v cloudflared >/dev/null 2>&1; then
        CLOUDFLARED_BIN="$(command -v cloudflared)"
        return 0
    fi
    if [[ -x "$CLOUDFLARED_BIN" ]]; then
        return 0
    fi

    echo "⚡ Instalando cloudflared en $BIN_DIR..."
    mkdir -p "$BIN_DIR"

    ARCH="$(uname -m)"
    OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

    if [[ "$OS" == "darwin" ]]; then
        if command -v brew >/dev/null 2>&1; then
            echo "📦 Instalando mediante Homebrew..."
            brew install cloudflared || true
            if command -v cloudflared >/dev/null 2>&1; then
                CLOUDFLARED_BIN="$(command -v cloudflared)"
                return 0
            fi
        fi
        # Fallback a binario directo en macOS
        case "$ARCH" in
            arm64|aarch64) DL_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64" ;;
            *)             DL_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64" ;;
        esac
    else
        # Linux
        case "$ARCH" in
            arm64|aarch64) DL_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64" ;;
            *)             DL_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" ;;
        esac
    fi

    echo "Descargando desde: $DL_URL"
    curl -fsSL "$DL_URL" -o "$CLOUDFLARED_BIN"
    chmod +x "$CLOUDFLARED_BIN"
    echo "✓ cloudflared instalado correctamente."
}

start_tunnel() {
    instalar_cloudflared

    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "⚠️ El túnel ya está en ejecución."
        echo "URL Pública: $(cat "$URL_FILE" 2>/dev/null || echo "Revisa ./tunnel.sh url")"
        return 0
    fi

    echo "🚀 Iniciando Quick Tunnel para http://127.0.0.1:$PORT..."
    rm -f "$LOG_FILE" "$URL_FILE"

    "$CLOUDFLARED_BIN" tunnel --url "http://127.0.0.1:$PORT" > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    echo "⏳ Obteniendo URL pública segura..."
    for _ in {1..30}; do
        sleep 1
        if grep -o "https://[-a-zA-Z0-9]*\.trycloudflare\.com" "$LOG_FILE" | head -n 1 > "$URL_FILE"; then
            TUNNEL_URL="$(cat "$URL_FILE")"
            if [[ -n "$TUNNEL_URL" ]]; then
                echo ""
                echo "================================================================"
                echo "🎉 ¡TÚNEL ACTIVO Y CONEXIÓN ESTABLECIDA!"
                echo "================================================================"
                echo "🌐 URL Pública: $TUNNEL_URL"
                echo "================================================================"
                echo ""
                echo "Pásale esta URL a tu compañero para colaborar desde cualquier red."
                return 0
            fi
        fi
    done

    echo "❌ No se pudo extraer la URL en 30s. Revisa los logs con: ./tunnel.sh logs"
}

stop_tunnel() {
    if [[ -f "$PID_FILE" ]]; then
        PID="$(cat "$PID_FILE")"
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            echo "✓ Túnel (PID $PID) detenido."
        else
            echo "El proceso ya no estaba activo."
        fi
        rm -f "$PID_FILE" "$URL_FILE"
    else
        echo "No hay ningún túnel en ejecución."
    fi
}

get_url() {
    if [[ -f "$URL_FILE" ]]; then
        cat "$URL_FILE"
    else
        echo "No hay túnel activo. Inícialo con: ./tunnel.sh start"
    fi
}

status_tunnel() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "🟢 Estado: ACTIVO (PID $(cat "$PID_FILE"))"
        echo "🌐 URL: $(cat "$URL_FILE" 2>/dev/null || echo "Desconocida")"
    else
        echo "🔴 Estado: DETENIDO"
    fi
}

logs_tunnel() {
    if [[ -f "$LOG_FILE" ]]; then
        tail -n 40 "$LOG_FILE"
    else
        echo "No hay logs disponibles."
    fi
}

case "${1:-}" in
    start)  start_tunnel ;;
    stop)   stop_tunnel ;;
    status) status_tunnel ;;
    url)    get_url ;;
    logs)   logs_tunnel ;;
    *)
        echo "Uso: $0 {start|stop|status|url|logs}"
        exit 1
        ;;
esac