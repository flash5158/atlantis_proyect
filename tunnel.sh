#!/usr/bin/env bash
# cloudflared tunnel para NEXO - corre en segundo plano
# Uso: ./tunnel.sh start|stop|logs|url

set -euo pipefail
TUNNEL_NAME="nexotunnel"
CF_DIR="$HOME/.cloudflared"

start() {
    CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$HOME/.local/bin/cloudflared}"
    if [[ ! -x "$CLOUDFLARED_BIN" ]]; then
        echo "Instalando cloudflared en $CLOUDFLARED_BIN..."
        if [[ "$OSTYPE" == "linux-gnu"* ]]; then
            # uname -m devuelve aarch64 en ARM64, pero el release se llama arm64
            case "$(uname -m)" in
                aarch64|arm64) ARCH="arm64" ;;
                x86_64|amd64)  ARCH="amd64" ;;
                *) ARCH="$(uname -m)" ;;
            esac
            mkdir -p "$(dirname "$CLOUDFLARED_BIN")"
            curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH" -o "$CLOUDFLARED_BIN"
            chmod +x "$CLOUDFLARED_BIN"
        elif [[ "$OSTYPE" == "darwin"* ]]; then
            brew install cloudflared
            CLOUDFLARED_BIN="$(command -v cloudflared)"
        fi
    fi
    CLOUDFLARED_BIN="$(readlink -f "$CLOUDFLARED_BIN" 2>/dev/null || echo "$CLOUDFLARED_BIN")"

    if [[ ! -f "$CF_DIR/cert.pem" ]]; then
        echo "Primera vez: autentica con Cloudflare..."
        cloudflared tunnel login
    fi

    if ! cloudflared tunnel list | grep -q "$TUNNEL_NAME"; then
        cloudflared tunnel create "$TUNNEL_NAME"
    fi

    TUNNEL_ID=$(cloudflared tunnel list | awk -v n="$TUNNEL_NAME" '$2==n{print $1}')
    echo "Tunnel ID: $TUNNEL_ID"

    # Config
    mkdir -p "$CF_DIR"
    cat > "$CF_DIR/config.yml" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CF_DIR/$TUNNEL_ID.json
ingress:
  - hostname: nexo.${TUNNEL_NAME}.cfargotunnel.com
    service: http://localhost:8787
  - service: http_status:404
EOF

    echo "Iniciando túnel..."
    cloudflared tunnel run "$TUNNEL_NAME" &
    echo $! > /tmp/nexotunnel.pid
    sleep 3
    echo "Túnel activo. URL pública:"
    echo "  https://nexo.${TUNNEL_NAME}.cfargotunnel.com"
    echo ""
    echo "Para tu amigo: nexo-cli --server https://nexo.${TUNNEL_NAME}.cfargotunnel.com --token <TOKEN> --nombre hermes-2 ..."
}

stop() {
    if [[ -f /tmp/nexotunnel.pid ]]; then
        kill "$(cat /tmp/nexotunnel.pid)" 2>/dev/null || true
        rm /tmp/nexotunnel.pid
        echo "Túnel detenido."
    else
        echo "No hay túnel corriendo."
    fi
}

logs() {
    if [[ -f /tmp/nexotunnel.pid ]]; then
        echo "PID: $(cat /tmp/nexotunnel.pid)"
    fi
    cloudflared tunnel list | grep "$TUNNEL_NAME" || echo "Túnel no creado."
}

url() {
    echo "https://nexo.${TUNNEL_NAME}.cfargotunnel.com"
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    logs) logs ;;
    url) url ;;
    *) echo "Uso: $0 {start|stop|logs|url}" ;;
esac