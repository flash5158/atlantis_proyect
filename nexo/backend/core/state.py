"""Estado en memoria del hub (runtime, no persistente). Build v0.6.1.

Todo lo que aquí vive es efímero por diseño: conexiones websocket,
presencia, salas y cachés de ejecución. La persistencia real está en
``backend.store`` (SQLite) y ``backend.db``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from fastapi import WebSocket

from backend.core.config import config
from backend.store import get_store


def _podar_bus_equipo(equipo_id: str) -> None:
    """Poda de retención del bus (últimos 200 por equipo). Best-effort:
    si falla, el post ya quedó persistido; no se finge éxito ni se
    rompe la publicación."""
    try:
        from backend import bus_entrega
        bus_entrega.podar(equipo_id)
    except Exception as e:
        print(f"[Bus] Aviso: no se pudo podar retención del equipo: {e}")


def _AI_STORE():
    return get_store()


# ---------------------------------------------------------------- Conexiones
CONECTADOS: set[WebSocket] = set()
SALAS: dict[str, Sala] = {}


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
    if nombre_limpio == "__raiz__":
        # Alcance especial: archivos de la raíz del workspace (no es una sala colaborativa).
        if "__raiz__" not in SALAS or SALAS["__raiz__"].directorio != config.WORKSPACE:
            # Sin caché o workspace cambiado: recrear (misma razón que abajo).
            SALAS["__raiz__"] = Sala("__raiz__", config.WORKSPACE)
        return SALAS["__raiz__"]
    if nombre_limpio not in SALAS:
        directorio = config.PROYECTOS / nombre_limpio
        directorio.mkdir(parents=True, exist_ok=True)
        if not (directorio / "README.md").exists():
            (directorio / "README.md").write_text(
                f"# {nombre_limpio}\n\nProyecto colaborativo en NEXO.\n",
                encoding="utf-8",
            )
        SALAS[nombre_limpio] = Sala(nombre_limpio, directorio)
    else:
        # El workspace puede cambiar en caliente (set_active_workspace,
        # o create_app() que re-lee el entorno): una Sala cacheada con
        # el directorio viejo escribiría en el workspace equivocado.
        # Si el directorio ya no coincide con el workspace actual,
        # invalidar la caché y recrear.
        esperado = config.PROYECTOS / nombre_limpio
        if SALAS[nombre_limpio].directorio != esperado:
            del SALAS[nombre_limpio]
            return sala_de(nombre)
    return SALAS[nombre_limpio]


def ruta_segura(sala: Sala, ruta: str) -> Path:
    base = sala.directorio.resolve()
    p = (base / ruta).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"Ruta fuera del proyecto: {ruta}")
    # Nunca exponer tripas del sistema por la API de archivos, ni al dueño:
    # un .git/hooks/ escribible permitiría plantar un pre-commit malicioso
    # que se ejecutaría en el próximo git commit, y un .env guarda secretos.
    # El dueño edita .env en su propio editor fuera de Atlantis.
    partes_rel = p.relative_to(base).parts
    if any(part in (".git", ".env") for part in partes_rel):
        raise ValueError(f"Ruta protegida del sistema: {ruta}")
    return p


def resolver_archivo(proyecto: str, ruta: str) -> Path:
    sala = sala_de(proyecto)
    return ruta_segura(sala, ruta)


def _arbol() -> list[dict]:
    arbol = []
    # 1. Proyectos si existen
    if config.PROYECTOS.exists():
        ws_name = config.WORKSPACE.name
        for p in sorted(config.PROYECTOS.iterdir()):
            if not p.is_dir() or p.name.startswith("."):
                continue
            # Evitar duplicados visuales: un proyecto con el mismo nombre que la
            # raíz del workspace se vería idéntico en el explorador.
            if p.name == ws_name:
                continue
            archivos = []
            for f in sorted(p.rglob("*")):
                if f.is_file() and not any(part.startswith(".") for part in f.parts) and f.name != ".nexo-chat.md":
                    archivos.append(str(f.relative_to(p)))
            arbol.append({"nombre": p.name, "archivos": archivos})

    # 2. Archivos del workspace principal si no están en proyectos
    if config.WORKSPACE.exists():
        archivos_raiz = []
        ignorar = {".git", ".venv", "node_modules", "__pycache__", "dist", ".ruff_cache", ".vercel"}
        for f in sorted(config.WORKSPACE.iterdir()):
            if f.name.startswith(".") or f.name in ignorar:
                continue
            if f.is_file():
                archivos_raiz.append(f.name)
            elif f.is_dir() and f.name != "proyectos":
                for subf in sorted(f.rglob("*")):
                    if subf.is_file() and not any(part.startswith(".") or part in ignorar for part in subf.parts):
                        archivos_raiz.append(str(subf.relative_to(config.WORKSPACE)))
        if archivos_raiz:
            arbol.insert(0, {"nombre": config.WORKSPACE.name or "workspace", "archivos": archivos_raiz, "es_raiz": True})

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


# ---------------------------------------------------------------- Presencia
PRESENCIA: dict = {}  # ws -> {"nombre", "file", "cursor", "selection", "color"}
PALETA_PRESENCIA = ["#4da3ff", "#3ecf8e", "#c792ea", "#ffb86b", "#ff6b81", "#5eead4"]


def _color_para(nombre: str) -> str:
    return PALETA_PRESENCIA[abs(hash(nombre)) % len(PALETA_PRESENCIA)]


async def _difundir_presencia() -> None:
    usuarios = [
        {"nombre": v.get("nombre", "?"), "file": v.get("file", ""),
         "cursor": v.get("cursor") or {},
         "selection": v.get("selection") or {},
         "color": v.get("color", "#4da3ff")}
        for v in PRESENCIA.values()
    ]
    await enviar_todos({"tipo": "presence_update", "users": usuarios})


# ---------------------------------------------------------------- Agentes (estado en memoria)
HERMES_STATE = {
    "my_agent": {
        "name": "Hermes-Daniel",
        "role": "Creador Autónomo",
        "engine": "local",
        "adapter": "hermes",
        "provider": "hermes",
        "model": "auto",
        "endpoint": "",
        "status": "disconnected",
        "model_agnostic": True,
        "capabilities": ["terminal_pty", "bash", "filesystem", "git", "antigravity_skills", "dual_pairing"],
    },
}

FILE_LOCKS: dict[str, dict] = {}


# ---------------------------------------------------------------- Versiones de archivo (persistente vía HubStore)
def _version_archivo(proyecto: str, ruta: str) -> int:
    return int(_AI_STORE().get("file_versions", {}).get(f"{proyecto}::{ruta}", 0))


def _bump_version_archivo(proyecto: str, ruta: str, autor: str = "") -> int:
    versiones = _AI_STORE().setdefault("file_versions", {})
    clave = f"{proyecto}::{ruta}"
    versiones[clave] = int(versiones.get(clave, 0)) + 1
    if autor:
        _AI_STORE().setdefault("file_version_authors", {})[clave] = autor
    return versiones[clave]


def _autor_version_archivo(proyecto: str, ruta: str) -> str:
    return str(_AI_STORE().get("file_version_authors", {}).get(f"{proyecto}::{ruta}", ""))


def _fijar_version_archivo(proyecto: str, ruta: str, version: int, autor: str = "") -> int:
    """Fija la versión de un archivo (sync entre servidores).

    A diferencia de ``_bump_version_archivo`` (que suma 1), aquí la versión
    viene impuesta por el peer que originó el cambio. Nunca baja de 0.
    """
    versiones = _AI_STORE().setdefault("file_versions", {})
    clave = f"{proyecto}::{ruta}"
    versiones[clave] = max(0, int(version))
    if autor:
        _AI_STORE().setdefault("file_version_authors", {})[clave] = autor
    return versiones[clave]


def _eliminar_version_archivo(proyecto: str, ruta: str) -> None:
    """Borra la entrada de versión de un archivo (sync de delete/rename)."""
    clave = f"{proyecto}::{ruta}"
    _AI_STORE().setdefault("file_versions", {}).pop(clave, None)
    _AI_STORE().setdefault("file_version_authors", {}).pop(clave, None)


def _mover_version_archivo(proyecto: str, origen: str, destino: str) -> int:
    """Mueve la versión+autor de ``origen`` a ``destino`` (sync de rename)."""
    clave_origen = f"{proyecto}::{origen}"
    clave_destino = f"{proyecto}::{destino}"
    versiones = _AI_STORE().setdefault("file_versions", {})
    autores = _AI_STORE().setdefault("file_version_authors", {})
    version = int(versiones.pop(clave_origen, 0))
    autor = autores.pop(clave_origen, "")
    versiones[clave_destino] = version
    if autor:
        autores[clave_destino] = autor
    return version


# ---------------------------------------------------------------- Bus de agentes (persistente vía HubStore)
_BUS_WAKE_EVENT: asyncio.Event | None = None


def get_bus_wake_event() -> asyncio.Event:
    global _BUS_WAKE_EVENT
    if _BUS_WAKE_EVENT is None:
        _BUS_WAKE_EVENT = asyncio.Event()
    return _BUS_WAKE_EVENT


def notificar_bus_event() -> None:
    """Despierta de inmediato a cualquier worker o espera activa en el bus."""
    global _BUS_WAKE_EVENT
    if _BUS_WAKE_EVENT is not None:
        try:
            _BUS_WAKE_EVENT.set()
        except RuntimeError:
            pass


def _bus_post(de: str, para: str, texto: str, tipo: str = "mensaje",
              task_ref: str | None = None, proyecto: str = "general",
              respuesta_a: str | None = None, equipo_id: str | None = None,
              agente: dict | None = None, extra: dict | None = None) -> dict:
    """Publica un mensaje en el bus de agentes y lo persiste. No difunde (ver _bus_difundir).

    ``equipo_id`` da alcance al equipo del proyecto (los agentes del
    proyecto X no ven ni actúan en el proyecto Y). ``agente`` es la
    identidad de quien habla (id, nombre, color, dueño) para que
    siempre se sepa QUÉ agente habla. ``extra`` lleva campos de la
    tarea (tarea_id, fingerprint, notas, carpeta)."""
    mid = f"bus-{int(time.time())}-{secrets.token_hex(3)}"
    msg = {
        "id": mid,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "de": de,
        "para": para,
        "proyecto": proyecto,
        "tipo": tipo,
        "texto": texto,
        "task_ref": task_ref,
        "respuesta_a": respuesta_a,
        "estado": "pendiente",
        "resultado": "",
        "equipo_id": equipo_id or "",
        "agente": agente or {},
    }
    if extra:
        msg.update({k: v for k, v in extra.items() if k not in msg})
    guardado = _AI_STORE().setdefault("agent_bus", []).append(msg)
    # Retención: últimos 200 mensajes por equipo (best-effort, no
    # bloquea ni finge el resultado de la publicación).
    _podar_bus_equipo(equipo_id or "")
    notificar_bus_event()
    return guardado if guardado is not None else msg


async def _bus_difundir(msg: dict) -> None:
    notificar_bus_event()
    await enviar_todos({"tipo": "agent_msg", "mensaje": msg})


def _bus_inbox(agente: str, solo_pendientes: bool = False,
               equipo_id: str | None = None) -> list[dict]:
    """Bandeja del bus para un agente. Con ``equipo_id``, solo los
    mensajes de ESE equipo (un agente de X no recoge trabajo de Y)."""
    nombre = (agente or "").lower()
    out = []
    for m in _AI_STORE().get("agent_bus", []):
        para = str(m.get("para", "")).lower()
        if para and para not in (nombre, "todos"):
            continue
        if equipo_id is not None and m.get("equipo_id") not in ("", equipo_id):
            continue
        if solo_pendientes and m.get("estado") == "completado":
            continue
        out.append(m)
    return out


def _bus_buscar_respuesta(respuesta_a: str) -> dict | None:
    for m in _AI_STORE().get("agent_bus", []):
        if (m.get("respuesta_a") == respuesta_a or m.get("id") == respuesta_a) and m.get("estado") in ("completado", "error"):
            return m
    return None


async def _bus_esperar_respuesta(respuesta_a: str, timeout: float = 180.0) -> dict | None:
    """Espera reactivamente la respuesta de un agente en el bus. Retorna None si expira."""
    ev = get_bus_wake_event()
    fin = time.time() + timeout
    while time.time() < fin:
        r = _bus_buscar_respuesta(respuesta_a)
        if r:
            return r
        restante = max(0.05, min(1.0, fin - time.time()))
        try:
            await asyncio.wait_for(ev.wait(), timeout=restante)
        except TimeoutError:
            pass
        finally:
            ev.clear()
    return None


def entorno_limpio(proyecto_path: Path) -> dict:
    path_dirs = [
        str(config.NEXO_DIR / ".venv" / "bin"),
        str(config.HOME / ".local" / "bin"),
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
        "HOME": str(config.HOME),
        "WORKSPACE": str(config.WORKSPACE),
        "PROYECTO": str(proyecto_path),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "PYTHONUNBUFFERED": "1",
    })
    return env
