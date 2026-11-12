"""Router preview local (ítem 7, build v0.6.0): previsualizar la app que se está
construyendo, 100% en local, gratis, sin server.

- **HTML estático** (index.html, sin punto de entrada de script): el
  propio servidor NEXO sirve los archivos del proyecto por
  ``/api/preview/sitio/<carpeta>/…`` y el panel los muestra en un iframe.
- **Python / Node** (package.json con start, server.js/app.js/index.js,
  app.py/main.py/server.py): corre el comando del proyecto en un
  subproceso en 127.0.0.1:<puerto libre> y el servidor lo proxyea por
  ``/api/preview/app/<carpeta>/…`` (el iframe apunta ahí).

Cada persona previsualiza SU propia copia sincronizada. El preview
corre el código del proyecto (la misma confianza que el terminal: solo
lo inicia el dueño de la laptop, con auth local/token).

"Compartir preview": el preview se sirve a través del propio servidor
NEXO, así que el túnel gratis de Cloudflare que la app ya tiene (que
expone el puerto del hub) también expone el preview: el enlace es
<túnel>/api/preview/…. Si el túnel no está abierto, el endpoint lo
dice (no inventa un enlace).
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from backend.core.config import es_local, token_ok
from backend.core.state import sala_de

router = APIRouter()

# (proyecto, carpeta) -> {tipo, puerto, proceso, entrada, url, salida, iniciado_en}
_PREVIEWS: dict[tuple[str, str], dict] = {}
_MAX_LINEAS_SALIDA = 200


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _carpeta_proyecto(proyecto: str, carpeta: str) -> Path:
    """Directorio del proyecto en disco (la carpeta dentro del workspace)."""
    sala = sala_de(proyecto)
    # La carpeta del proyecto vive dentro de la sala (proyecto/carpeta).
    base = Path(sala.directorio) / carpeta
    return base


def _detectar(proyecto: str, carpeta: str) -> tuple[str, list[str], str]:
    """(tipo, comando, entrada) del preview, o (tipo, [], entrada) si
    no se detectó qué previsualizar. Orden: package.json start →
    server.js → app.js → index.js → app.py → main.py → server.py →
    index.html (estático)."""
    base = _carpeta_proyecto(proyecto, carpeta)
    if not base.is_dir():
        return "", [], ""
    package_json = base / "package.json"
    if package_json.is_file():
        try:
            pkg = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
            if isinstance(pkg.get("scripts", {}).get("start"), str):
                npm = shutil.which("npm")
                if npm:
                    return "script", [npm, "start"], "package.json (npm start)"
        except Exception:
            pass
    for entrada in ("server.js", "app.js", "index.js"):
        if (base / entrada).is_file():
            node = shutil.which("node")
            if node:
                return "script", [node, entrada], entrada
            return "", [], f"falta node para {entrada}"
    for entrada in ("app.py", "main.py", "server.py"):
        if (base / entrada).is_file():
            return "script", [sys.executable, entrada], entrada
    if (base / "index.html").is_file():
        return "estatico", [], "index.html"
    return "", [], ""


def _url_preview(proyecto: str, carpeta: str, tipo: str) -> str:
    base = "/api/preview/sitio" if tipo == "estatico" else "/api/preview/app"
    return f"{base}/{carpeta}/?proyecto={proyecto}"


def _leer_salida(proc: subprocess.Popen, salida: deque) -> None:
    try:
        for linea in proc.stdout or []:
            salida.append(linea.rstrip("\n"))
    except Exception:
        pass


@router.post("/api/preview/iniciar")
async def api_preview_iniciar(req: Request, token: str = Query("")):
    """Inicia el preview de un proyecto. Idempotente: si ya está
    activo, devuelve su estado actual."""
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    carpeta = str(body.get("carpeta", "") or "")
    if not carpeta or "/" in carpeta or carpeta.startswith("."):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    clave = (proyecto, carpeta)
    existente = _PREVIEWS.get(clave)
    if existente is not None:
        if existente["tipo"] == "estatico":
            return {"ok": True, "activo": True, "tipo": "estatico",
                    "url": existente["url"], "puerto": None,
                    "entrada": existente["entrada"], "salida": []}
        if existente["proceso"] is not None and existente["proceso"].poll() is None:
            return {"ok": True, "activo": True, "tipo": "script",
                    "url": existente["url"], "puerto": existente["puerto"],
                    "entrada": existente["entrada"],
                    "salida": list(existente["salida"])}
        # El proceso murió: limpiar y reiniciar.
        _PREVIEWS.pop(clave, None)
    tipo, comando, entrada = _detectar(proyecto, carpeta)
    if not tipo:
        return JSONResponse(
            {"ok": False, "error": entrada or "No se detectó qué previsualizar (falta index.html o punto de entrada: server.js, app.py, main.py o package.json con start)."},
            status_code=400)
    if tipo == "estatico":
        _PREVIEWS[clave] = {"tipo": "estatico", "puerto": None, "proceso": None,
                            "entrada": entrada, "url": _url_preview(proyecto, carpeta, tipo),
                            "salida": deque(maxlen=_MAX_LINEAS_SALIDA),
                            "iniciado_en": time.time()}
        return {"ok": True, "activo": True, "tipo": "estatico",
                "url": _PREVIEWS[clave]["url"], "puerto": None,
                "entrada": entrada, "salida": []}
    puerto = _puerto_libre()
    base = _carpeta_proyecto(proyecto, carpeta)
    env = dict(os.environ)
    env["PORT"] = str(puerto)
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.Popen(
            comando, cwd=str(base), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "error": f"No se pudo ejecutar el preview: {e}"},
                            status_code=500)
    salida: deque = deque(maxlen=_MAX_LINEAS_SALIDA)
    threading.Thread(target=_leer_salida, args=(proc, salida),
                     daemon=True).start()
    _PREVIEWS[clave] = {"tipo": "script", "puerto": puerto, "proceso": proc,
                        "entrada": entrada,
                        "url": _url_preview(proyecto, carpeta, tipo),
                        "salida": salida, "iniciado_en": time.time()}
    # Si el proceso muere al instante (error de arranque), reportar el
    # error real con su salida en vez de un preview "activo" falso.
    time.sleep(0.8)
    if proc.poll() is not None:
        _PREVIEWS.pop(clave, None)
        return JSONResponse(
            {"ok": False, "error": f"El preview terminó al arrancar (código {proc.returncode}). Revisa la salida.",
             "salida": list(salida)}, status_code=500)
    return {"ok": True, "activo": True, "tipo": "script",
            "url": _PREVIEWS[clave]["url"], "puerto": puerto,
            "entrada": entrada, "salida": list(salida)}


@router.post("/api/preview/detener")
async def api_preview_detener(req: Request, token: str = Query("")):
    """Detiene el preview de un proyecto (mata el subproceso si lo hay)."""
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    carpeta = str(body.get("carpeta", "") or "")
    clave = (proyecto, carpeta)
    info = _PREVIEWS.pop(clave, None)
    if info is None:
        return {"ok": True, "activo": False}
    proc = info.get("proceso")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return {"ok": True, "activo": False}


@router.get("/api/preview/estado")
async def api_preview_estado(request: Request, proyecto: str = Query("general"),
                             carpeta: str = Query(""), token: str = Query("")):
    """Estado del preview: activo, tipo, URL, puerto y últimas líneas
    de salida del proceso (si lo hay)."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    info = _PREVIEWS.get((proyecto, carpeta))
    if info is None:
        return {"activo": False, "tipo": "", "url": "", "puerto": None,
                "entrada": "", "salida": [], "proceso_vivo": False}
    proc = info.get("proceso")
    vivo = proc is None or proc.poll() is None
    if info["tipo"] == "script" and not vivo:
        # El proceso murió: el estado lo refleja con su salida.
        return {"activo": False, "tipo": "script", "url": info["url"],
                "puerto": info["puerto"], "entrada": info["entrada"],
                "salida": list(info["salida"]), "proceso_vivo": False}
    return {"activo": True, "tipo": info["tipo"], "url": info["url"],
            "puerto": info["puerto"], "entrada": info["entrada"],
            "salida": list(info["salida"]), "proceso_vivo": vivo}


@router.post("/api/preview/compartir")
async def api_preview_compartir(req: Request, token: str = Query("")):
    """Enlace público del preview vía el túnel Cloudflare de la app.
    El preview se sirve a través del servidor NEXO, así que el túnel
    (que expone el puerto del hub) también expone el preview. Si el
    túnel no está abierto, error claro (no se inventa un enlace)."""
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    carpeta = str(body.get("carpeta", "") or "")
    info = _PREVIEWS.get((proyecto, carpeta))
    if info is None:
        return JSONResponse({"ok": False, "error": "Inicia el preview primero."},
                            status_code=400)
    tunnel_file = Path("/tmp/nexo_tunnel.url")
    try:
        url_tunel = tunnel_file.read_text(encoding="utf-8").strip() if tunnel_file.is_file() else ""
    except Exception:
        url_tunel = ""
    if not url_tunel:
        return JSONResponse(
            {"ok": False,
             "error": "El túnel no está abierto. Ábrelo primero (botón de túnel de la app) y vuelve a intentarlo."},
            status_code=400)
    return {"ok": True, "url": url_tunel.rstrip("/") + info["url"]}


@router.get("/api/preview/sitio/{carpeta}")
@router.get("/api/preview/sitio/{carpeta}/{resto:path}")
async def api_preview_sitio(carpeta: str, request: Request,
                            resto: str = "", proyecto: str = Query("general"),
                            token: str = Query("")):
    """Sirve el proyecto estático (HTML) por el propio servidor NEXO.

    Sin auth de token por diseño: el preview es un servidor de
    desarrollo que el usuario encendió explícitamente (y apaga con
    Detener). Solo sirve archivos del proyecto con preview ACTIVO,
    nunca secretos (.env/.git/ocultos/backups). El enlace compartido
    no lleva token (no filtra credenciales)."""
    info = _PREVIEWS.get((proyecto, carpeta))
    if info is None or info["tipo"] != "estatico":
        return JSONResponse({"error": "preview estático no activo para este proyecto."},
                            status_code=404)
    base = _carpeta_proyecto(proyecto, carpeta)
    rel = (resto or "").strip("/")
    if not rel:
        rel = "index.html"
    # Igual que el sync: nunca se sirven ocultos (.env, .git), backups
    # de conflicto ni directorios de build/dependencias.
    partes = Path(rel).parts
    if (any(p.startswith(".") for p in partes)
            or any(p in {"__pycache__", "node_modules", ".venv", "dist", ".git"}
                   for p in partes)
            or ".conflict." in Path(rel).name):
        return JSONResponse({"error": "archivo no servible en preview"}, status_code=403)
    try:
        objetivo = (base / rel).resolve()
        if not str(objetivo).startswith(str(base.resolve())):
            return JSONResponse({"error": "ruta fuera del proyecto"}, status_code=403)
    except Exception:
        return JSONResponse({"error": "ruta inválida"}, status_code=400)
    if objetivo.is_dir():
        objetivo = objetivo / "index.html"
    if not objetivo.is_file():
        return JSONResponse({"error": "archivo no encontrado"}, status_code=404)
    mime, _ = mimetypes.guess_type(str(objetivo))
    return Response(objetivo.read_bytes(),
                    media_type=mime or "application/octet-stream")


@router.api_route("/api/preview/app/{carpeta}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@router.api_route("/api/preview/app/{carpeta}/{resto:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def api_preview_app(carpeta: str, request: Request,
                          resto: str = "", proyecto: str = Query("general"),
                          token: str = Query("")):
    """Proxyea al subproceso del preview (proyecto con script). Sin
    auth de token por diseño (igual que el sitio estático): el
    usuario encendió el preview explícitamente; solo proxy al
    preview activo de este proyecto."""
    info = _PREVIEWS.get((proyecto, carpeta))
    if info is None or info["tipo"] != "script":
        return JSONResponse({"error": "preview con script no activo para este proyecto."},
                            status_code=404)
    proc = info.get("proceso")
    if proc is None or proc.poll() is not None:
        return JSONResponse({"error": "el proceso del preview no está corriendo."},
                            status_code=502)
    puerto = info["puerto"]
    ruta = "/" + (resto or "").strip("/")
    query = str(request.url.query)
    url = f"http://127.0.0.1:{puerto}{ruta}"
    if query:
        url += "?" + query
    # Reenviar cabeceras salvo las de salto y host.
    saltar = {"host", "content-length", "connection", "keep-alive",
              "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "transfer-encoding", "upgrade"}
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in saltar}
    cuerpo = await request.body()
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as cliente:
            r = await cliente.request(request.method, url, headers=headers,
                                      content=cuerpo)
    except Exception as e:
        return JSONResponse({"error": f"no se pudo contactar al preview: {e}"},
                            status_code=502)
    headers_salida = {k: v for k, v in r.headers.items()
                      if k.lower() not in saltar | {"content-encoding"}}
    return Response(r.content, status_code=r.status_code, headers=headers_salida)
