#!/usr/bin/env python3
from __future__ import annotations

"""NEXO 2.0 — Atlantis Collaborative Hub & Real AI Matrix.

Características avanzadas:
  - Terminal interactiva PTY real (xterm.js + zsh/bash nativo con soporte completo de colores, REPL y atajos)
  - Motor de IA real multi-proveedor (Gemini, Groq, OpenRouter, OpenAI, Ollama y análisis de código local)
  - Respuestas automáticas de IA en tiempo real al mencionar @ia, @antigravity, @hermes o solicitar revisiones
  - Soporte para editor Monaco (VS Code engine) con resaltado de sintaxis, números de línea y autocompletado
  - Detección automática de token para conexiones locales (sin modales molestos)
  - Colaboración simultánea entre dos desarrolladores humanos e IAs
  - Compatible con macOS (Apple Silicon/Intel) y Linux
"""

import asyncio
import fcntl
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import time

try:
    import fcntl
    import pty
    import struct
    import termios
    HAS_PTY = True
except ImportError:
    HAS_PTY = False
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Importar motor de IA real y Red Social
import ai_engine
import antigravity_engine
import social_matrix

# ---------------------------------------------------------------- Rutas y Workspace
HOME = Path.home()
BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parent

IS_VERCEL = bool(os.environ.get("VERCEL"))
# Public deployments are intentionally usable without a shared owner token.
# Local installations keep the bearer-token guard by default.
PUBLIC_MODE = IS_VERCEL or bool(os.environ.get("NEXO_PUBLIC"))
if IS_VERCEL:
    WORKSPACE = Path("/tmp/atlantis_proyect").resolve()
    PROYECTOS = WORKSPACE / "proyectos"
    NEXO_DATA_DIR = Path("/tmp/.nexo")
else:
    env_ws = os.environ.get("NEXO_WORKSPACE")
    if env_ws and Path(env_ws).exists():
        WORKSPACE = Path(env_ws).resolve()
    elif (REPO_ROOT / "proyectos").exists():
        WORKSPACE = REPO_ROOT.resolve()
    elif (BASE / "colab-hub").exists():
        WORKSPACE = (BASE / "colab-hub").resolve()
    elif (HOME / "colab-hub").exists():
        WORKSPACE = (HOME / "colab-hub").resolve()
    else:
        WORKSPACE = REPO_ROOT.resolve()

    PROYECTOS = WORKSPACE / "proyectos"
    NEXO_DATA_DIR = WORKSPACE / ".nexo"

    ATLANTIS_USER_DIR = HOME / ".atlantis"
    ATLANTIS_USER_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE = ATLANTIS_USER_DIR / "accounts.json"

CURRENT_TERMINAL_CWD: Path = WORKSPACE


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest() == hashed


def cargar_cuentas() -> list[dict[str, Any]]:
    if not ACCOUNTS_FILE.exists():
        # Crear cuenta inicial para desarrollo inmediato si no existe ninguna
        default_hash, default_salt = hash_password("atlantis123")
        initial = [
            {
                "id": "usr_daniel_01",
                "username": "daniel",
                "email": "daniel@atlantis.local",
                "name": "Daniel",
                "role": "Lead Developer / Arquitecto",
                "password_hash": default_hash,
                "salt": default_salt,
                "created_at": datetime.now().isoformat(),
                "avatar": "DS",
            }
        ]
        try:
            ACCOUNTS_FILE.write_text(json.dumps(initial, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Cuentas] Error creando archivo inicial: {e}")
        return initial

    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def guardar_cuentas(cuentas: list[dict[str, Any]]):
    try:
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNTS_FILE.write_text(json.dumps(cuentas, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[Cuentas] Error guardando cuentas: {e}")


ACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}
LAST_AUTHENTICATED_USER: dict[str, Any] | None = None

HERMES_STATE = {
    "my_agent": {
        "name": "Hermes-Daniel",
        "role": "Creador Autónomo",
        "engine": "local",
        "provider": "gemini",
        "model": "gemini-3.6-flash",
        "endpoint": "",
        "status": "connected",
        "model_agnostic": True,
        "capabilities": ["terminal_pty", "bash", "filesystem", "git", "antigravity_skills", "dual_pairing"],
    },
    "friend_agent": {
        "name": "Hermes-Amigo",
        "role": "Auditor & Co-piloto",
        "engine": "dual_local",
        "provider": "agnostic",
        "model": "claude-3.5-sonnet",
        "endpoint": "",
        "status": "connected",
        "model_agnostic": True,
    },
    "paired": True,
    "pair_code": "HERMES-" + secrets.token_hex(3).upper(),
    "pair_timestamp": datetime.now().isoformat(),
}


def set_active_workspace(new_path: Path):
    global WORKSPACE, PROYECTOS, NEXO_DATA_DIR, CURRENT_TERMINAL_CWD
    WORKSPACE = Path(new_path).resolve()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    PROYECTOS = WORKSPACE / "proyectos"
    NEXO_DATA_DIR = WORKSPACE / ".nexo"
    NEXO_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_TERMINAL_CWD = WORKSPACE
    os.environ["NEXO_WORKSPACE"] = str(WORKSPACE)

# The web shell lives at the repository root so the deployed experience can
# evolve independently from the legacy NEXO assets.  Keep the old directory as
# a fallback for existing local installs and API smoke tests.
STATIC_DIR = REPO_ROOT / "web" if (REPO_ROOT / "web" / "index.html").is_file() else BASE / "static"
if not STATIC_DIR.is_dir() and (REPO_ROOT / "nexo" / "static").is_dir():
    STATIC_DIR = REPO_ROOT / "nexo" / "static"

AI_DATA_FILE = NEXO_DATA_DIR / "ai_colab.json"
CONFIG = ATLANTIS_USER_DIR / "config.json"
BASE_CONFIG = BASE / "config.json"
PORT = int(os.environ.get("PORT", "8787"))

MAX_LECTURA = 10 * 1024 * 1024     # 10 MB
MAX_SUBIDA = 50 * 1024 * 1024      # 50 MB
MAX_CHAT = 12000                   # caracteres por mensaje

IS_DARWIN = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def cargar_config() -> dict:
    cfg: dict[str, Any] = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    elif BASE_CONFIG.exists():
        try:
            cfg = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    cfg.setdefault("token", secrets.token_hex(16))
    cfg.setdefault("nombre", "jaime")
    cfg.setdefault("companero", "amigo")
    cfg.setdefault("ia_nombre", "antigravity")
    cfg.setdefault("ia_companero", "hermes")
    cfg.setdefault("tema", "jarvis")
    cfg.setdefault("ai_proveedor", "auto")
    cfg.setdefault("ai_api_key", os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY") or "")
    cfg.setdefault("ai_modelo", "auto")
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[Server] Aviso guardando config: {e}")
    return cfg


CFG = cargar_config()
TOKEN = os.environ.get("TOKEN") or CFG["token"]
try:
    PROYECTOS.mkdir(parents=True, exist_ok=True)
    NEXO_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not (PROYECTOS / "README.md").exists():
        (PROYECTOS / "README.md").write_text(
            "# Proyectos de NEXO\n\nDirectorio de trabajo colaborativo para código compartido.\n",
            encoding="utf-8",
        )
except Exception:
    pass


# ---------------------------------------------------------------- Datos de IA y Presencia
def _cargar_ai_data() -> dict:
    if AI_DATA_FILE.exists():
        try:
            return json.loads(AI_DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    datos = {
        "mensajes": [],
        "tareas": [],
        "agentes": {
            CFG.get("ia_nombre", "antigravity"): {
                "tipo": "antigravity",
                "estado": "listo",
                "actividad": "Co-pilot en línea",
                "ultimo_ping": time.time(),
            },
            CFG.get("ia_companero", "hermes"): {
                "tipo": "hermes",
                "estado": "listo",
                "actividad": "Enlace activo",
                "ultimo_ping": time.time(),
            },
        },
    }
    _guardar_ai_data(datos)
    return datos


def _guardar_ai_data(datos: dict) -> None:
    try:
        NEXO_DATA_DIR.mkdir(parents=True, exist_ok=True)
        AI_DATA_FILE.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[NEXO] Error al guardar datos IA: {e}")


AI_STORE = _cargar_ai_data()
FILE_LOCKS: dict[str, dict] = {}


def entorno_limpio(proyecto_path: Path) -> dict:
    path_dirs = [
        str(BASE / ".venv" / "bin"),
        str(HOME / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    current_path = os.environ.get("PATH", "")
    full_path = ":".join(d for d in path_dirs if Path(d).exists()) + (":" + current_path if current_path else "")
    env = dict(os.environ)
    env.update({
        "PATH": full_path,
        "HOME": str(HOME),
        "WORKSPACE": str(WORKSPACE),
        "PROYECTO": str(proyecto_path),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def token_ok(token: str) -> bool:
    if PUBLIC_MODE:
        return True
    return hmac.compare_digest(token or "", TOKEN)


def es_local(req_or_ws) -> bool:
    client = getattr(req_or_ws, "client", None)
    if not client:
        return True
    host = client.host
    return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "testclient")


# ---------------------------------------------------------------- FastAPI App
app = FastAPI(title="NEXO 2.0 Real AI", description="Atlantis Real Developer & AI Hub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CONECTADOS: set[WebSocket] = set()
SALAS: dict[str, Sala] = {}


# ---------------------------------------------------------------- Sala de Proyecto
class Sala:
    def __init__(self, nombre: str, directorio: Path):
        self.nombre = nombre
        self.directorio = directorio
        self.conectados: set[WebSocket] = set()
        self.chat_file = directorio / ".nexo-chat.md"
        if not self.chat_file.exists():
            cabecera = (
                f"# NEXO Chat — {nombre}\n"
                f"> Sala iniciada: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                "> Colaboración en tiempo real para humanos e IAs.\n\n"
            )
            self.chat_file.write_text(cabecera, encoding="utf-8")

    def historial(self, limite: int = 150) -> list[dict]:
        if not self.chat_file.exists():
            return []
        msgs = []
        pat = re.compile(r"^\*\*\[(.*?)\] (.*?):\*\* (.*)$")
        current_msg = None
        for linea in self.chat_file.read_text(encoding="utf-8", errors="replace").splitlines():
            m = pat.match(linea)
            if m:
                if current_msg:
                    msgs.append(current_msg)
                current_msg = {"fecha": m.group(1), "de": m.group(2), "texto": m.group(3)}
            elif linea.startswith("> "):
                if current_msg:
                    msgs.append(current_msg)
                    current_msg = None
                msgs.append({"fecha": "", "de": "sistema", "texto": linea[2:]})
            else:
                if current_msg:
                    current_msg["texto"] += "\n" + linea
                elif linea.strip():
                    msgs.append({"fecha": "", "de": "sistema", "texto": linea})
        if current_msg:
            msgs.append(current_msg)
        return msgs[-limite:]

    def guardar_chat(self, de: str, texto: str, fecha: str):
        with self.chat_file.open("a", encoding="utf-8") as f:
            f.write(f"**[{fecha}] {de}:** {texto}\n")

    def guardar_sistema(self, texto: str, fecha: str):
        with self.chat_file.open("a", encoding="utf-8") as f:
            f.write(f"> {texto} ({fecha})\n")


def sala_de(nombre: str) -> Sala:
    nombre_limpio = Path(nombre or "general").name.strip() or "general"
    if nombre_limpio not in SALAS:
        directorio = PROYECTOS / nombre_limpio
        directorio.mkdir(parents=True, exist_ok=True)
        if not (directorio / "README.md").exists():
            (directorio / "README.md").write_text(
                f"# {nombre_limpio}\n\nProyecto colaborativo en NEXO.\n",
                encoding="utf-8",
            )
        SALAS[nombre_limpio] = Sala(nombre_limpio, directorio)
    return SALAS[nombre_limpio]


def ruta_segura(sala: Sala, ruta: str) -> Path:
    base = sala.directorio.resolve()
    p = (base / ruta).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"Ruta fuera del proyecto: {ruta}")
    return p


def _arbol() -> list[dict]:
    arbol = []
    # 1. Proyectos si existen
    if PROYECTOS.exists():
        for p in sorted(PROYECTOS.iterdir()):
            if not p.is_dir() or p.name.startswith("."):
                continue
            archivos = []
            for f in sorted(p.rglob("*")):
                if f.is_file() and not any(part.startswith(".") for part in f.parts) and f.name != ".nexo-chat.md":
                    archivos.append(str(f.relative_to(p)))
            arbol.append({"nombre": p.name, "archivos": archivos})

    # 2. Archivos del workspace principal si no están en proyectos
    if WORKSPACE.exists():
        archivos_raiz = []
        ignorar = {".git", ".venv", "node_modules", "__pycache__", "dist", ".ruff_cache", ".vercel"}
        for f in sorted(WORKSPACE.iterdir()):
            if f.name.startswith(".") or f.name in ignorar:
                continue
            if f.is_file():
                archivos_raiz.append(f.name)
            elif f.is_dir() and f.name != "proyectos":
                for subf in sorted(f.rglob("*")):
                    if subf.is_file() and not any(part.startswith(".") or part in ignorar for part in subf.parts):
                        archivos_raiz.append(str(subf.relative_to(WORKSPACE)))
        if archivos_raiz:
            arbol.insert(0, {"nombre": WORKSPACE.name or "workspace", "archivos": archivos_raiz, "es_raiz": True})

    return arbol


async def enviar(ws: WebSocket, msg: dict):
    try:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))
    except Exception:
        pass


async def enviar_sala(sala: Sala, msg: dict, excepto: WebSocket | None = None):
    for ws in list(sala.conectados):
        if ws is excepto:
            continue
        try:
            await enviar(ws, msg)
        except Exception:
            sala.conectados.discard(ws)


async def enviar_todos(msg: dict):
    for ws in list(CONECTADOS):
        try:
            await enviar(ws, msg)
        except Exception:
            CONECTADOS.discard(ws)


async def difundir_arbol():
    await enviar_todos({"tipo": "arbol", "arbol": _arbol()})


# ---------------------------------------------------------------- Endpoints REST
@app.get("/")
async def raiz():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/token_local")
async def api_token_local(request: Request):
    """Bootstrap for local sessions and anonymous public deployments."""
    if PUBLIC_MODE:
        return {"token": "public", "local": False}
    if es_local(request):
        return {"token": TOKEN, "local": True}
    return JSONResponse({"error": "No permitido"}, status_code=403)


@app.get("/api/status")
async def api_status(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    cfg_ia = ai_engine.obtener_config_ia()
    return {
        "sistema": "NEXO 2.0 Atlantis",
        "os": platform.system(),
        "arch": platform.machine(),
        "workspace": str(WORKSPACE),
        "terminal_cwd": str(CURRENT_TERMINAL_CWD),
        "proyectos": len(_arbol()),
        "conectados": len(CONECTADOS),
        "ai_engine": {
            "proveedor": cfg_ia.get("proveedor"),
            "modelo": cfg_ia.get("modelo"),
            "has_key": bool(cfg_ia.get("api_key")),
        },
        "config": {
            "nombre": CFG.get("nombre"),
            "companero": CFG.get("companero"),
            "ia_nombre": CFG.get("ia_nombre"),
            "ia_companero": CFG.get("ia_companero"),
            "tema": CFG.get("tema"),
        },
    }


@app.get("/api/arbol")
async def api_arbol(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {
        "arbol": _arbol(),
        "nombre": CFG.get("nombre", "jaime"),
        "companero": CFG.get("companero", "amigo"),
        "ia_nombre": CFG.get("ia_nombre", "antigravity"),
        "ia_companero": CFG.get("ia_companero", "hermes"),
        "tema": CFG.get("tema", "jarvis"),
        "token": TOKEN if es_local(request) else "",
        "file_locks": FILE_LOCKS,
    }


@app.get("/api/historial")
async def api_historial(proyecto: str, request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        return {"historial": sala_de(proyecto).historial()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/chat")
async def api_chat(req: Request, proyecto: str = Query("general"), token: str = Query("")):
    """Persist a chat message when a deployment cannot keep WebSockets alive.

    Vercel's Python runtime does not provide a durable WebSocket connection.
    The web client uses this endpoint as a small REST fallback and polls
    ``/api/historial`` to pick up messages from the other participant.
    Local native sessions continue to use the WebSocket path.
    """
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    texto = str(body.get("texto", "")).strip()
    if not texto:
        return JSONResponse({"error": "el mensaje está vacío"}, status_code=400)
    if len(texto) > MAX_CHAT:
        return JSONResponse({"error": f"el mensaje supera {MAX_CHAT} caracteres"}, status_code=413)

    nombre = str(body.get("de") or CFG.get("nombre", "dev")).strip()[:120] or "dev"
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sala = sala_de(proyecto)
    sala.guardar_chat(nombre, texto, fecha)
    evento = {"tipo": "chat", "proyecto": sala.nombre, "de": nombre, "texto": texto, "fecha": fecha}
    await enviar_sala(sala, evento)
    return {"ok": True, "mensaje": evento}


def resolver_archivo(proyecto: str, ruta: str) -> Path:
    p_direct = Path(ruta)
    if p_direct.is_absolute():
        return p_direct.resolve()
    sala = sala_de(proyecto)
    try:
        p = ruta_segura(sala, ruta)
        if p.is_file():
            return p
    except Exception:
        pass
    p_ws = (WORKSPACE / ruta).resolve()
    if p_ws == WORKSPACE or WORKSPACE in p_ws.parents:
        return p_ws
    return sala.directorio / ruta


@app.get("/api/archivo")
async def api_archivo(ruta: str, request: Request, proyecto: str = Query("general"), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        p = resolver_archivo(proyecto, ruta)
        if not p.is_file():
            return JSONResponse({"error": "no es un archivo"}, status_code=404)
        texto = p.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "ruta": ruta, "contenido": texto}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/archivo")
async def api_archivo_guardar(ruta: str, request: Request, proyecto: str = Query("general"), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        contenido = body.get("contenido", "")
        p = resolver_archivo(proyecto, ruta)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contenido, encoding="utf-8")
        await difundir_arbol()
        return {"ok": True, "ruta": ruta, "bytes": len(contenido)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/archivo")
async def api_archivo_eliminar(ruta: str, request: Request, proyecto: str = Query("general"), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        p = resolver_archivo(proyecto, ruta)
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
        else:
            return JSONResponse({"error": "archivo o carpeta no existe"}, status_code=404)
        await difundir_arbol()
        return {"ok": True, "ruta": ruta}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/archivo/renombrar")
async def api_archivo_renombrar(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        origen = body.get("origen", "").strip()
        destino = body.get("destino", "").strip()
        proyecto = body.get("proyecto", "general")
        if not origen or not destino:
            return JSONResponse({"error": "rutas requeridas"}, status_code=400)
        p_origen = resolver_archivo(proyecto, origen)
        p_destino = resolver_archivo(proyecto, destino)
        if not p_origen.exists():
            return JSONResponse({"error": f"no existe {origen}"}, status_code=404)
        p_destino.parent.mkdir(parents=True, exist_ok=True)
        p_origen.rename(p_destino)
        await difundir_arbol()
        return {"ok": True, "origen": origen, "destino": destino}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/terminal/exec")
async def api_terminal_exec(request: Request, token: str = Query("")):
    global CURRENT_TERMINAL_CWD
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        cmd = body.get("cmd", "").strip()
        if not cmd:
            return JSONResponse({"error": "comando vacío"}, status_code=400)
        
        # Verify that CURRENT_TERMINAL_CWD exists, fallback to WORKSPACE
        if not CURRENT_TERMINAL_CWD.is_dir():
            CURRENT_TERMINAL_CWD = WORKSPACE
        
        if body.get("reset_cwd"):
            CURRENT_TERMINAL_CWD = WORKSPACE
        elif body.get("target_cwd"):
            cand = Path(body["target_cwd"]).resolve()
            if cand.is_dir():
                CURRENT_TERMINAL_CWD = cand

        start_cwd = CURRENT_TERMINAL_CWD

        # Execute command in bash with CWD tracking sentinel
        # Subshell captures command exit code and prints final $PWD
        script = f'''
cd "{start_cwd}"
{cmd}
__ATL_RET__=$?
printf "\\n__ATL_CWD__:%s\\n" "$PWD"
exit $__ATL_RET__
'''
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(start_cwd),
            env=entorno_limpio(start_cwd),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=40.0)
            stdout_raw = stdout_bytes.decode("utf-8", errors="replace")
            stderr_raw = stderr_bytes.decode("utf-8", errors="replace")

            clean_stdout = stdout_raw
            new_cwd = str(CURRENT_TERMINAL_CWD)

            if "\n__ATL_CWD__:" in stdout_raw:
                parts = stdout_raw.rsplit("\n__ATL_CWD__:", 1)
                clean_stdout = parts[0]
                extracted_path = parts[1].splitlines()[0].strip()
                if extracted_path:
                    cand = Path(extracted_path).resolve()
                    if cand.is_dir():
                        CURRENT_TERMINAL_CWD = cand
                        new_cwd = str(CURRENT_TERMINAL_CWD)
            elif stdout_raw.startswith("__ATL_CWD__:"):
                parts = stdout_raw.split("__ATL_CWD__:", 1)
                clean_stdout = ""
                extracted_path = parts[1].splitlines()[0].strip()
                if extracted_path:
                    cand = Path(extracted_path).resolve()
                    if cand.is_dir():
                        CURRENT_TERMINAL_CWD = cand
                        new_cwd = str(CURRENT_TERMINAL_CWD)

            clean_stderr = "\n".join(
                line for line in stderr_raw.splitlines()
                if "warning: setlocale:" not in line
            ).strip()
            if clean_stderr:
                clean_stderr += "\n"

            return {
                "ok": proc.returncode == 0,
                "cmd": cmd,
                "exit_code": proc.returncode,
                "stdout": clean_stdout,
                "stderr": clean_stderr,
                "cwd": new_cwd,
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "Tiempo de espera agotado (40s)", "exit_code": -1, "cwd": str(CURRENT_TERMINAL_CWD)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/terminal/complete")
async def api_terminal_complete(request: Request, token: str = Query("")):
    global CURRENT_TERMINAL_CWD
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        query = body.get("query", "").strip()
        base_dir = CURRENT_TERMINAL_CWD if CURRENT_TERMINAL_CWD.is_dir() else WORKSPACE
        matches = []
        try:
            for item in base_dir.iterdir():
                suffix = "/" if item.is_dir() else ""
                name = item.name + suffix
                if not query or name.lower().startswith(query.lower()):
                    matches.append(name)
        except Exception:
            pass
        return {"ok": True, "matches": sorted(matches)[:30], "cwd": str(base_dir)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)



@app.get("/api/buscar")
async def api_buscar(q: str, request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    if not q or len(q) < 2:
        return {"resultados": []}
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "grep", "-n", "-I", "-i", q,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE)
        )
        out, _ = await proc.communicate()
        resultados = []
        for line in out.decode("utf-8", errors="replace").splitlines()[:50]:
            parts = line.split(":", 2)
            if len(parts) == 3:
                resultados.append({"archivo": parts[0], "linea": int(parts[1]), "texto": parts[2].strip()})
        return {"ok": True, "resultados": resultados}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/git/status")
async def api_git_status(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain", "-b",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE)
        )
        out, _ = await proc.communicate()
        lines = out.decode("utf-8", errors="replace").splitlines()
        branch = lines[0].replace("##", "").strip() if lines else "main"
        archivos = []
        for line in lines[1:]:
            if line.strip():
                archivos.append({"estado": line[:2].strip(), "archivo": line[3:].strip()})
        return {"ok": True, "branch": branch, "archivos": archivos}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/git/commit")
async def api_git_commit(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        msg = body.get("mensaje", "chore: actualizacion desde Atlantis Studio").strip()
        p1 = await asyncio.create_subprocess_exec("git", "add", "-A", cwd=str(WORKSPACE))
        await p1.wait()
        p2 = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", msg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE)
        )
        out, err = await p2.communicate()
        return {"ok": p2.returncode == 0, "salida": (out or err).decode("utf-8", errors="replace")}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/descargar")
async def api_descargar(ruta: str, request: Request, proyecto: str = Query("general"), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        p = resolver_archivo(proyecto, ruta)
        if not p.is_file():
            return JSONResponse({"error": "no es un archivo"}, status_code=404)
        return FileResponse(p, filename=Path(ruta).name)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------------------------------------------------------- Endpoints IA
@app.get("/api/ai/config")
async def api_get_ai_config(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    cfg = ai_engine.obtener_config_ia()
    # Ocultar key parcial
    key = cfg.get("api_key", "")
    masked = (key[:6] + "..." + key[-4:]) if len(key) > 12 else ("Configurada" if key else "Sin clave")
    return {
        "proveedor": cfg.get("proveedor"),
        "modelo": cfg.get("modelo"),
        "key_status": masked,
        "has_key": bool(key),
    }


@app.post("/api/ai/config")
async def api_set_ai_config(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    prov = body.get("proveedor", "auto")
    key = body.get("api_key", "")
    mod = body.get("modelo", "auto")
    ai_engine.guardar_config_ia(prov, key, mod)
    return {"ok": True, "mensaje": "Configuración de IA actualizada con éxito"}


@app.get("/api/ai/inbox")
async def api_ai_inbox(request: Request, agente: str = Query(""), proyecto: str = Query(""), pendientes: bool = Query(False), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    mensajes = AI_STORE.get("mensajes", [])
    filtrados = []
    for m in mensajes:
        if agente and m.get("para") and m.get("para").lower() != agente.lower() and m.get("para") != "todos":
            continue
        if proyecto and m.get("proyecto") != proyecto:
            continue
        if pendientes and m.get("estado") == "completado":
            continue
        filtrados.append(m)
    return {"mensajes": filtrados, "total": len(filtrados)}


@app.post("/api/ai/msg")
async def api_ai_msg(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    mid = f"ai-{int(time.time())}-{secrets.token_hex(2)}"
    nuevo_msg = {
        "id": mid,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "de": body.get("de", CFG.get("ia_nombre", "antigravity")),
        "para": body.get("para", "todos"),
        "proyecto": body.get("proyecto", "general"),
        "tipo": body.get("tipo", "tarea"),
        "titulo": body.get("titulo", "Mensaje IA"),
        "contenido": body.get("contenido", ""),
        "archivos": body.get("archivos", []),
        "codigo": body.get("codigo", ""),
        "estado": body.get("estado", "pendiente"),
    }
    AI_STORE.setdefault("mensajes", []).append(nuevo_msg)
    _guardar_ai_data(AI_STORE)

    await enviar_todos({"tipo": "ai_msg", "mensaje": nuevo_msg})

    # Si es una petición de revisión o tarea, invocar al motor de IA real
    if nuevo_msg["tipo"] in ("revision", "tarea") or "revisa" in nuevo_msg["contenido"].lower():
        sala = sala_de(nuevo_msg["proyecto"])
        async def procesar_tarea_ia():
            ctx = ""
            if nuevo_msg.get("archivos"):
                try:
                    p = ruta_segura(sala, nuevo_msg["archivos"][0])
                    if p.is_file():
                        ctx = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            resp_ia, cod_ia = await ai_engine.generar_respuesta_ia(
                nuevo_msg["contenido"],
                contexto_archivo=ctx or nuevo_msg.get("codigo", ""),
                nombre_archivo=nuevo_msg.get("archivos", [""])[0],
                agente_nombre="Hermes" if nuevo_msg["de"] != "Hermes" else "Antigravity",
            )
            # Guardar respuesta
            rep_msg = {
                "id": f"rep-{int(time.time())}-{secrets.token_hex(2)}",
                "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "de": "Hermes" if nuevo_msg["de"] != "Hermes" else "Antigravity",
                "para": nuevo_msg["de"],
                "proyecto": nuevo_msg["proyecto"],
                "tipo": "respuesta",
                "titulo": f"Re: {nuevo_msg['titulo']}",
                "contenido": resp_ia,
                "codigo": cod_ia or "",
                "estado": "completado",
                "respuesta_a": mid,
            }
            nuevo_msg["estado"] = "completado"
            AI_STORE["mensajes"].append(rep_msg)
            _guardar_ai_data(AI_STORE)
            await enviar_todos({"tipo": "ai_msg", "mensaje": rep_msg})
            # Chat en sala
            sala.guardar_chat(rep_msg["de"], resp_ia, rep_msg["fecha"])
            await enviar_sala(sala, {
                "tipo": "chat", "proyecto": sala.nombre,
                "de": f"🤖 {rep_msg['de']}", "texto": resp_ia,
                "fecha": rep_msg["fecha"], "codigo": cod_ia,
            })
        asyncio.create_task(procesar_tarea_ia())

    return {"ok": True, "mensaje": nuevo_msg}


@app.get("/api/ai/tasks")
async def api_ai_tasks(request: Request, proyecto: str = Query(""), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    tareas = AI_STORE.get("tareas", [])
    if proyecto:
        tareas = [t for t in tareas if t.get("proyecto") == proyecto]
    return {"tareas": tareas}


@app.post("/api/ai/task")
async def api_ai_task(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    tid = body.get("id") or f"task-{int(time.time())}-{secrets.token_hex(2)}"
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tarea = {
        "id": tid,
        "proyecto": body.get("proyecto", "general"),
        "titulo": body.get("titulo", "Nueva tarea"),
        "descripcion": body.get("descripcion", ""),
        "asignado": body.get("asignado", "todos"),
        "creador": body.get("creador", "usuario"),
        "estado": body.get("estado", "todo"),
        "prioridad": body.get("prioridad", "media"),
        "archivos": body.get("archivos", []),
        "creado": ahora,
        "actualizado": ahora,
    }
    tareas = AI_STORE.setdefault("tareas", [])
    existente = next((t for t in tareas if t["id"] == tid), None)
    if existente:
        existente.update(tarea)
    else:
        tareas.append(tarea)
    _guardar_ai_data(AI_STORE)
    await enviar_todos({"tipo": "ai_task_update", "tarea": tarea})
    return {"ok": True, "tarea": tarea}


# ---------------------------------------------------------------- Endpoints Reales Hermes & Gemini
@app.get("/api/ai/status")
async def api_ai_status(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    status = await ai_engine.verificar_estado_ia()
    return status


@app.post("/api/ai/hermes/run")
async def api_ai_hermes_run(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt requerido"}, status_code=400)

    proyecto = body.get("proyecto", "")
    sala = sala_de(proyecto) if proyecto else None
    cwd_dir = Path(sala.directorio) if (sala and Path(sala.directorio).is_dir()) else WORKSPACE
    yolo = body.get("yolo", True)

    await enviar_todos({
        "tipo": "hermes_stream_start",
        "prompt": prompt,
        "proyecto": proyecto or "general",
    })

    def on_chunk(chunk: str):
        asyncio.create_task(enviar_todos({
            "tipo": "hermes_stream_chunk",
            "chunk": chunk,
            "proyecto": proyecto or "general",
        }))

    ret, salida = await ai_engine.ejecutar_hermes(prompt, cwd=cwd_dir, yolo=yolo, on_chunk=on_chunk)

    await enviar_todos({
        "tipo": "hermes_stream_end",
        "retcode": ret,
        "salida": salida,
        "proyecto": proyecto or "general",
    })

    await difundir_arbol()
    return {"ok": ret == 0, "retcode": ret, "salida": salida}


@app.post("/api/ai/gemini/run")
async def api_ai_gemini_run(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt requerido"}, status_code=400)

    contexto = body.get("contexto", "")
    nombre_archivo = body.get("nombre_archivo", "")
    modelo = body.get("modelo", "gemini-3.6-flash")
    historial = body.get("historial", [])

    texto, codigo = await ai_engine.consultar_gemini(
        prompt,
        contexto_archivo=contexto,
        nombre_archivo=nombre_archivo,
        modelo=modelo,
        historial=historial,
    )
    return {"ok": True, "texto": texto, "codigo": codigo}


@app.post("/api/ai/dual/run")
async def api_ai_dual_run(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    objetivo = body.get("objetivo", "").strip()
    if not objetivo:
        return JSONResponse({"error": "objetivo requerido"}, status_code=400)

    proyecto = body.get("proyecto", "")
    sala = sala_de(proyecto) if proyecto else None
    cwd_dir = Path(sala.directorio) if (sala and Path(sala.directorio).is_dir()) else WORKSPACE

    def on_event(ev: dict):
        asyncio.create_task(enviar_todos(ev))

    resultado = await ai_engine.colaboracion_dual(objetivo, cwd=cwd_dir, on_event=on_event)
    await difundir_arbol()
    return resultado


# ---------------------------------------------------------------- Endpoints de Autenticación de Cuentas Atlantis
@app.post("/api/auth/register")
async def api_auth_register(request: Request):
    body = await request.json()
    username = str(body.get("username", "")).strip().lower()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", "")).strip()
    name = str(body.get("name", "")).strip() or username.capitalize()
    role = str(body.get("role", "Lead Developer / Arquitecto")).strip()

    if not username or len(username) < 3:
        return JSONResponse({"error": "El nombre de usuario debe tener al menos 3 caracteres."}, status_code=400)
    if not email or "@" not in email:
        return JSONResponse({"error": "Introduce un correo electrónico válido."}, status_code=400)
    if not password or len(password) < 4:
        return JSONResponse({"error": "La contraseña debe tener al menos 4 caracteres."}, status_code=400)

    cuentas = cargar_cuentas()
    for c in cuentas:
        if c.get("username", "").lower() == username:
            return JSONResponse({"error": f"El usuario '{username}' ya está registrado."}, status_code=400)
        if c.get("email", "").lower() == email:
            return JSONResponse({"error": f"El correo '{email}' ya está registrado."}, status_code=400)

    p_hash, salt = hash_password(password)
    initials = "".join([part[0] for part in name.split()[:2]]).upper() or username[:2].upper()
    user_id = f"usr_{secrets.token_hex(6)}"

    new_user = {
        "id": user_id,
        "username": username,
        "email": email,
        "name": name,
        "role": role,
        "password_hash": p_hash,
        "salt": salt,
        "created_at": datetime.now().isoformat(),
        "avatar": initials,
    }
    cuentas.append(new_user)
    guardar_cuentas(cuentas)

    session_token = secrets.token_hex(24)
    user_public = {k: v for k, v in new_user.items() if k not in ("password_hash", "salt")}
    ACTIVE_SESSIONS[session_token] = user_public
    global LAST_AUTHENTICATED_USER
    LAST_AUTHENTICATED_USER = user_public

    return {
        "ok": True,
        "message": f"Cuenta '{username}' creada exitosamente.",
        "token": session_token,
        "user": user_public,
    }


@app.post("/api/auth/login")
async def api_auth_login(request: Request):
    body = await request.json()
    identifier = str(body.get("identifier", "")).strip().lower()
    password = str(body.get("password", "")).strip()

    if not identifier or not password:
        return JSONResponse({"error": "Introduce usuario/correo y contraseña."}, status_code=400)

    cuentas = cargar_cuentas()
    matched_user = None
    for c in cuentas:
        if c.get("username", "").lower() == identifier or c.get("email", "").lower() == identifier:
            matched_user = c
            break

    if not matched_user:
        return JSONResponse({"error": "Usuario o correo no encontrado."}, status_code=404)

    salt = matched_user.get("salt", "")
    p_hash = matched_user.get("password_hash", "")
    if not verify_password(password, p_hash, salt):
        return JSONResponse({"error": "Contraseña incorrecta."}, status_code=401)

    session_token = secrets.token_hex(24)
    user_public = {k: v for k, v in matched_user.items() if k not in ("password_hash", "salt")}
    ACTIVE_SESSIONS[session_token] = user_public
    global LAST_AUTHENTICATED_USER
    LAST_AUTHENTICATED_USER = user_public

    return {
        "ok": True,
        "message": f"Bienvenido, {user_public.get('name')}.",
        "token": session_token,
        "user": user_public,
    }


@app.get("/api/auth/me")
async def api_auth_me(request: Request, token: str = Query("")):
    # 1. Comprobar sesión por token
    auth_header = request.headers.get("Authorization", "")
    tok = token or (auth_header.split(" ")[1] if "Bearer " in auth_header else "")
    if tok and tok in ACTIVE_SESSIONS:
        return {"ok": True, "user": ACTIVE_SESSIONS[tok]}

    # 2. Si no hay token explícito pero hay última sesión autenticada
    if LAST_AUTHENTICATED_USER:
        return {"ok": True, "user": LAST_AUTHENTICATED_USER}

    # 3. Retornar cuenta por defecto en local si existe
    cuentas = cargar_cuentas()
    if cuentas:
        default_user = {k: v for k, v in cuentas[0].items() if k not in ("password_hash", "salt")}
        return {"ok": True, "user": default_user}

    return JSONResponse({"error": "No autenticado"}, status_code=401)


@app.post("/api/auth/logout")
async def api_auth_logout(request: Request, token: str = Query("")):
    auth_header = request.headers.get("Authorization", "")
    tok = token or (auth_header.split(" ")[1] if "Bearer " in auth_header else "")
    if tok and tok in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS.pop(tok, None)
    global LAST_AUTHENTICATED_USER
    LAST_AUTHENTICATED_USER = None
    return {"ok": True, "message": "Sesión cerrada correctamente."}


# ---------------------------------------------------------------- Endpoints de Hermes Agent y Vinculación Dual
@app.get("/api/hermes/detect")
async def api_hermes_detect(request: Request):
    hermes_dir = HOME / ".hermes"
    hermes_bin = ai_engine.detectar_hermes_bin()
    cfg_yaml = hermes_dir / "config.yaml"

    agent_name = "Koko"
    model_name = "nemotron-3-ultra-free"
    provider_name = "opencode-free"
    gateway_running = False

    cfg_json = hermes_dir / "config.json"
    cfg_data = None

    if cfg_yaml.exists():
        try:
            import yaml
            cfg_data = yaml.safe_load(cfg_yaml.read_text(encoding="utf-8"))
        except Exception:
            pass
    elif cfg_json.exists():
        try:
            cfg_data = json.loads(cfg_json.read_text(encoding="utf-8"))
        except Exception:
            pass

    if isinstance(cfg_data, dict):
        # 1. Modelo y proveedor
        m = cfg_data.get("model", {})
        if isinstance(m, dict):
            model_name = m.get("default", m.get("name", model_name))
            provider_name = m.get("provider", provider_name)
        elif isinstance(m, str):
            model_name = m

        # 2. Nombre del agente
        agent_cfg = cfg_data.get("agent", {})
        if isinstance(agent_cfg, dict):
            if agent_cfg.get("name"):
                agent_name = str(agent_cfg["name"]).strip()
            sys_prompt = agent_cfg.get("system_prompt", "")
            if "Tu nombre es " in sys_prompt:
                match = re.search(r"Tu nombre es\s+([A-Za-z0-9_\-]+)", sys_prompt)
                if match:
                    agent_name = match.group(1)
        elif cfg_data.get("name"):
            agent_name = str(cfg_data["name"]).strip()

    if os.environ.get("HERMES_AGENT_NAME"):
        agent_name = os.environ["HERMES_AGENT_NAME"].strip()

    gw_sock = hermes_dir / "gateway.sock"
    gw_state_file = hermes_dir / "gateway_state.json"
    if gw_sock.exists() or gw_state_file.exists():
        gateway_running = True

    return {
        "ok": True,
        "installed": hermes_bin is not None,
        "hermes_bin": str(hermes_bin) if hermes_bin else "",
        "hermes_dir": str(hermes_dir),
        "agent_name": agent_name,
        "model": model_name,
        "provider": provider_name,
        "gateway_running": gateway_running,
    }


@app.get("/api/hermes/status")
async def api_hermes_status(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {
        "ok": True,
        "state": HERMES_STATE,
        "workspace": str(WORKSPACE),
        "workspace_name": WORKSPACE.name,
    }


@app.post("/api/hermes/connect")
async def api_hermes_connect(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        name = str(body.get("name", "Hermes-Daniel")).strip() or "Hermes-Daniel"
        role = str(body.get("role", "Creador Autónomo")).strip()
        engine = str(body.get("engine", "local"))
        provider = str(body.get("provider", "gemini")).strip().lower()
        model = str(body.get("model", "gemini-3.6-flash")).strip()
        endpoint = str(body.get("endpoint", ""))
        api_key = str(body.get("api_key", "")).strip()

        # Guardar configuración en ai_engine de forma persistente
        try:
            ai_engine.guardar_config_ia(provider, api_key, model)
        except Exception as e:
            print(f"[HermesConnect] Aviso guardando config IA: {e}")

        HERMES_STATE["my_agent"].update({
            "name": name,
            "role": role,
            "engine": engine,
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "status": "connected",
            "model_agnostic": True,
            "capabilities": ["terminal_pty", "bash", "filesystem", "git", "antigravity_skills", "dual_pairing"],
        })

        try:
            await enviar_todos({
                "tipo": "hermes_status",
                "state": HERMES_STATE,
            })
        except Exception as e:
            print(f"[HermesConnect] Aviso en broadcast: {e}")

        return {
            "ok": True,
            "message": f"Agente {name} activo con modelo '{model}' ({provider}). La arquitectura modelo-agnóstica mantiene todas sus herramientas y enlace dual sincronizados.",
            "agent": HERMES_STATE["my_agent"],
            "state": HERMES_STATE,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Error conectando agente: {e}"}, status_code=500)


@app.post("/api/hermes/pair")
async def api_hermes_pair(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        pair_code = str(body.get("pair_code", "")).strip()
        friend_name = str(body.get("friend_name", "Hermes-Amigo")).strip() or "Hermes-Amigo"
        mode = str(body.get("mode", "dual_local"))
        endpoint = str(body.get("endpoint", ""))

        HERMES_STATE["friend_agent"].update({
            "name": friend_name,
            "role": "Auditor & Co-piloto",
            "engine": mode,
            "endpoint": endpoint,
            "status": "connected",
        })
        HERMES_STATE["paired"] = True
        HERMES_STATE["pair_timestamp"] = datetime.now().isoformat()
        if pair_code:
            HERMES_STATE["pair_code"] = pair_code

        my_name = HERMES_STATE["my_agent"]["name"]
        try:
            social_matrix.crear_post(
                autor=my_name,
                contenido=f"⚡ Red Hermes Dual enlazada: {my_name} y {friend_name} ahora colaboran de forma sincronizada en el workspace.",
                tags=["hermes", "enlace", "colaboracion"],
            )
        except Exception as e:
            print(f"[HermesPair] Nota social omitida: {e}")

        try:
            await enviar_todos({
                "tipo": "hermes_paired",
                "state": HERMES_STATE,
            })
        except Exception as e:
            print(f"[HermesPair] Aviso en broadcast: {e}")

        return {
            "ok": True,
            "message": f"Enlace dual establecido entre {my_name} y {friend_name}.",
            "state": HERMES_STATE,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Error vinculando agentes: {e}"}, status_code=500)


@app.post("/api/workspace/select")
async def api_workspace_select(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        mode = str(body.get("mode", "clean"))
        target_path = str(body.get("path", "")).strip()
        project_name = str(body.get("name", "")).strip()

        if mode == "new" and project_name:
            clean_dir = HOME / "AtlantisProjects" / project_name
        elif mode == "existing" and target_path:
            clean_dir = Path(target_path).expanduser().resolve()
        else:
            clean_dir = HOME / "AtlantisProjects" / "sandbox"

        clean_dir.mkdir(parents=True, exist_ok=True)
        set_active_workspace(clean_dir)
        try:
            await difundir_arbol()
        except Exception as e:
            print(f"[WorkspaceSelect] Aviso difundiendo arbol: {e}")

        return {
            "ok": True,
            "workspace": str(clean_dir),
            "name": clean_dir.name,
            "arbol": _arbol(),
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Error seleccionando workspace: {e}"}, status_code=500)


# ---------------------------------------------------------------- Endpoints de Compatibilidad Antigravity (Plugins, Skills, Reglas, MCP)
@app.get("/api/antigravity/overview")
async def api_antigravity_overview(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    sources = engine.discover_sources()
    plugins = engine.discover_plugins()
    skills = engine.discover_skills()
    rules = engine.discover_rules()
    mcp = engine.discover_mcp()
    return {
        "ok": True,
        "counts": {
            "sources": len(sources),
            "plugins": len(plugins),
            "skills": len(skills),
            "rules": len(rules),
            "mcp": len(mcp),
        },
        "sources": sources,
    }


@app.get("/api/antigravity/sources")
async def api_antigravity_sources(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    return {"ok": True, "sources": engine.discover_sources()}


@app.post("/api/antigravity/sources/add")
async def api_antigravity_sources_add(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    path_str = body.get("path", "").strip()
    if not path_str:
        return JSONResponse({"error": "ruta requerida"}, status_code=400)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    ok = engine.add_custom_source(path_str)
    if not ok:
        return JSONResponse({"error": f"La ruta '{path_str}' no existe"}, status_code=400)
    return {"ok": True, "sources": engine.discover_sources()}


@app.post("/api/antigravity/sources/remove")
async def api_antigravity_sources_remove(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    path_str = body.get("path", "").strip()
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    engine.remove_custom_source(path_str)
    return {"ok": True, "sources": engine.discover_sources()}


@app.get("/api/antigravity/plugins")
async def api_antigravity_plugins(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    return {"ok": True, "plugins": engine.discover_plugins()}


@app.post("/api/antigravity/plugins/toggle")
async def api_antigravity_plugin_toggle(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    plug_id = body.get("id")
    enabled = body.get("enabled", True)
    if not plug_id:
        return JSONResponse({"error": "id requerido"}, status_code=400)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    engine.toggle_item(plug_id, enabled)
    return {"ok": True, "id": plug_id, "enabled": enabled}


@app.post("/api/antigravity/plugins/create")
async def api_antigravity_plugin_create(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    plug_id = body.get("id", "").strip()
    name = body.get("name", "").strip() or plug_id
    desc = body.get("description", "").strip() or "Plugin de Antigravity"
    version = body.get("version", "1.0.0")
    if not plug_id:
        return JSONResponse({"error": "id requerido"}, status_code=400)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    res = engine.install_plugin(plug_id, name, desc, version)
    await difundir_arbol()
    return {"ok": True, "plugin": res}


@app.get("/api/antigravity/skills")
async def api_antigravity_skills(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    return {"ok": True, "skills": engine.discover_skills()}


@app.get("/api/antigravity/skills/content")
async def api_antigravity_skill_content(request: Request, id: str = Query(""), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    skills = engine.discover_skills()
    found = next((s for s in skills if s["id"] == id), None)
    if not found:
        return JSONResponse({"error": "Skill no encontrado"}, status_code=404)
    skill_file = Path(found["skill_file"])
    content = skill_file.read_text(encoding="utf-8", errors="replace") if skill_file.is_file() else ""
    return {"ok": True, "skill": found, "content": content}


@app.post("/api/antigravity/skills/toggle")
async def api_antigravity_skill_toggle(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    skill_id = body.get("id")
    enabled = body.get("enabled", True)
    if not skill_id:
        return JSONResponse({"error": "id requerido"}, status_code=400)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    engine.toggle_item(skill_id, enabled)
    return {"ok": True, "id": skill_id, "enabled": enabled}


@app.post("/api/antigravity/skills/create")
async def api_antigravity_skill_create(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    skill_id = body.get("id", "").strip()
    name = body.get("name", "").strip() or skill_id
    desc = body.get("description", "").strip() or "Habilidad Antigravity"
    instructions = body.get("instructions", "").strip() or "Instrucciones de la habilidad..."
    if not skill_id:
        return JSONResponse({"error": "id requerido"}, status_code=400)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    res = engine.install_skill(skill_id, name, desc, instructions)
    await difundir_arbol()
    return {"ok": True, "skill": res}


@app.post("/api/antigravity/skills/run")
async def api_antigravity_skill_run(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    skill_id = body.get("id", "")
    custom_prompt = body.get("prompt", "").strip()
    proyecto = body.get("proyecto", "general")
    sala = sala_de(proyecto) if proyecto else None
    cwd_dir = Path(sala.directorio) if (sala and Path(sala.directorio).is_dir()) else WORKSPACE

    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    skills = engine.discover_skills()
    target_skill = next((s for s in skills if s["id"] == skill_id), None)
    if not target_skill:
        return JSONResponse({"error": f"Skill '{skill_id}' no encontrado"}, status_code=404)

    skill_file = Path(target_skill["skill_file"])
    instructions = skill_file.read_text(encoding="utf-8", errors="replace") if skill_file.is_file() else ""

    run_prompt = (
        f"Ejecuta la habilidad de Antigravity '{target_skill['name']}' (ID: {skill_id}).\n"
        f"Ruta de la habilidad: {target_skill['path']}\n\n"
        f"INSTRUCCIONES DE LA HABILIDAD (SKILL.md):\n{instructions}\n\n"
        f"OBJETIVO ESPECÍFICO DEL USUARIO:\n{custom_prompt or 'Ejecuta el procedimiento principal de esta habilidad según las directrices descritas.'}"
    )

    await enviar_todos({
        "tipo": "hermes_stream_start",
        "prompt": f"⚡ Ejecutando Antigravity Skill: {target_skill['name']}",
        "proyecto": proyecto,
    })

    def on_chunk(chunk: str):
        asyncio.create_task(enviar_todos({
            "tipo": "hermes_stream_chunk",
            "chunk": chunk,
            "proyecto": proyecto,
        }))

    ret, salida = await ai_engine.ejecutar_hermes(run_prompt, cwd=cwd_dir, yolo=True, on_chunk=on_chunk)

    await enviar_todos({
        "tipo": "hermes_stream_end",
        "retcode": ret,
        "salida": salida,
        "proyecto": proyecto,
    })
    await difundir_arbol()
    return {"ok": ret == 0, "retcode": ret, "salida": salida, "skill": target_skill["name"]}


@app.get("/api/antigravity/rules")
async def api_antigravity_rules(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    return {"ok": True, "rules": engine.discover_rules()}


@app.get("/api/antigravity/mcp")
async def api_antigravity_mcp(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(WORKSPACE)
    return {"ok": True, "mcp": engine.discover_mcp()}


# ---------------------------------------------------------------- Social Matrix (Red Social 4 Entidades)
@app.get("/api/social/feed")
async def api_social_feed(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    feed = social_matrix.cargar_feed()
    return {
        "ok": True,
        "feed": feed,
        "entidades": social_matrix.ENTIDADES,
        "presencia": social_matrix.ESTADO_PRESENCIA,
    }


@app.get("/api/social/entities")
async def api_social_entities(request: Request, token: str = Query("")):
    return {
        "entidades": social_matrix.ENTIDADES,
        "presencia": social_matrix.ESTADO_PRESENCIA,
    }


@app.post("/api/social/post")
async def api_social_post(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "JSON mal formado"}, status_code=400)

    autor = body.get("autor", "daniel")
    contenido = body.get("contenido", "").strip()
    codigo = body.get("codigo")
    archivo = body.get("archivo")
    tags = body.get("tags", [])
    auto_debate = bool(body.get("debate", False))

    if not contenido:
        return JSONResponse({"error": "El contenido no puede estar vacío"}, status_code=400)

    nuevo_post = social_matrix.crear_post(autor, contenido, codigo=codigo, archivo=archivo, tags=tags)
    await enviar_todos({"tipo": "social_new_post", "post": nuevo_post})

    if auto_debate:
        async def _run_debate():
            def _on_ev(ev):
                asyncio.create_task(enviar_todos(ev))
            await social_matrix.ejecutar_debate_dual_feed(
                nuevo_post["id"],
                contenido,
                codigo=codigo,
                archivo=archivo,
                cwd=WORKSPACE,
                on_evento=_on_ev,
            )
        asyncio.create_task(_run_debate())
    else:
        async def _run_menciones():
            def _on_ia_rep(ev):
                asyncio.create_task(enviar_todos(ev))
            await social_matrix.procesar_menciones_feed(
                nuevo_post["id"],
                contenido,
                codigo=codigo,
                archivo=archivo,
                cwd=WORKSPACE,
                on_ia_reply=_on_ia_rep,
            )
        asyncio.create_task(_run_menciones())

    return {"ok": True, "post": nuevo_post}


@app.post("/api/social/reply")
async def api_social_reply(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "JSON mal formado"}, status_code=400)

    post_id = body.get("post_id")
    autor = body.get("autor", "daniel")
    contenido = body.get("contenido", "").strip()
    codigo = body.get("codigo")

    if not post_id or not contenido:
        return JSONResponse({"error": "Faltan campos obligatorios"}, status_code=400)

    resp = social_matrix.agregar_respuesta(post_id, autor, contenido, codigo=codigo)
    if not resp:
        return JSONResponse({"error": "Post no encontrado"}, status_code=404)

    await enviar_todos({"tipo": "social_new_reply", "post_id": post_id, "respuesta": resp})

    async def _run_menciones_rep():
        def _on_ia_rep(ev):
            asyncio.create_task(enviar_todos(ev))
        await social_matrix.procesar_menciones_feed(
            post_id,
            contenido,
            codigo=codigo,
            cwd=WORKSPACE,
            on_ia_reply=_on_ia_rep,
        )
    asyncio.create_task(_run_menciones_rep())

    return {"ok": True, "respuesta": resp}


@app.post("/api/social/react")
async def api_social_react(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "JSON mal formado"}, status_code=400)

    post_id = body.get("post_id")
    emoji = body.get("emoji", "🔥")
    usuario = body.get("usuario", "daniel")
    reply_id = body.get("reply_id")

    res = social_matrix.alternar_reaccion(post_id, emoji, usuario, reply_id=reply_id)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)

    await enviar_todos({
        "tipo": "social_reaction_update",
        "post_id": post_id,
        "reply_id": reply_id,
        "emoji": emoji,
        "reacciones": res["reacciones"],
    })
    return res


@app.post("/api/social/debate")
async def api_social_debate(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "JSON mal formado"}, status_code=400)

    post_id = body.get("post_id")
    tema = body.get("tema", "").strip()
    codigo = body.get("codigo")
    archivo = body.get("archivo")

    if not post_id or not tema:
        return JSONResponse({"error": "Faltan campos obligatorios"}, status_code=400)

    async def _run_debate():
        def _on_ev(ev):
            asyncio.create_task(enviar_todos(ev))
        await social_matrix.ejecutar_debate_dual_feed(
            post_id,
            tema,
            codigo=codigo,
            archivo=archivo,
            cwd=WORKSPACE,
            on_evento=_on_ev,
        )
    asyncio.create_task(_run_debate())
    return {"ok": True, "mensaje": "Debate IA iniciado"}


@app.post("/api/social/apply_code")
async def api_social_apply_code(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "JSON mal formado"}, status_code=400)

    ruta = body.get("ruta", "").strip()
    codigo = body.get("codigo", "")
    if not ruta or not codigo:
        return JSONResponse({"error": "ruta y codigo son obligatorios"}, status_code=400)

    res = social_matrix.aplicar_codigo_a_archivo(ruta, codigo, cwd=WORKSPACE)
    if res.get("ok"):
        await difundir_arbol()
        await enviar_todos({
            "tipo": "sistema",
            "texto": f"💾 Código aplicado a `{ruta}` ({res.get('bytes', 0)} bytes).",
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return res


# ---------------------------------------------------------------- PTY Real Interactive Terminal
@app.websocket("/pty")
async def pty_endpoint(
    ws: WebSocket,
    token: str = Query(""),
    cols: int = Query(80),
    rows: int = Query(24),
    proyecto: str = Query(""),
):
    if not es_local(ws) and not token_ok(token):
        await ws.close(code=4401)
        return

    await ws.accept()

    if not HAS_PTY:
        await ws.send_text("\r\n\x1b[33m[NEXO Cloud] Terminal interactiva PTY deshabilitada en modo Serverless (Vercel).\x1b[0m\r\n\x1b[36mPara una sesión shell local completa, ejecuta NEXO en tu máquina local.\x1b[0m\r\n")
        await asyncio.sleep(1)
        await ws.close()
        return

    master, slave = pty.openpty()
    wsz = struct.pack("HHHH", max(rows, 10), max(cols, 40), 0, 0)
    fcntl.ioctl(master, termios.TIOCSWINSZ, wsz)

    shell = os.environ.get("SHELL", "/bin/zsh" if Path("/bin/zsh").exists() else "/bin/bash")
    cwd_dir = (PROYECTOS / proyecto).resolve() if proyecto and (PROYECTOS / proyecto).is_dir() else WORKSPACE

    proc = subprocess.Popen(
        [shell],
        preexec_fn=os.setsid,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=str(cwd_dir),
        env=entorno_limpio(cwd_dir),
    )
    os.close(slave)

    loop = asyncio.get_running_loop()

    def on_pty_output():
        try:
            data = os.read(master, 8192)
            if data:
                asyncio.run_coroutine_threadsafe(ws.send_bytes(data), loop)
        except Exception:
            pass

    loop.add_reader(master, on_pty_output)

    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg and msg["bytes"]:
                os.write(master, msg["bytes"])
            elif "text" in msg and msg["text"]:
                txt = msg["text"]
                if txt.startswith('{"tipo":"resize"'):
                    try:
                        d = json.loads(txt)
                        c = int(d.get("cols", 80))
                        r = int(d.get("rows", 24))
                        sz = struct.pack("HHHH", max(r, 5), max(c, 10), 0, 0)
                        fcntl.ioctl(master, termios.TIOCSWINSZ, sz)
                    except Exception:
                        pass
                else:
                    os.write(master, txt.encode("utf-8"))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        try:
            loop.remove_reader(master)
        except Exception:
            pass
        try:
            os.close(master)
        except Exception:
            pass
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------- WebSocket de Mensajería y Estado
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(""), nombre: str = Query("")):
    if not es_local(ws) and not token_ok(token):
        await ws.close(code=4401)
        return

    await ws.accept()
    nombre_usuario = (nombre or CFG.get("nombre", "dev")).strip()
    CONECTADOS.add(ws)
    mis_salas: set[Sala] = set()

    # Presencia en la matriz social
    entidad_id = "daniel" if "daniel" in nombre_usuario.lower() else ("amigo" if "amigo" in nombre_usuario.lower() else None)
    if entidad_id:
        social_matrix.actualizar_presencia(entidad_id, "online", "En vivo en NEXO Studio")

    await enviar_todos({
        "tipo": "presencia",
        "conectados": len(CONECTADOS),
        "agentes": AI_STORE.get("agentes", {}),
    })
    # Estado inicial de la Red Social
    await enviar(ws, {
        "tipo": "social_init",
        "feed": social_matrix.cargar_feed(),
        "entidades": social_matrix.ENTIDADES,
        "presencia": social_matrix.ESTADO_PRESENCIA,
    })

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            tipo = msg.get("tipo")
            fecha = datetime.now().strftime("%Y-%m-%d %H:%M")

            if "proyecto" in msg:
                s_obj = sala_de(msg.get("proyecto", "general"))
                s_obj.conectados.add(ws)
                mis_salas.add(s_obj)

            if tipo == "join":
                sala = sala_de(msg.get("proyecto", "general"))
                await enviar_sala(sala, {
                    "tipo": "sistema", "proyecto": sala.nombre,
                    "texto": f"🟢 {nombre_usuario} se unió a la sala", "fecha": fecha,
                }, excepto=ws)
                await enviar(ws, {"tipo": "operacion_ok", "accion": "join", "proyecto": sala.nombre})

            elif tipo == "chat":
                sala = sala_de(msg.get("proyecto", "general"))
                texto = msg.get("texto", "").strip()
                if texto:
                    sala.guardar_chat(nombre_usuario, texto, fecha)
                    await enviar_sala(sala, {
                        "tipo": "chat", "proyecto": sala.nombre,
                        "de": nombre_usuario, "texto": texto, "fecha": fecha,
                    })

                    # Detección y respuesta de IA real
                    texto_lower = texto.lower()
                    mencion_ia = any(k in texto_lower for k in ("@ia", "@antigravity", "@hermes", "@ai", "ia,", "antigravity,", "hermes,")) or msg.get("para_ia")
                    if mencion_ia:
                        agente_target = "Hermes" if "@hermes" in texto_lower else "Antigravity"
                        await enviar_sala(sala, {"tipo": "escribiendo", "proyecto": sala.nombre, "de": f"🤖 {agente_target}"})

                        # Leer archivo si se menciona
                        nombre_archivo = msg.get("archivo", "")
                        ctx_file = ""
                        if nombre_archivo:
                            try:
                                p = ruta_segura(sala, nombre_archivo)
                                if p.is_file():
                                    ctx_file = p.read_text(encoding="utf-8", errors="replace")
                            except Exception:
                                pass

                        async def async_ia_chat():
                            clean_prompt = re.sub(r"@\w+", "", texto).strip()
                            resp_ia, cod_ia = await ai_engine.generar_respuesta_ia(
                                clean_prompt,
                                contexto_archivo=ctx_file,
                                nombre_archivo=nombre_archivo,
                                agente_nombre=agente_target,
                            )
                            f_ia = datetime.now().strftime("%Y-%m-%d %H:%M")
                            sala.guardar_chat(f"🤖 {agente_target}", resp_ia, f_ia)
                            await enviar_sala(sala, {
                                "tipo": "chat", "proyecto": sala.nombre,
                                "de": f"🤖 {agente_target}", "texto": resp_ia,
                                "fecha": f_ia, "codigo": cod_ia,
                            })
                            # Auto-escritura si se solicitó explícitamente modificar el archivo
                            if cod_ia and nombre_archivo and any(k in texto_lower for k in ("escribe", "modifica", "actualiza", "guarda", "reemplaza")):
                                try:
                                    p = ruta_segura(sala, nombre_archivo)
                                    p.write_text(cod_ia, encoding="utf-8")
                                    aviso = f"💾 {agente_target} aplicó los cambios automáticamente en `{nombre_archivo}`."
                                    sala.guardar_sistema(aviso, f_ia)
                                    await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre, "texto": aviso, "fecha": f_ia})
                                    await difundir_arbol()
                                except Exception as err:
                                    print(f"Error escribiendo archivo por IA: {err}")
                        asyncio.create_task(async_ia_chat())

            elif tipo == "escribiendo":
                sala = sala_de(msg.get("proyecto", "general"))
                await enviar_sala(sala, {
                    "tipo": "escribiendo", "proyecto": sala.nombre,
                    "de": nombre_usuario,
                }, excepto=ws)

            elif tipo == "file_lock":
                ruta_lock = msg.get("ruta", "")
                accion_lock = msg.get("accion", "lock")
                if accion_lock == "lock":
                    FILE_LOCKS[ruta_lock] = {"usuario": nombre_usuario, "ts": time.time()}
                else:
                    FILE_LOCKS.pop(ruta_lock, None)
                await enviar_todos({"tipo": "file_locks", "locks": FILE_LOCKS})

            elif tipo == "archivo_escribir":
                sala = sala_de(msg.get("proyecto", "general"))
                try:
                    ruta = msg.get("ruta", "")
                    p = ruta_segura(sala, ruta)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(msg.get("contenido", ""), encoding="utf-8")
                    sala.guardar_sistema(f"{nombre_usuario} guardó {ruta}", fecha)
                    await enviar_sala(sala, {
                        "tipo": "sistema", "proyecto": sala.nombre,
                        "texto": f"💾 {nombre_usuario} guardó {ruta}", "fecha": fecha,
                    })
                    await enviar(ws, {"tipo": "operacion_ok", "accion": "escribir", "ruta": ruta})
                    await difundir_arbol()
                except Exception as e:
                    await enviar(ws, {"tipo": "error", "texto": str(e)})

            elif tipo == "archivo_borrar":
                sala = sala_de(msg.get("proyecto", "general"))
                try:
                    ruta = msg.get("ruta", "")
                    p = ruta_segura(sala, ruta)
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
                    sala.guardar_sistema(f"{nombre_usuario} eliminó {ruta}", fecha)
                    await enviar_sala(sala, {
                        "tipo": "sistema", "proyecto": sala.nombre,
                        "texto": f"🗑 {nombre_usuario} eliminó {ruta}", "fecha": fecha,
                    })
                    await enviar(ws, {"tipo": "operacion_ok", "accion": "borrar", "ruta": ruta})
                    await difundir_arbol()
                except Exception as e:
                    await enviar(ws, {"tipo": "error", "texto": str(e)})

            elif tipo == "proyecto_nuevo":
                n_proy = Path(msg.get("nombre", "")).name.strip()
                if n_proy:
                    sala_nueva = sala_de(n_proy)
                    sala_nueva.guardar_sistema(f"Proyecto creado por {nombre_usuario}", fecha)
                    await enviar_todos({
                        "tipo": "sistema", "proyecto": sala_nueva.nombre,
                        "texto": f"📁 Proyecto '{n_proy}' creado por {nombre_usuario}",
                        "fecha": fecha,
                    })
                    await difundir_arbol()

            elif tipo == "arbol":
                await enviar(ws, {"tipo": "arbol", "arbol": _arbol()})

            elif tipo == "hermes_run":
                p_text = msg.get("prompt", "").strip()
                if p_text:
                    p_proy = msg.get("proyecto", "")
                    s_obj = sala_de(p_proy) if p_proy else None
                    c_dir = Path(s_obj.directorio) if (s_obj and Path(s_obj.directorio).is_dir()) else WORKSPACE
                    await enviar_todos({"tipo": "hermes_stream_start", "prompt": p_text, "proyecto": p_proy})
                    def _hk(chunk: str):
                        asyncio.create_task(enviar_todos({"tipo": "hermes_stream_chunk", "chunk": chunk, "proyecto": p_proy}))
                    async def _run_h():
                        ret, sal = await ai_engine.ejecutar_hermes(p_text, cwd=c_dir, yolo=True, on_chunk=_hk)
                        await enviar_todos({"tipo": "hermes_stream_end", "retcode": ret, "salida": sal, "proyecto": p_proy})
                        await difundir_arbol()
                    asyncio.create_task(_run_h())

            elif tipo == "gemini_run":
                p_text = msg.get("prompt", "").strip()
                if p_text:
                    async def _run_g():
                        resp_t, cod_t = await ai_engine.consultar_gemini(
                            p_text,
                            contexto_archivo=msg.get("contexto", ""),
                            nombre_archivo=msg.get("nombre_archivo", ""),
                            modelo=msg.get("modelo", "gemini-3.6-flash"),
                        )
                        await enviar(ws, {"tipo": "gemini_response", "texto": resp_t, "codigo": cod_t, "id": msg.get("id")})
                    asyncio.create_task(_run_g())

            elif tipo == "dual_run":
                obj_text = msg.get("objetivo", "").strip()
                if obj_text:
                    p_proy = msg.get("proyecto", "")
                    s_obj = sala_de(p_proy) if p_proy else None
                    c_dir = Path(s_obj.directorio) if (s_obj and Path(s_obj.directorio).is_dir()) else WORKSPACE
                    def _ev_cb(ev: dict):
                        asyncio.create_task(enviar_todos(ev))
                    async def _run_d():
                        res = await ai_engine.colaboracion_dual(obj_text, cwd=c_dir, on_event=_ev_cb)
                        await enviar_todos({"tipo": "dual_colab_finish", "resultado": res})
                        await difundir_arbol()
                    asyncio.create_task(_run_d())

            # --- Eventos Red Social (4 Entidades) ---
            elif tipo == "social_post":
                s_autor = msg.get("autor", entidad_id or "daniel")
                s_cont = msg.get("contenido", "").strip()
                s_cod = msg.get("codigo")
                s_arch = msg.get("archivo")
                s_tags = msg.get("tags", [])
                s_deb = bool(msg.get("debate", False))
                if s_cont:
                    nuevo_p = social_matrix.crear_post(s_autor, s_cont, codigo=s_cod, archivo=s_arch, tags=s_tags)
                    await enviar_todos({"tipo": "social_new_post", "post": nuevo_p})
                    if s_deb:
                        async def _run_deb_ws():
                            def _on_deb_ev(ev):
                                asyncio.create_task(enviar_todos(ev))
                            await social_matrix.ejecutar_debate_dual_feed(
                                nuevo_p["id"], s_cont, codigo=s_cod, archivo=s_arch, cwd=WORKSPACE, on_evento=_on_deb_ev
                            )
                        asyncio.create_task(_run_deb_ws())
                    else:
                        async def _run_menc_ws():
                            def _on_menc_rep(ev):
                                asyncio.create_task(enviar_todos(ev))
                            await social_matrix.procesar_menciones_feed(
                                nuevo_p["id"], s_cont, codigo=s_cod, archivo=s_arch, cwd=WORKSPACE, on_ia_reply=_on_menc_rep
                            )
                        asyncio.create_task(_run_menc_ws())

            elif tipo == "social_reply":
                s_pid = msg.get("post_id")
                s_autor = msg.get("autor", entidad_id or "daniel")
                s_cont = msg.get("contenido", "").strip()
                s_cod = msg.get("codigo")
                if s_pid and s_cont:
                    s_r = social_matrix.agregar_respuesta(s_pid, s_autor, s_cont, codigo=s_cod)
                    if s_r:
                        await enviar_todos({"tipo": "social_new_reply", "post_id": s_pid, "respuesta": s_r})
                        async def _run_rep_menc():
                            def _on_menc_ev(ev):
                                asyncio.create_task(enviar_todos(ev))
                            await social_matrix.procesar_menciones_feed(
                                s_pid, s_cont, codigo=s_cod, cwd=WORKSPACE, on_ia_reply=_on_menc_ev
                            )
                        asyncio.create_task(_run_rep_menc())

            elif tipo == "social_react":
                s_pid = msg.get("post_id")
                s_emoji = msg.get("emoji", "🔥")
                s_usr = msg.get("usuario", entidad_id or "daniel")
                s_rid = msg.get("reply_id")
                if s_pid:
                    s_res = social_matrix.alternar_reaccion(s_pid, s_emoji, s_usr, reply_id=s_rid)
                    if s_res.get("ok"):
                        await enviar_todos({
                            "tipo": "social_reaction_update",
                            "post_id": s_pid,
                            "reply_id": s_rid,
                            "emoji": s_emoji,
                            "reacciones": s_res["reacciones"],
                        })

            elif tipo == "social_debate":
                s_pid = msg.get("post_id")
                s_tema = msg.get("tema", "").strip()
                s_cod = msg.get("codigo")
                s_arch = msg.get("archivo")
                if s_pid and s_tema:
                    async def _run_manual_deb():
                        def _on_ev(ev):
                            asyncio.create_task(enviar_todos(ev))
                        await social_matrix.ejecutar_debate_dual_feed(
                            s_pid, s_tema, codigo=s_cod, archivo=s_arch, cwd=WORKSPACE, on_evento=_on_ev
                        )
                    asyncio.create_task(_run_manual_deb())

            elif tipo == "social_presence":
                s_ent = msg.get("entidad")
                s_est = msg.get("estado", "online")
                s_act = msg.get("actividad", "")
                if s_ent:
                    pres_act = social_matrix.actualizar_presencia(s_ent, s_est, s_act)
                    await enviar_todos({"tipo": "social_presence_update", "presencia": pres_act})

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        CONECTADOS.discard(ws)
        for s in mis_salas:
            s.conectados.discard(ws)
        locks_del = [k for k, v in FILE_LOCKS.items() if v.get("usuario") == nombre_usuario]
        for k in locks_del:
            FILE_LOCKS.pop(k, None)
        await enviar_todos({
            "tipo": "presencia",
            "conectados": len(CONECTADOS),
            "file_locks": FILE_LOCKS,
        })


# ---------------------------------------------------------------- Main
if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 68)
    print("🚀 NEXO 2.0 Atlantis — Servidor de Desarrollo Real & Matriz IA")
    print("=" * 68)
    print(f"• URL Local:        http://127.0.0.1:{PORT}")
    print(f"• Workspace:        {WORKSPACE}")
    print("• Terminal PTY:     Nativa (/pty)")
    print("• Motor de IA:      Activo (ai_engine)")
    print(f"• Token:            {TOKEN}")
    print("=" * 68 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
