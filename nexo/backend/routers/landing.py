"""Router de landing pública y descarga — Atlantis Studio.

Rutas públicas (sin auth: las abre el amigo de Daniel sin cuenta):
  GET /landing           -> web/landing.html (página que explica la app + descarga)
  GET /download          -> ~/.atlantis/dist/atlantis-studio.zip (409 honesto si falta)
  GET /api/download/info -> {"ready", "size", "version"}

Ruta protegida (local o token, como el túnel):
  GET /api/net/info      -> {"lan_ip", "port", "tunnel_url"} para construir el enlace.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from backend.core.config import config, es_local, token_ok

router = APIRouter()

TUNNEL_URL_FILE = Path("/tmp/nexo_tunnel.url")


def _lan_ip() -> str:
    """IP de la red local (sin enviar tráfico; UDP connect no transmite)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip else "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def _dist_zip() -> Path:
    return config.ATLANTIS_USER_DIR / "dist" / "atlantis-studio.zip"


def _version() -> str:
    try:
        pkg = config.REPO_ROOT / "desktop" / "native" / "package.json"
        return json.loads(pkg.read_text(encoding="utf-8")).get("version", "0.1.0")
    except Exception:
        return "0.1.0"


def _tunnel_url() -> str:
    try:
        if TUNNEL_URL_FILE.exists():
            return TUNNEL_URL_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


@router.get("/landing")
async def landing():
    """Página pública que describe Atlantis Studio (para el amigo)."""
    f = config.STATIC_DIR / "landing.html"
    if not f.exists():
        return JSONResponse({"ok": False, "error": "Página no disponible."}, status_code=404)
    return FileResponse(f, media_type="text/html")


@router.get("/download")
async def download():
    """Descarga pública del instalador/portable. 409 honesto si el host no la generó."""
    z = _dist_zip()
    if not z.exists():
        return JSONResponse(
            {"ok": False, "error": "El anfitrión aún no generó la descarga."},
            status_code=409,
        )
    return FileResponse(z, media_type="application/zip", filename="atlantis-studio.zip")


@router.get("/api/download/info")
async def download_info():
    z = _dist_zip()
    ready = z.exists()
    return {
        "ready": ready,
        "size": z.stat().st_size if ready else 0,
        "version": _version(),
    }


@router.get("/api/net/info")
async def net_info(request: Request, token: str = Query("")):
    """IP LAN + puerto + túnel activo (para construir el enlace de invitación)."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {
        "lan_ip": _lan_ip(),
        "port": config.PORT,
        "tunnel_url": _tunnel_url() or None,
    }
