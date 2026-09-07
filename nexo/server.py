#!/usr/bin/env python3
from __future__ import annotations

"""NEXO 2.0 — Espacio de trabajo colaborativo para dos desarrolladores e IAs (Antigravity & Hermes).

Servidor local (FastAPI + WebSocket):
  - Árbol de proyectos con archivos en tiempo real
  - Chat compartido por proyecto, persistido en .nexo-chat.md
  - Capa de comunicación estructurada IA-a-IA (Buzón, Tareas Kanban, Presencia)
  - Notificaciones de edición simultánea y bloqueo preventivo de archivos
  - Terminal en vivo con streaming de salida
  - Editor colaborativo y gestión de archivos (leer, escribir, borrar, renombrar, subir, descargar)
  - Integración Git / GitHub (status, add, commit, push, pull, log, diff)
  - Compatible con macOS (Apple Silicon/Intel) y Linux (con sandbox bwrap opcional)
"""

import asyncio
import base64
import hmac
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import signal
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------- Rutas y Workspace
HOME = Path.home()
BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parent

# Detección inteligente del workspace raíz:
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
AI_DATA_FILE = NEXO_DATA_DIR / "ai_colab.json"
CONFIG = BASE / "config.json"
PORT = int(os.environ.get("PORT", "8787"))

MAX_LECTURA = 4 * 1024 * 1024      # 4 MB por archivo en editor
MAX_SUBIDA = 50 * 1024 * 1024      # 50 MB por subida
MAX_CHAT = 8000                    # caracteres por mensaje de chat
MAX_CMD = 4000                     # caracteres por comando
MAX_PROCESOS_SALA = 8              # procesos concurrentes por sala
TIMEOUT_CMD = 600                  # 10 min máx por comando

IS_DARWIN = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def cargar_config() -> dict:
    cfg: dict[str, Any] = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg.setdefault("token", secrets.token_hex(16))
    cfg.setdefault("nombre", "jaime")
    cfg.setdefault("companero", "amigo")
    cfg.setdefault("ia_nombre", "antigravity")
    cfg.setdefault("ia_companero", "hermes")
    cfg.setdefault("tema", "jarvis")     # "jarvis" o "ultron"
    cfg.setdefault("sandbox", True if IS_LINUX and shutil.which("bwrap") else False)
    cfg.setdefault("timeout", TIMEOUT_CMD)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


CFG = cargar_config()
TOKEN = os.environ.get("TOKEN") or CFG["token"]
PROYECTOS.mkdir(parents=True, exist_ok=True)
NEXO_DATA_DIR.mkdir(parents=True, exist_ok=True)

if not (PROYECTOS / "README.md").exists():
    (PROYECTOS / "README.md").write_text(
        "# Proyectos de NEXO\n\nDirectorio de trabajo colaborativo para código compartido.\n",
        encoding="utf-8",
    )

# ---------------------------------------------------------------- Base de Datos IA y Estado
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
                "actividad": "Esperando tareas",
                "ultimo_ping": time.time(),
            },
            CFG.get("ia_companero", "hermes"): {
                "tipo": "hermes",
                "estado": "listo",
                "actividad": "Esperando conexión",
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
FILE_LOCKS: dict[str, dict] = {}   # ruta -> {"usuario": str, "timestamp": float}

# ---------------------------------------------------------------- Seguridad y Sanitización
HOSTNAME = socket.gethostname() or "nexo-host"
USUARIO = HOME.name

RUTAS_SISTEMA_CRITICAS = (
    "/etc/shadow", "/etc/sudoers", "/etc/pam.d",
    "/proc/kcore", "/dev/mem", "/dev/kmem",
    str(HOME / ".ssh"), str(HOME / ".gnupg"),
)

BINARIOS_PELIGROSOS = {
    "sudo", "doas", "su", "passwd", "chpasswd", "useradd", "userdel", "usermod",
    "fdisk", "parted", "mkfs", "dd", "format", "iptables", "nft", "ufw",
}

PATRONES_INYECCION = re.compile(
    r"ignor(a|e|a\s+las|a\s+tus)\s+(las\s+|tus\s+)?(instrucciones|ordenes|órdenes|prompt)"
    r"|ignore\s+(previous|all|your|the)\s+(instructions|prompts?|messages?|context)"
    r"|disregard\s+(previous|all|your|the)"
    r"|override\s+(your|previous|system)"
    r"|jailbreak|dan\s*mode|developer\s+mode"
    r"|system\s+prompt|system\s+message|instrucciones\s+del\s+sistema"
    r"|prompt\s+del\s+sistema|prompt\s+de\s+sistema"
    r"|ahora\s+eres|eres\s+ahora|act[uú]a\s+como\s+si\s+fueras|you\s+are\s+now"
    r"|act\s+as\s+if\s+you\s+are|pretend\s+to\s+be"
    r"|revela\s+tu|dame\s+tu\s+prompt|reveal\s+your|give\s+me\s+your\s+(prompt|instructions)"
    r"|olvida\s+tus\s+instrucciones|forget\s+your\s+instructions"
    r"|no\s+reveles\s+tu|desbloquea\s+tu|responde\s+sin\s+(filtros?|restricciones)"
    r"|ignora\s+lo\s+anterior|ignore\s+everything\s+(above|before|previously)"
    r"|simula\s+ser|pretende\s+ser",
    re.IGNORECASE,
)

RE_IP = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
RE_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")
RE_TOKEN = re.compile(
    r"(?:token|api[_-]?key|secret|password|passwd|clave)\s*[=:]\s*[\"']?[A-Za-z0-9_\-\.]{8,}",
    re.IGNORECASE,
)
RE_SK = re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs]|AKIA)[A-Za-z0-9_\-]{8,}\b")

_BWRAP_OK = shutil.which("bwrap") is not None and bool(CFG.get("sandbox", False)) and IS_LINUX


def _sh_quote(s: str) -> str:
    return shlex.quote(s)


def sanitizar(texto: str) -> str:
    """Redacta tokens sensibles y estandariza la ruta del workspace."""
    if not texto:
        return texto
    t = texto
    t = t.replace(str(WORKSPACE), "/workspace")
    if TOKEN:
        t = t.replace(TOKEN, "[TOKEN]")
    t = RE_TOKEN.sub(lambda m: m.group(0).split("=")[0].split(":")[0] + "=[REDACTADO]", t)
    t = RE_SK.sub("[TOKEN]", t)
    return t


def comando_bloqueado(cmd: str) -> Optional[str]:
    """Valida que el comando sea seguro sin limitar herramientas legítimas de desarrollo."""
    if len(cmd) > MAX_CMD:
        return "comando demasiado largo (>4000 caracteres)"
    lower = cmd.lower()
    for rc in RUTAS_SISTEMA_CRITICAS:
        if rc.lower() in lower:
            return f"acceso a ruta crítica restringido: {rc}"
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return "comando mal formado (comillas sin cerrar)"
    for tok in tokens:
        if "/" in tok:
            tok = Path(tok).name
        base = tok.strip(";|&(){}<>").lower()
        if base in BINARIOS_PELIGROSOS:
            return f"comando de administración restringido: {base}"
    return None


def entorno_limpio(proyecto: Path) -> dict:
    """Entorno de ejecución con PATH de desarrollo completo."""
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
    return {
        "PATH": full_path,
        "HOME": str(HOME),
        "WORKSPACE": str(WORKSPACE),
        "PROYECTO": str(proyecto),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "xterm-256color",
        "TMPDIR": "/tmp",
        "PYTHONUNBUFFERED": "1",
    }


def cmd_sandbox(cmd: str, proyecto: Path, red: bool = True) -> str:
    """Aplica bwrap en Linux si está disponible; en macOS o sin bwrap ejecuta directamente."""
    if not _BWRAP_OK:
        return cmd
    partes = [
        "bwrap", "--die-with-parent", "--new-session",
        "--unshare-uts", "--unshare-ipc", "--unshare-pid",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib" if Path("/lib").exists() else "/usr/lib",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", "/etc/ssl", "/etc/ssl" if Path("/etc/ssl").exists() else "/usr/share/ca-certificates",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", str(WORKSPACE), "/workspace",
        "--chdir", f"/workspace/{proyecto.name}",
    ]
    if not red:
        partes.append("--unshare-net")
    partes += ["/bin/sh", "-c", cmd]
    return " ".join(_sh_quote(p) for p in partes)


def sanitizar_chat(texto: str) -> tuple[str, bool]:
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", texto)
    texto = sanitizar(texto).strip()
    if len(texto) > MAX_CHAT:
        texto = texto[:MAX_CHAT] + "… [truncado]"
    peligro = bool(PATRONES_INYECCION.search(texto))
    return texto, peligro


# ---------------------------------------------------------------- FastAPI App
app = FastAPI(title="NEXO 2.0", description="Collaborative Hub & AI Matrix")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

CONECTADOS: set[WebSocket] = set()
SALAS: dict[str, "Sala"] = {}


def token_ok(token: str) -> bool:
    return hmac.compare_digest(token or "", TOKEN)


# ---------------------------------------------------------------- Modelo Sala
class Sala:
    def __init__(self, nombre: str, directorio: Path):
        self.nombre = nombre
        self.directorio = directorio
        self.conectados: set[WebSocket] = set()
        self.procesos: dict[str, asyncio.subprocess.Process] = {}
        self.chat_file = directorio / ".nexo-chat.md"
        if not self.chat_file.exists():
            cabecera = (
                f"# NEXO Chat — {nombre}\n"
                f"> Sala creada: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                "> Mensajes de desarrolladores e IAs (Antigravity & Hermes).\n\n"
            )
            self.chat_file.write_text(cabecera, encoding="utf-8")

    def historial(self, limite: int = 150) -> list[dict]:
        if not self.chat_file.exists():
            return []
        msgs = []
        pat = re.compile(r"^\*\*\[(.*?)\] (.*?):\*\* (.*)$")
        for linea in self.chat_file.read_text(encoding="utf-8", errors="replace").splitlines():
            m = pat.match(linea)
            if m:
                msgs.append({"fecha": m.group(1), "de": m.group(2), "texto": m.group(3)})
            elif linea.startswith("> "):
                msgs.append({"fecha": "", "de": "sistema", "texto": linea[2:]})
        return msgs[-limite:]

    def guardar_chat(self, de: str, texto: str, fecha: str):
        with self.chat_file.open("a", encoding="utf-8") as f:
            f.write(f"**[{fecha}] {de}:** {texto}\n")

    def guardar_sistema(self, texto: str, fecha: str):
        with self.chat_file.open("a", encoding="utf-8") as f:
            f.write(f"> {texto} ({fecha})\n")


def sala_de(nombre: str) -> Sala:
    nombre_limpio = Path(nombre or "general").name.strip()
    if not nombre_limpio:
        nombre_limpio = "general"
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


def nombre_limpio(nombre: str) -> str:
    limpio = re.sub(r"[^A-Za-z0-9_\-\.\s]", "", nombre or "").strip()
    return (limpio or CFG.get("nombre", "agente"))[:32]


# ---------------------------------------------------------------- Helpers de Red y Envío
async def enviar(ws: WebSocket, msg: dict):
    try:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))
    except Exception:
        pass


async def enviar_sala(sala: Sala, msg: dict, excepto: Optional[WebSocket] = None):
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


def _arbol() -> list[dict]:
    arbol = []
    if not PROYECTOS.exists():
        return arbol
    for p in sorted(PROYECTOS.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        archivos = []
        for f in sorted(p.rglob("*")):
            if f.is_file() and ".git" not in f.parts and f.name != ".nexo-chat.md":
                archivos.append(str(f.relative_to(p)))
        arbol.append({"nombre": p.name, "archivos": archivos})
    return arbol


async def difundir_arbol():
    await enviar_todos({"tipo": "arbol", "arbol": _arbol()})


# ---------------------------------------------------------------- Ejecución de Comandos
async def lanzar(sala: Sala, rid: str, cmd: str, tipo: str = "run"):
    if len(sala.procesos) >= MAX_PROCESOS_SALA:
        await enviar_sala(sala, {
            "tipo": "output", "proyecto": sala.nombre, "id": rid,
            "stream": "stderr", "texto": f"[NEXO-SEC] Límite de {MAX_PROCESOS_SALA} procesos concurrentes alcanzado.\n",
        })
        await enviar_sala(sala, {"tipo": "run_fin", "origen": tipo, "proyecto": sala.nombre, "id": rid, "code": -1})
        return

    motivo = comando_bloqueado(cmd)
    if motivo:
        await enviar_sala(sala, {
            "tipo": "output", "proyecto": sala.nombre, "id": rid,
            "stream": "stderr", "texto": f"[NEXO-SEC] Comando bloqueado: {motivo}\n",
        })
        await enviar_sala(sala, {"tipo": "run_fin", "origen": tipo, "proyecto": sala.nombre, "id": rid, "code": -1})
        return

    env = entorno_limpio(sala.directorio)
    cmd_ejecutable = cmd_sandbox(cmd, sala.directorio, red=True)

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd_ejecutable,
            cwd=sala.directorio,
            env=env,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        await enviar_sala(sala, {
            "tipo": "output", "proyecto": sala.nombre, "id": rid,
            "stream": "stderr", "texto": f"Error al ejecutar: {e}\n",
        })
        await enviar_sala(sala, {"tipo": "run_fin", "origen": tipo, "proyecto": sala.nombre, "id": rid, "code": -1})
        return

    sala.procesos[rid] = proc

    async def leer_stream(stream, nombre_stream):
        while True:
            linea = await stream.readline()
            if not linea:
                break
            texto = sanitizar(linea.decode(errors="replace"))
            await enviar_sala(sala, {
                "tipo": "output", "proyecto": sala.nombre, "id": rid,
                "stream": nombre_stream, "texto": texto,
            })

    t1 = asyncio.create_task(leer_stream(proc.stdout, "stdout"))
    t2 = asyncio.create_task(leer_stream(proc.stderr, "stderr"))

    timeout_dur = CFG.get("timeout", TIMEOUT_CMD)
    try:
        code = await asyncio.wait_for(proc.wait(), timeout=timeout_dur)
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        code = -9
        await enviar_sala(sala, {
            "tipo": "output", "proyecto": sala.nombre, "id": rid,
            "stream": "stderr", "texto": f"\n[NEXO] Proceso terminado por límite de tiempo ({timeout_dur}s).\n",
        })

    await t1
    await t2
    sala.procesos.pop(rid, None)
    await enviar_sala(sala, {
        "tipo": "run_fin", "origen": tipo, "proyecto": sala.nombre,
        "id": rid, "code": code, "cmd": sanitizar(cmd),
    })


def cmd_git(accion: str, msg: Optional[str], nombre: str) -> str:
    ident = f"-c user.name={_sh_quote(nombre)} -c user.email={_sh_quote(nombre + '@nexo.local')}"
    base_ruta = "/workspace" if _BWRAP_OK else str(WORKSPACE)
    base = f"git -C {_sh_quote(base_ruta)} {ident}"
    acciones = {
        "status": f"{base} status --short --branch",
        "add": f"{base} add -A",
        "commit": f"{base} add -A && {base} commit -m {_sh_quote(msg or 'nexo: cambios colaborativos')}",
        "push": f"{base} push origin HEAD",
        "pull": f"{base} pull --rebase",
        "log": f"{base} log --oneline -15",
        "branch": f"{base} branch -a",
        "remote": f"{base} remote -v",
        "diff": f"{base} diff --stat",
        "stash": f"{base} stash",
    }
    return acciones.get(accion, f"{base} status --short --branch")


# ---------------------------------------------------------------- API REST
@app.get("/")
async def raiz():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/status")
async def api_status(token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {
        "sistema": "NEXO 2.0 Atlantis",
        "os": platform.system(),
        "arch": platform.machine(),
        "workspace": str(WORKSPACE),
        "proyectos": len(_arbol()),
        "conectados": len(CONECTADOS),
        "bwrap_sandbox": _BWRAP_OK,
        "config": {
            "nombre": CFG.get("nombre"),
            "companero": CFG.get("companero"),
            "ia_nombre": CFG.get("ia_nombre"),
            "ia_companero": CFG.get("ia_companero"),
            "tema": CFG.get("tema"),
        },
    }


@app.get("/api/arbol")
async def api_arbol(token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {
        "arbol": _arbol(),
        "nombre": CFG.get("nombre", "jaime"),
        "companero": CFG.get("companero", "amigo"),
        "ia_nombre": CFG.get("ia_nombre", "antigravity"),
        "ia_companero": CFG.get("ia_companero", "hermes"),
        "tema": CFG.get("tema", "jarvis"),
        "file_locks": FILE_LOCKS,
    }


@app.get("/api/historial")
async def api_historial(proyecto: str, token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        return {"historial": sala_de(proyecto).historial()}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/archivo")
async def api_archivo(proyecto: str, ruta: str, token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        sala = sala_de(proyecto)
        p = ruta_segura(sala, ruta)
        if not p.is_file():
            return JSONResponse({"error": "no es un archivo"}, status_code=404)
        datos = p.read_bytes()
        if b"\x00" in datos[:8192]:
            return JSONResponse({"error": "archivo binario: usa Descargar"}, status_code=415)
        texto = datos.decode("utf-8", errors="replace")
        truncado = len(datos) > MAX_LECTURA
        if truncado:
            texto = texto[:MAX_LECTURA] + "\n… [archivo truncado por tamaño]"
        return {"ruta": ruta, "contenido": texto, "truncado": truncado}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/descargar")
async def api_descargar(proyecto: str, ruta: str, token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        sala = sala_de(proyecto)
        p = ruta_segura(sala, ruta)
        if not p.is_file():
            return JSONResponse({"error": "no es un archivo"}, status_code=404)
        return FileResponse(p, filename=Path(ruta).name)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------------------------------------------------------- Endpoints IA-a-IA
@app.get("/api/ai/inbox")
async def api_ai_inbox(
    agente: str = Query(""),
    proyecto: str = Query(""),
    pendientes: bool = Query(False),
    token: str = Query(""),
):
    """Buzón estructurado para agentes IA (Antigravity & Hermes)."""
    if not token_ok(token):
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
    return {
        "mensajes": filtrados,
        "total": len(filtrados),
        "agentes": AI_STORE.get("agentes", {}),
    }


@app.post("/api/ai/msg")
async def api_ai_msg(req: Request, token: str = Query("")):
    """Envío de mensaje estructurado entre IAs."""
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "JSON mal formado"}, status_code=400)

    mid = f"ai-{int(time.time())}-{secrets.token_hex(2)}"
    nuevo_msg = {
        "id": mid,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "de": body.get("de", CFG.get("ia_nombre", "antigravity")),
        "para": body.get("para", "todos"),
        "proyecto": body.get("proyecto", "general"),
        "tipo": body.get("tipo", "tarea"),       # tarea, pregunta, respuesta, revision, sync, alerta
        "titulo": body.get("titulo", "Mensaje IA"),
        "contenido": body.get("contenido", ""),
        "archivos": body.get("archivos", []),
        "codigo": body.get("codigo", ""),
        "estado": body.get("estado", "pendiente"), # pendiente, en_progreso, completado
        "respuesta_a": body.get("respuesta_a"),
    }
    AI_STORE.setdefault("mensajes", []).append(nuevo_msg)
    # Conservar últimos 200 mensajes IA
    if len(AI_STORE["mensajes"]) > 200:
        AI_STORE["mensajes"] = AI_STORE["mensajes"][-200:]
    _guardar_ai_data(AI_STORE)

    # Notificar WebSocket en vivo
    await enviar_todos({"tipo": "ai_msg", "mensaje": nuevo_msg})

    # También registrar en el chat del proyecto si aplica
    if nuevo_msg["proyecto"]:
        sala = sala_de(nuevo_msg["proyecto"])
        texto_chat = f"🤖 [IA {nuevo_msg['de']} → {nuevo_msg['para']}] {nuevo_msg['titulo']}: {nuevo_msg['contenido']}"
        sala.guardar_sistema(texto_chat, nuevo_msg["fecha"])
        await enviar_sala(sala, {
            "tipo": "chat", "proyecto": sala.nombre,
            "de": f"🤖 {nuevo_msg['de']}",
            "texto": f"**[{nuevo_msg['tipo'].upper()}] {nuevo_msg['titulo']}**\n{nuevo_msg['contenido']}",
            "fecha": nuevo_msg["fecha"],
        })

    return {"ok": True, "mensaje": nuevo_msg}


@app.post("/api/ai/reply")
async def api_ai_reply(req: Request, token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    mid = body.get("id")
    mensajes = AI_STORE.get("mensajes", [])
    objetivo = next((m for m in mensajes if m["id"] == mid), None)
    if not objetivo:
        return JSONResponse({"error": "mensaje no encontrado"}, status_code=404)

    nuevo_estado = body.get("estado", "completado")
    objetivo["estado"] = nuevo_estado
    respuesta_msg = {
        "id": f"rep-{int(time.time())}-{secrets.token_hex(2)}",
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "de": body.get("de", CFG.get("ia_companero", "hermes")),
        "para": objetivo.get("de", "todos"),
        "proyecto": objetivo.get("proyecto", "general"),
        "tipo": "respuesta",
        "titulo": f"Re: {objetivo.get('titulo')}",
        "contenido": body.get("contenido", ""),
        "codigo": body.get("codigo", ""),
        "estado": nuevo_estado,
        "respuesta_a": mid,
    }
    mensajes.append(respuesta_msg)
    _guardar_ai_data(AI_STORE)

    await enviar_todos({"tipo": "ai_msg", "mensaje": respuesta_msg})
    return {"ok": True, "respuesta": respuesta_msg}


@app.get("/api/ai/tasks")
async def api_ai_tasks(proyecto: str = Query(""), token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    tareas = AI_STORE.get("tareas", [])
    if proyecto:
        tareas = [t for t in tareas if t.get("proyecto") == proyecto]
    return {"tareas": tareas}


@app.post("/api/ai/task")
async def api_ai_task(req: Request, token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    tid = body.get("id")
    tareas = AI_STORE.setdefault("tareas", [])
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if tid:
        tarea = next((t for t in tareas if t["id"] == tid), None)
        if not tarea:
            return JSONResponse({"error": "tarea no encontrada"}, status_code=404)
        if "titulo" in body:
            tarea["titulo"] = body["titulo"]
        if "descripcion" in body:
            tarea["descripcion"] = body["descripcion"]
        if "asignado" in body:
            tarea["asignado"] = body["asignado"]
        if "estado" in body:
            tarea["estado"] = body["estado"]
        if "prioridad" in body:
            tarea["prioridad"] = body["prioridad"]
        if "archivos" in body:
            tarea["archivos"] = body["archivos"]
        tarea["actualizado"] = ahora
    else:
        tid = f"task-{int(time.time())}-{secrets.token_hex(2)}"
        tarea = {
            "id": tid,
            "proyecto": body.get("proyecto", "general"),
            "titulo": body.get("titulo", "Nueva tarea"),
            "descripcion": body.get("descripcion", ""),
            "asignado": body.get("asignado", "todos"),
            "creador": body.get("creador", "usuario"),
            "estado": body.get("estado", "todo"),       # todo, in_progress, done
            "prioridad": body.get("prioridad", "media"), # baja, media, alta, urgente
            "archivos": body.get("archivos", []),
            "creado": ahora,
            "actualizado": ahora,
        }
        tareas.append(tarea)

    _guardar_ai_data(AI_STORE)
    await enviar_todos({"tipo": "ai_task_update", "tarea": tarea})
    return {"ok": True, "tarea": tarea}


@app.post("/api/ai/presence")
async def api_ai_presence(req: Request, token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    agente = body.get("agente")
    if not agente:
        return JSONResponse({"error": "agente requerido"}, status_code=400)
    agentes = AI_STORE.setdefault("agentes", {})
    agentes[agente] = {
        "tipo": body.get("tipo", "ia"),
        "estado": body.get("estado", "activo"),
        "actividad": body.get("actividad", "activo"),
        "ultimo_ping": time.time(),
    }
    _guardar_ai_data(AI_STORE)
    await enviar_todos({"tipo": "ai_presence", "agentes": agentes})
    return {"ok": True, "agentes": agentes}


# ---------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(""), nombre: str = Query("")):
    if not token_ok(token):
        await ws.close(code=4401)
        return

    await ws.accept()
    nombre_usuario = nombre_limpio(nombre)
    CONECTADOS.add(ws)
    mis_salas: set[Sala] = set()

    # Difundir estado inicial
    await enviar_todos({
        "tipo": "presencia",
        "conectados": len(CONECTADOS),
        "agentes": AI_STORE.get("agentes", {}),
    })

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
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

            elif tipo == "proyecto_nuevo":
                nombre_p = Path(msg.get("nombre", "")).name.strip()
                if nombre_p:
                    n = sala_de(nombre_p)
                    n.guardar_sistema(f"Proyecto creado por {nombre_usuario}", fecha)
                    await enviar_todos({
                        "tipo": "sistema", "proyecto": n.nombre,
                        "texto": f"📁 Proyecto '{nombre_p}' creado por {nombre_usuario}",
                        "fecha": fecha,
                    })
                    await difundir_arbol()

            elif tipo == "proyecto_borrar":
                nombre_p = Path(msg.get("nombre", "")).name.strip()
                if msg.get("confirmar") is True and nombre_p:
                    target = (PROYECTOS / nombre_p).resolve()
                    if PROYECTOS.resolve() in target.parents and target.is_dir():
                        shutil.rmtree(target)
                        SALAS.pop(nombre_p, None)
                        await enviar_todos({
                            "tipo": "sistema", "proyecto": nombre_p,
                            "texto": f"🗑 Proyecto '{nombre_p}' eliminado por {nombre_usuario}",
                            "fecha": fecha,
                        })
                        await difundir_arbol()

            elif tipo == "chat":
                sala = sala_de(msg.get("proyecto", "general"))
                sala.conectados.add(ws)
                mis_salas.add(sala)
                texto, peligro = sanitizar_chat(msg.get("texto", ""))
                if texto:
                    if peligro:
                        aviso = f"⚠️ [NEXO-SEC] Mensaje de {nombre_usuario} contiene patrones de inyección detectados."
                        sala.guardar_sistema(aviso, fecha)
                        await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre, "texto": aviso, "fecha": fecha})
                    sala.guardar_chat(nombre_usuario, texto, fecha)
                    await enviar_sala(sala, {
                        "tipo": "chat", "proyecto": sala.nombre,
                        "de": nombre_usuario, "texto": texto, "fecha": fecha,
                    })

            elif tipo == "escribiendo":
                sala = sala_de(msg.get("proyecto", "general"))
                await enviar_sala(sala, {
                    "tipo": "escribiendo", "proyecto": sala.nombre,
                    "de": nombre_usuario,
                }, excepto=ws)

            elif tipo == "file_lock":
                # Notificación de edición concurrente
                ruta_lock = msg.get("ruta", "")
                accion_lock = msg.get("accion", "lock")
                if accion_lock == "lock":
                    FILE_LOCKS[ruta_lock] = {"usuario": nombre_usuario, "ts": time.time()}
                else:
                    FILE_LOCKS.pop(ruta_lock, None)
                await enviar_todos({"tipo": "file_locks", "locks": FILE_LOCKS})

            elif tipo == "run":
                sala = sala_de(msg.get("proyecto", "general"))
                rid = f"{msg.get('id') or 'run'}-{secrets.token_hex(3)}"
                cmd = msg.get("cmd", "")
                if cmd.strip():
                    sala.guardar_sistema(f"{nombre_usuario} ejecutó: {sanitizar(cmd)}", fecha)
                    asyncio.create_task(lanzar(sala, rid, cmd))
                    await enviar(ws, {"tipo": "run_id", "proyecto": sala.nombre, "id": rid})

            elif tipo == "stop":
                rid = msg.get("id", "")
                proc = None
                target_sala = None
                for s in SALAS.values():
                    if rid in s.procesos:
                        target_sala = s
                        proc = s.procesos[rid]
                        break
                if proc:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    if target_sala:
                        await enviar_sala(target_sala, {
                            "tipo": "sistema", "proyecto": target_sala.nombre,
                            "texto": f"🛑 Proceso {rid} detenido por {nombre_usuario}", "fecha": fecha,
                        })

            elif tipo == "archivo_leer":
                sala = sala_de(msg.get("proyecto", "general"))
                try:
                    p = ruta_segura(sala, msg.get("ruta", ""))
                    datos = p.read_bytes()
                    if b"\x00" in datos[:8192]:
                        raise ValueError("archivo binario: usa Descargar")
                    texto = datos.decode("utf-8", errors="replace")
                    truncado = len(datos) > MAX_LECTURA
                    if truncado:
                        texto = texto[:MAX_LECTURA] + "\n… [truncado]"
                    await enviar(ws, {
                        "tipo": "archivo", "proyecto": sala.nombre,
                        "ruta": msg.get("ruta"), "contenido": texto,
                        "truncado": truncado,
                    })
                except Exception as e:
                    await enviar(ws, {"tipo": "error", "texto": str(e)})

            elif tipo == "archivo_escribir":
                sala = sala_de(msg.get("proyecto", "general"))
                try:
                    ruta = msg.get("ruta", "")
                    if not ruta:
                        raise ValueError("ruta vacía")
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
                    if msg.get("confirmar") is not True:
                        raise ValueError("confirmación requerida")
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

            elif tipo == "archivo_renombrar":
                sala = sala_de(msg.get("proyecto", "general"))
                try:
                    viejo = msg.get("ruta", "")
                    nuevo = msg.get("nuevo", "")
                    p_viejo = ruta_segura(sala, viejo)
                    p_nuevo = ruta_segura(sala, nuevo)
                    if p_nuevo.exists():
                        raise ValueError("Ya existe un archivo con ese nombre")
                    p_nuevo.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p_viejo), str(p_nuevo))
                    sala.guardar_sistema(f"{nombre_usuario} renombró {viejo} → {nuevo}", fecha)
                    await enviar_sala(sala, {
                        "tipo": "sistema", "proyecto": sala.nombre,
                        "texto": f"✏️ {nombre_usuario} renombró {viejo} → {nuevo}", "fecha": fecha,
                    })
                    await enviar(ws, {"tipo": "operacion_ok", "accion": "renombrar", "ruta": nuevo})
                    await difundir_arbol()
                except Exception as e:
                    await enviar(ws, {"tipo": "error", "texto": str(e)})

            elif tipo == "archivo_subir":
                sala = sala_de(msg.get("proyecto", "general"))
                try:
                    ruta = msg.get("ruta", "")
                    datos = base64.b64decode(msg.get("datos", ""))
                    if len(datos) > MAX_SUBIDA:
                        raise ValueError(f"Archivo supera {MAX_SUBIDA // (1024*1024)} MB")
                    p = ruta_segura(sala, ruta)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(datos)
                    sala.guardar_sistema(f"{nombre_usuario} subió {ruta} ({len(datos)} bytes)", fecha)
                    await enviar_sala(sala, {
                        "tipo": "sistema", "proyecto": sala.nombre,
                        "texto": f"⬆️ {nombre_usuario} subió {ruta} ({len(datos)} bytes)", "fecha": fecha,
                    })
                    await enviar(ws, {"tipo": "operacion_ok", "accion": "subir", "ruta": ruta})
                    await difundir_arbol()
                except Exception as e:
                    await enviar(ws, {"tipo": "error", "texto": str(e)})

            elif tipo == "git":
                sala = sala_de(msg.get("proyecto", "general"))
                accion = msg.get("accion", "status")
                rid = f"git-{secrets.token_hex(3)}"
                asyncio.create_task(lanzar(sala, rid, cmd_git(accion, msg.get("msg"), nombre_usuario), tipo="git"))

            elif tipo == "arbol":
                await enviar(ws, {"tipo": "arbol", "arbol": _arbol()})

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        CONECTADOS.discard(ws)
        for s in mis_salas:
            s.conectados.discard(ws)
        # Limpiar locks del usuario desconectado
        locks_a_quitar = [k for k, v in FILE_LOCKS.items() if v.get("usuario") == nombre_usuario]
        for k in locks_a_quitar:
            FILE_LOCKS.pop(k, None)
        await enviar_todos({
            "tipo": "presencia",
            "conectados": len(CONECTADOS),
            "file_locks": FILE_LOCKS,
        })


# ---------------------------------------------------------------- Arranque
if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 64)
    print(f"🚀 NEXO 2.0 Atlantis — Servidor de Colaboración IA & Humana")
    print("=" * 64)
    print(f"• URL Local:        http://127.0.0.1:{PORT}")
    print(f"• Red LAN:          http://0.0.0.0:{PORT}")
    print(f"• Workspace Raíz:   {WORKSPACE}")
    print(f"• Proyectos:        {PROYECTOS}")
    print(f"• Token de Acceso:  {TOKEN}")
    print(f"• Modo Sandbox:     {'bwrap activo (Linux)' if _BWRAP_OK else 'Seguridad de entorno nativa'}")
    print("=" * 64 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
