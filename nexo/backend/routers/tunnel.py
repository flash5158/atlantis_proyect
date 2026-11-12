"""Router túnel público — movido intacto desde server.py (NEXO 2.0).

Comportamiento idéntico: mismas rutas, métodos, formas de request/response
y códigos de estado. Solo cambió el andamiaje (package backend/).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from backend.core.config import config, es_host, es_local, token_ok

router = APIRouter()

TUNNEL_URL_FILE = Path("/tmp/nexo_tunnel.url")


def _tunnel_env() -> dict:
    env = dict(os.environ)
    env["NEXO_PORT"] = str(config.PORT)
    return env


@router.post("/api/tunnel/start")
async def api_tunnel_start(request: Request, token: str = Query("")):
    """Arranca el túnel Cloudflare (tunnel.sh start) hacia el puerto real del hub."""
    # Solo el dueño: abrir la máquina a internet no es delegable.
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede abrir el túnel."}, status_code=403)
    script = config.REPO_ROOT / "tunnel.sh"
    if not script.exists():
        return JSONResponse({"error": "tunnel.sh no encontrado"}, status_code=500)
    # Proceso asíncrono: el arranque (5-30 s) no congela el event loop.
    proc = await asyncio.create_subprocess_exec(
        "bash", str(script), "start",
        cwd=str(config.REPO_ROOT),
        env=_tunnel_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        url = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip() if TUNNEL_URL_FILE.exists() else ""
        url_valido = bool(url) and "api.trycloudflare.com" not in url and proc.returncode == 0
        if not url_valido:
            return {"ok": False, "url": "",
                    "error": "No se pudo establecer el túnel (revisa tu conexión a internet).",
                    "salida": stdout[-800:]}
        return {"ok": True, "url": url, "salida": stdout[-800:]}
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        url = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip() if TUNNEL_URL_FILE.exists() else ""
        return {"ok": bool(url), "url": url, "nota": "el arranque sigue en curso; consulta /api/tunnel/url"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/tunnel/url")
async def api_tunnel_url(request: Request, token: str = Query("")):
    """Devuelve la URL pública actual del túnel, o vacío si no hay túnel activo."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    url = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip() if TUNNEL_URL_FILE.exists() else ""
    pid_file = Path("/tmp/nexo_tunnel.pid")
    vivo = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            vivo = True
        except Exception:
            vivo = False
    return {"url": url if vivo else "", "activo": vivo and bool(url)}


@router.post("/api/tunnel/stop")
async def api_tunnel_stop(request: Request, token: str = Query("")):
    """Detiene el túnel Cloudflare."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede cerrar el túnel."}, status_code=403)
    script = config.REPO_ROOT / "tunnel.sh"
    proc = await asyncio.create_subprocess_exec(
        "bash", str(script), "stop",
        cwd=str(config.REPO_ROOT),
        env=_tunnel_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {"ok": True, "salida": stdout_bytes.decode("utf-8", errors="replace")[-400:]}
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return JSONResponse(
            {"error": "Tiempo de espera agotado deteniendo el túnel."}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
