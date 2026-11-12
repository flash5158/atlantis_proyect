"""Sincronización de archivos entre servidores NEXO (co-edición en vivo).
Build v0.6.0 (8 mejoras: join solo con código, conflictos, cursores,
códigos con expiración, estado de sync, retiro del modo equipo viejo,
preview local, aprobación dual).

Problema que resuelve: cada laptop corre su propio servidor NEXO con SU
copia de cada proyecto. Hasta ahora, el broadcast ``file_changed`` solo
llegaba a los clientes de UN servidor. Este módulo conecta servidores
entre sí para que todos converjan a la misma versión de cada archivo.

Modelo (por diseño, no por accidente):
- **Proyecto = una CARPETA dentro del workspace** (el modelo del
  asistente "Nuevo proyecto": ``<nombre>/...``), NO el workspace entero
  ni el namespace ``proyecto``. Una ruta ``X/a/b.txt`` pertenece al
  proyecto ``X``; las rutas de la raíz (sin carpeta) no pertenecen a
  ningún proyecto y NUNCA se sincronizan (proyectos en solitario).
- **Cada proyecto tiene SU propio equipo**: su código de invitación y
  sus miembros. Compartir X con A le da a A acceso SOLO a ``X/``.
  A nunca ve Y, B nunca ve X.
- **Equipo abierto**: el dueño puede generar códigos nuevos para el
  MISMO proyecto en cualquier momento; los nuevos miembros se unen al
  equipo existente (con catch-up de los archivos de ese proyecto).
- **Varias sesiones a la vez**: un servidor puede estar en X con A y
  en Y con B al mismo tiempo (un PeerClient por proyecto).
- Cada servidor guarda SU copia en SU disco. No hay copia central.
- Un servidor hace de *relay* (puede ser el hub central o una de las
  laptops): reenvía mensajes entre peers de un mismo equipo.
  **El relay NUNCA persiste contenido de archivos** (ni en BD, ni en
  disco, ni en logs). El registro de equipos en el relay es solo
  metadato (códigos, miembros, tokens de invitado, aprobaciones).
- Protocolo sobre el canal /ws existente: ``file_sync`` (cambios),
  ``sync_request`` / ``sync_state`` / ``sync_fetch`` (catch-up),
  ``sync_equipo_update`` (miembros del equipo), ``agent_msg`` (bus de
  agentes del equipo: los agentes de un proyecto trabajan en conjunto
  sin cruzar a otros proyectos), ``file_big`` (transferencia COMPLETA de
  un archivo grande/binario en UN solo mensaje: bytes en base64 + sha256
  del archivo completo; el receptor verifica el hash ANTES de escribir).
- Códigos de invitación: expiran (por defecto 24 h, el anfitrión puede
  poner otra duración) y se pueden revocar uno a uno sin cerrar el
  equipo. Equipos existentes siguen funcionando; solo los nuevos
  ingresos se ven afectados.
- Aprobación dual: el anfitrión y un compañero aprueban la MISMA
  versión del proyecto (huella del árbol); solo entonces se publica la
  tarea de terminar el desarrollo en el bus del equipo.
- Cada agente tiene identidad (id estable, nombre renombrable solo por
  su dueño, color) y siempre se sabe qué agente habla.
- Conflictos: si dos peers editan el mismo archivo desde la misma base,
  NINGUNO pierde su trabajo: el contenido local se mantiene y el entrante
  se guarda como backup ``<ruta>.conflict.<autor>.<timestamp>``.

Seguridad:
- Solo peers autenticados envían/reciben sync: token de dueño, o
  guest_token emitido para ESE equipo. Un guest_token pertenece a UN
  solo equipo; usarlo en otro equipo se rechaza (4403).
- El relay valida el prefijo de carpeta en CADA mensaje (y el receptor
  lo vuelve a validar al aplicar): nunca se confía en que el cliente
  se quede en su carpeta.
- Todas las rutas pasan por ``ruta_segura()`` (sin .git/.env, sin escapes).
- Contenido por mensaje capado a 1 MB (se rechaza, no se trunca).
- Archivos binarios / grandes SÍ se sincronizan por contenido mediante
  ``file_big`` (un solo mensaje con sha256 verificado antes de escribir),
  hasta el cap MAX_FILE_BIG. Sobre el cap se reportan como no soportados,
  nunca se corrompen ni se envían a medias.
- Jamás se registra contenido de archivos en logs.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.parse
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import WebSocket

from backend.core.config import config
from backend.core.state import (
    _autor_version_archivo,
    _bus_difundir,
    _bus_post,
    _bump_version_archivo,
    _eliminar_version_archivo,
    _fijar_version_archivo,
    _mover_version_archivo,
    _version_archivo,
    difundir_arbol,
    enviar_todos,
    ruta_segura,
    sala_de,
)
SYNC_VERSION = 1
# Contenido máximo por mensaje de sync (bytes UTF-8). Se rechaza, no se trunca.
MAX_SYNC_CONTENIDO = 1_000_000
# Archivos mayores que esto no se hashean ni se sincronizan por contenido.
MAX_SYNC_ARBOL_BYTES = 1_000_000
# Tamaño máximo del contenido inline en file_changed local (igual que files.py).
MAX_INLINE_CONTENIDO = 65536
# Tamaño máximo por archivo en transferencia completa file_big (bytes).
# Por qué 10 MB: el relay es gratis (Render free o una laptop) y cada
# archivo viaja como UN solo mensaje de WebSocket: 10 MB -> ~13.4 MB en
# base64+JSON, que cabe en el límite de 16 MB del servidor (uvicorn por
# defecto) y en el de 32 MB del PeerClient. Más grande saturaría el relay
# y arriesgaría cortes a mitad de mensaje. Cubre sprites, audio y assets
# típicos de juegos. Sobre el cap: no se envía nada a medias, se reporta
# como no soportado igual que antes (sync_unsupported "demasiado_grande").
MAX_FILE_BIG = 10 * 1024 * 1024

_OPERACIONES = ("upsert", "delete", "rename")
_TIPOS_SYNC = ("file_sync", "sync_request", "sync_state", "sync_fetch",
               "peer_join", "sync_equipo_update", "agent_msg", "file_big")

# Expiración por defecto de los códigos de invitación (minutos): 24 h.
CODIGO_EXPIRA_DEFECTO_MIN = 1440

# Paleta de colores de agentes (identidad visual estable por agent_id).
_PALETA_AGENTES = ["#4da3ff", "#3ecf8e", "#c792ea", "#ffb86b", "#ff6b81", "#5eead4"]


def _color_agente(agent_id: str) -> str:
    """Color estable de un agente a partir de su id (no depende del
    hash() del proceso, que cambia en cada arranque)."""
    h = int(hashlib.sha256(str(agent_id or "").encode("utf-8")).hexdigest()[:8], 16)
    return _PALETA_AGENTES[h % len(_PALETA_AGENTES)]


def _nuevo_agent_id() -> str:
    return "agent-" + secrets.token_hex(6)


def identidad_agente(miembro: dict) -> dict:
    """Identidad pública de un agente: id estable, nombre (renombrable),
    color y dueño. Así siempre se sabe QUÉ agente habla."""
    agent_id = str(miembro.get("agent_id") or "")
    return {"id": agent_id,
            "nombre": miembro.get("agent_name", ""),
            "color": miembro.get("agent_color") or _color_agente(agent_id),
            "dueno": miembro.get("user_name", "")}


def _normalizar_codigo(c) -> dict:
    """Un código puede ser str legado (sin expiración, nunca expira)
    o dict {code, expira_en, revocado}."""
    if isinstance(c, str):
        return {"code": c.strip().upper(), "expira_en": None, "revocado": False}
    if isinstance(c, dict) and c.get("code"):
        return {"code": str(c["code"]).strip().upper(),
                "expira_en": c.get("expira_en"),
                "revocado": bool(c.get("revocado"))}
    return {"code": "", "expira_en": None, "revocado": True}


def _estado_codigo(c: dict) -> str:
    """vigente | expirado | revocado."""
    if c.get("revocado"):
        return "revocado"
    exp = c.get("expira_en")
    if exp:
        try:
            if datetime.now() > datetime.fromisoformat(exp):
                return "expirado"
        except Exception:
            pass
    return "vigente"


def _buscar_codigo(codigos, codigo_norm: str) -> dict | None:
    """Objeto del código, o None si no existe en la lista."""
    for c in codigos or []:
        norm = _normalizar_codigo(c)
        if norm["code"] == codigo_norm:
            return norm
    return None

# Dedupe de mensajes (evita doble aplicación cuando un peer recibe el
# mismo cambio por dos rutas: p.ej. relay local + relay central).
_SYNC_IDS_VISTOS: deque = deque(maxlen=2000)
_SYNC_IDS_SET: set = set()
_FETCH_IDS_VISTOS: set = set()


def _recordar_sync_id(sync_id: str) -> bool:
    """True si es nuevo (y lo registra); False si ya se vio."""
    if not sync_id or sync_id in _SYNC_IDS_SET:
        return False
    if len(_SYNC_IDS_VISTOS) >= 2000:
        viejo = _SYNC_IDS_VISTOS.popleft()
        _SYNC_IDS_SET.discard(viejo)
    _SYNC_IDS_VISTOS.append(sync_id)
    _SYNC_IDS_SET.add(sync_id)
    return True


def _recordar_fetch_id(fetch_id: str) -> bool:
    """True si es nuevo (y lo registra); False si ya se respondió/aplicó."""
    if not fetch_id or fetch_id in _FETCH_IDS_VISTOS:
        return False
    if len(_FETCH_IDS_VISTOS) > 5000:
        _FETCH_IDS_VISTOS.clear()
    _FETCH_IDS_VISTOS.add(fetch_id)
    return True


def _hash_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _hash_contenido(contenido: str) -> str:
    return _hash_bytes(contenido.encode("utf-8"))


def sanitizar_autor(autor: str) -> str:
    limpio = re.sub(r"[^A-Za-z0-9._-]", "_", str(autor or "peer").strip())
    return limpio[:32] or "peer"


def nombre_conflicto(ruta: str, autor: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"{ruta}.conflict.{sanitizar_autor(autor)}.{ts}"


def es_archivo_conflicto(ruta: str) -> bool:
    return ".conflict." in Path(ruta or "").name


# ------------------------------------------------------------------ carpetas de proyecto
def carpeta_segura(carpeta) -> bool:
    """¿Es un nombre de carpeta de proyecto válido? Un solo componente,
    sin puntos iniciales, sin separadores, sin .git/.env."""
    if not isinstance(carpeta, str) or not carpeta:
        return False
    if carpeta in (".", ".."):
        return False
    if carpeta.startswith(".") or "/" in carpeta or "\\" in carpeta:
        return False
    if carpeta in (".git", ".env"):
        return False
    return Path(carpeta).name == carpeta


def carpeta_de_ruta(ruta) -> str:
    """Carpeta de proyecto de una ruta relativa a la sala: su primer
    componente. "" si la ruta vive en la raíz (sin proyecto)."""
    if not isinstance(ruta, str) or not ruta:
        return ""
    partes = [p for p in ruta.replace("\\", "/").split("/") if p not in ("", ".")]
    if len(partes) < 2:
        return ""
    return partes[0]


def ruta_en_carpeta(ruta, carpeta: str) -> bool:
    """¿La ruta vive estrictamente dentro de la carpeta? ``X/a.txt`` sí;
    ``X`` (la carpeta misma), ``XY/a.txt`` y ``Y/a.txt`` no."""
    if not carpeta_segura(carpeta):
        return False
    if not isinstance(ruta, str) or not ruta:
        return False
    normalizada = ruta.replace("\\", "/")
    if normalizada.startswith("/"):
        return False
    partes = [p for p in normalizada.split("/") if p not in ("", ".")]
    if len(partes) < 2:
        return False
    return partes[0] == carpeta


def _proyecto_seguro(proyecto) -> bool:
    if not isinstance(proyecto, str) or not proyecto:
        return False
    if proyecto.startswith(".") or "/" in proyecto or "\\" in proyecto:
        return False
    return Path(proyecto).name == proyecto


def _ruta_rel_segura(proyecto: str, ruta) -> bool:
    """Validación de ruta sin crear directorios (para el relay).

    Replica las reglas de ``ruta_segura`` sin tocar disco: sin escapes,
    sin .git/.env. El receptor vuelve a validar con ``ruta_segura``.
    """
    if not isinstance(ruta, str) or not ruta or ruta.startswith("/"):
        return False
    try:
        if proyecto == "__raiz__":
            base = config.WORKSPACE.resolve()
        else:
            base = (config.PROYECTOS / proyecto).resolve()
        p = (base / ruta).resolve()
    except Exception:
        return False
    if p != base and base not in p.parents:
        return False
    try:
        partes = p.relative_to(base).parts
    except ValueError:
        return False
    if any(part in (".git", ".env") for part in partes):
        return False
    return True


def _es_entero_no_negativo(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _equipo_id_valido(equipo_id) -> bool:
    return isinstance(equipo_id, str) and bool(equipo_id) and len(equipo_id) <= 80 \
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", equipo_id) is not None


def validar_mensaje_sync(msg) -> tuple[bool, str]:
    """Validación estructural de un mensaje de sync (relay y receptor).

    Además de lo estructural, exige que TODA ruta vaya dentro de la
    carpeta del equipo (``carpeta``): el relay jamás reenvía una ruta
    fuera de la carpeta, y el receptor la vuelve a validar al aplicar.
    No registra contenido. Retorna (ok, motivo).
    """
    if not isinstance(msg, dict):
        return False, "no es un objeto"
    tipo = msg.get("tipo")
    if tipo not in _TIPOS_SYNC:
        return False, f"tipo desconocido: {tipo}"
    if not _proyecto_seguro(msg.get("proyecto")):
        return False, "proyecto inválido"

    # Todos los mensajes de equipo llevan carpeta + equipo_id.
    if tipo != "sync_equipo_update":
        if not carpeta_segura(msg.get("carpeta")):
            return False, "carpeta inválida"
        if not _equipo_id_valido(msg.get("equipo_id")):
            return False, "equipo_id inválido"
    else:
        if not _equipo_id_valido(msg.get("equipo_id")):
            return False, "equipo_id inválido"
        if not isinstance(msg.get("miembros"), list):
            return False, "miembros inválidos"
        return True, ""

    if tipo == "file_sync":
        if msg.get("operacion") not in _OPERACIONES:
            return False, "operación inválida"
        if not _ruta_rel_segura(msg.get("proyecto", ""), msg.get("ruta", "")):
            return False, "ruta inválida"
        if not ruta_en_carpeta(msg.get("ruta", ""), msg.get("carpeta", "")):
            return False, "ruta fuera de la carpeta del proyecto"
        if msg.get("operacion") == "rename":
            if not msg.get("origen"):
                return False, "rename sin origen"
            if not ruta_en_carpeta(msg.get("origen", ""), msg.get("carpeta", "")):
                return False, "origen fuera de la carpeta del proyecto"
        if not _es_entero_no_negativo(msg.get("base_version")):
            return False, "base_version inválida"
        if not _es_entero_no_negativo(msg.get("version")):
            return False, "version inválida"
        contenido = msg.get("contenido")
        if contenido is not None:
            if not isinstance(contenido, str):
                return False, "contenido no es texto"
            if len(contenido.encode("utf-8", errors="replace")) > MAX_SYNC_CONTENIDO:
                return False, "contenido excede 1 MB"
        return True, ""

    if tipo == "sync_fetch":
        if not _ruta_rel_segura(msg.get("proyecto", ""), msg.get("ruta", "")):
            return False, "ruta inválida"
        if not ruta_en_carpeta(msg.get("ruta", ""), msg.get("carpeta", "")):
            return False, "ruta fuera de la carpeta del proyecto"
        if not _es_entero_no_negativo(msg.get("version")):
            return False, "version inválida"
        if not msg.get("fetch_id"):
            return False, "fetch sin fetch_id"
        return True, ""

    if tipo == "file_big":
        # Transferencia completa de un archivo grande/binario en UN solo
        # mensaje: bytes en base64 + sha256 del archivo completo.
        if not _ruta_rel_segura(msg.get("proyecto", ""), msg.get("ruta", "")):
            return False, "ruta inválida"
        if not ruta_en_carpeta(msg.get("ruta", ""), msg.get("carpeta", "")):
            return False, "ruta fuera de la carpeta del proyecto"
        if msg.get("operacion") != "upsert":
            return False, "operación inválida (file_big solo admite upsert)"
        if not _es_entero_no_negativo(msg.get("base_version")):
            return False, "base_version inválida"
        if not _es_entero_no_negativo(msg.get("version")):
            return False, "version inválida"
        total = msg.get("total_bytes")
        if not _es_entero_no_negativo(total) or int(total) <= 0:
            return False, "total_bytes inválido"
        if int(total) > MAX_FILE_BIG:
            return False, "archivo excede el cap"
        datos_b64 = msg.get("datos_b64")
        if not isinstance(datos_b64, str) or not datos_b64:
            return False, "datos inválidos"
        # Cota del base64 declarado: 4*ceil(n/3) + margen. Evita que un
        # mensaje gigante disfrazado pase la validación estructural.
        if len(datos_b64) > 4 * ((int(total) + 2) // 3) + 64:
            return False, "datos exceden lo declarado"
        sha = msg.get("file_sha256")
        if (not isinstance(sha, str) or len(sha) != 64
                or any(c not in "0123456789abcdefABCDEF" for c in sha)):
            return False, "sha256 inválido"
        return True, ""

    if tipo in ("sync_request", "sync_state"):
        arbol = msg.get("arbol")
        if not isinstance(arbol, dict):
            return False, "árbol inválido"
        for ruta, entrada in arbol.items():
            if not _ruta_rel_segura(msg.get("proyecto", ""), ruta):
                return False, f"ruta inválida en árbol: {ruta}"
            if not ruta_en_carpeta(ruta, msg.get("carpeta", "")):
                return False, f"ruta fuera de la carpeta en árbol: {ruta}"
            if not isinstance(entrada, dict) or not _es_entero_no_negativo(entrada.get("version")):
                return False, f"entrada inválida en árbol: {ruta}"
        return True, ""

    if tipo == "peer_join":
        if not msg.get("peer_id") or not isinstance(msg.get("peer_id"), str):
            return False, "peer_join sin peer_id"
        return True, ""

    if tipo == "agent_msg":
        # Mensaje del bus de agentes del equipo: viaja entre las laptops
        # del MISMO equipo para que los dos agentes trabajen en conjunto.
        if not msg.get("sync_id"):
            return False, "agent_msg sin sync_id"
        mensaje = msg.get("mensaje")
        if not isinstance(mensaje, dict):
            return False, "agent_msg sin mensaje"
        if not mensaje.get("id") or not isinstance(mensaje.get("de"), str):
            return False, "mensaje de agente inválido"
        if not isinstance(mensaje.get("texto"), str):
            return False, "mensaje de agente sin texto"
        agente = msg.get("agente")
        if agente is not None and not isinstance(agente, dict):
            return False, "agente inválido"
        return True, ""

    return True, ""


def construir_arbol_sync(proyecto: str, carpeta: str) -> dict:
    """Árbol {ruta: {version, hash, binario, grande}} SOLO de la carpeta
    del proyecto (rutas relativas a la sala, p.ej. ``X/a.txt``).

    Excluye: ocultos, .git/.env, .nexo-chat.md, backups *.conflict.*,
    __pycache__/node_modules/.venv/dist. Archivos >1 MB: solo versión,
    sin hash (no se sincronizan por contenido).
    """
    if not carpeta_segura(carpeta):
        return {}
    sala = sala_de(proyecto)
    directorio = sala.directorio / carpeta
    ignorar_dirs = {"__pycache__", "node_modules", ".venv", "dist", ".git"}
    arbol: dict[str, dict] = {}
    if not directorio.is_dir():
        return arbol
    for f in sorted(directorio.rglob("*")):
        if not f.is_file():
            continue
        try:
            rel = str(f.relative_to(sala.directorio))
        except ValueError:
            continue
        partes = Path(rel).parts
        if any(p.startswith(".") for p in partes):
            continue
        if any(p in ignorar_dirs for p in partes):
            continue
        if rel == ".nexo-chat.md" or es_archivo_conflicto(rel):
            continue
        version = _version_archivo(proyecto, rel)
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size > MAX_SYNC_ARBOL_BYTES:
            arbol[rel] = {"version": version, "hash": None, "grande": True}
            continue
        try:
            datos = f.read_bytes()
        except OSError:
            continue
        try:
            datos.decode("utf-8")
            arbol[rel] = {"version": version, "hash": _hash_bytes(datos)}
        except UnicodeDecodeError:
            arbol[rel] = {"version": version, "hash": _hash_bytes(datos), "binario": True}
    return arbol


def pares_renombre_directorio(proyecto: str, origen: str, destino: str) -> list[tuple[str, str]]:
    """[(ruta_vieja, ruta_nueva)] de los archivos dentro de un directorio.

    Las versiones viven POR ARCHIVO: un rename de carpeta debe mover la
    versión de cada archivo, no la de la carpeta (que es 0).
    """
    try:
        sala = sala_de(proyecto)
        p = ruta_segura(sala, origen)
    except ValueError:
        return []
    pares: list[tuple[str, str]] = []
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if not f.is_file():
                continue
            try:
                rel_viejo = str(f.relative_to(sala.directorio))
            except ValueError:
                continue
            if es_archivo_conflicto(rel_viejo):
                continue
            pares.append((rel_viejo, destino + rel_viejo[len(origen):]))
    return pares


def colectar_borrado(proyecto: str, ruta: str) -> list[tuple[str, int]]:
    """[(ruta_rel, version)] de un archivo o de los archivos de un directorio.

    Se llama ANTES de borrar, para poder notificar el sync después.
    """
    try:
        sala = sala_de(proyecto)
        p = ruta_segura(sala, ruta)
    except ValueError:
        return []
    out: list[tuple[str, int]] = []
    if p.is_file():
        if es_archivo_conflicto(ruta):
            return []
        out.append((ruta, _version_archivo(proyecto, ruta)))
    elif p.is_dir():
        for f in sorted(p.rglob("*")):
            if not f.is_file():
                continue
            try:
                rel = str(f.relative_to(sala.directorio))
            except ValueError:
                continue
            if es_archivo_conflicto(rel):
                continue
            out.append((rel, _version_archivo(proyecto, rel)))
    return out

# ------------------------------------------------------------------ aplicar
async def aplicar_file_sync(proyecto: str, msg: dict) -> dict:
    """Aplica un file_sync entrante al disco local. Nunca pierde trabajo.

    La carpeta del equipo sale del propio mensaje (``msg["carpeta"]``) y
    se valida que la ruta viva dentro de ella: un peer de otro proyecto
    no puede escribir fuera de su carpeta aunque falsifique el mensaje.
    Reglas:
    - base_version == versión local → aplicar, fijar versión del emisor.
    - En cualquier otro caso (o divergente=True) → CONFLICTO: se mantiene
      el contenido local y el entrante se guarda como backup
      ``<ruta>.conflict.<autor>.<timestamp>``.
    - binario → no se escribe nada, se notifica sync_unsupported.
    - duplicado (sync_id/fetch_id ya visto) → no-op.
    Retorna {aplicado, conflicto, motivo, ...}. Nunca registra contenido.
    """
    ok, motivo = validar_mensaje_sync(msg)
    if not ok:
        return {"aplicado": False, "conflicto": False, "motivo": motivo}

    sync_id = msg.get("sync_id") or ""
    if sync_id and not _recordar_sync_id(sync_id):
        return {"aplicado": False, "conflicto": False, "motivo": "duplicado"}
    fetch_id = msg.get("fetch_id") or ""
    if fetch_id and not _recordar_fetch_id(fetch_id):
        return {"aplicado": False, "conflicto": False, "motivo": "duplicado_fetch"}

    carpeta = msg["carpeta"]
    ruta = msg["ruta"]
    operacion = msg["operacion"]
    base_version = int(msg["base_version"])
    version = int(msg["version"])
    autor = str(msg.get("autor", "peer") or "peer")
    contenido = msg.get("contenido")
    divergente = bool(msg.get("divergente"))

    if msg.get("binario"):
        await enviar_todos({
            "tipo": "sync_unsupported", "proyecto": proyecto, "carpeta": carpeta,
            "ruta": ruta, "motivo": "binario",
        })
        return {"aplicado": False, "conflicto": False, "motivo": "binario"}

    if contenido is not None:
        if _hash_contenido(contenido) != (msg.get("hash") or ""):
            return {"aplicado": False, "conflicto": False, "motivo": "hash_invalido"}

    # Un upsert sin contenido no escribe nada: escribir "" borraría el archivo.
    if operacion == "upsert" and contenido is None:
        if msg.get("grande"):
            await enviar_todos({
                "tipo": "sync_unsupported", "proyecto": proyecto, "carpeta": carpeta,
                "ruta": ruta, "motivo": "demasiado_grande",
            })
            return {"aplicado": False, "conflicto": False, "motivo": "demasiado_grande"}
        return {"aplicado": False, "conflicto": False, "motivo": "sin_contenido"}

    try:
        sala = sala_de(proyecto)
        p = ruta_segura(sala, ruta)
    except ValueError as e:
        return {"aplicado": False, "conflicto": False, "motivo": str(e)}

    local_version = _version_archivo(proyecto, ruta)

    if operacion == "upsert":
        if not divergente and local_version == base_version:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(contenido or "", encoding="utf-8")
            _fijar_version_archivo(proyecto, ruta, version, autor)
            await enviar_todos({
                "tipo": "file_changed", "proyecto": proyecto, "carpeta": carpeta,
                "ruta": ruta, "version": version, "autor": autor, "operacion": "upsert",
                "size": len(contenido or ""),
                "contenido": contenido if contenido is not None and len(contenido) <= MAX_INLINE_CONTENIDO else None,
            })
            await difundir_arbol()
            return {"aplicado": True, "conflicto": False, "version": version}
        # Conflicto: mantener local, guardar entrante como backup.
        backup = nombre_conflicto(ruta, autor)
        try:
            pb = ruta_segura(sala, backup)
            pb.parent.mkdir(parents=True, exist_ok=True)
            pb.write_text(contenido or "", encoding="utf-8")
        except ValueError:
            backup = ""
        await enviar_todos({
            "tipo": "file_changed", "proyecto": proyecto, "carpeta": carpeta,
            "ruta": ruta, "version": local_version, "autor": autor, "operacion": "upsert",
            "conflicto": True, "conflicto_archivo": backup,
        })
        await difundir_arbol()
        return {"aplicado": False, "conflicto": True,
                "conflicto_archivo": backup, "version": local_version}

    if operacion == "delete":
        if not divergente and local_version == base_version:
            try:
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    # Un delete de sync es por archivo; jamás borrar un
                    # directorio entero por un mensaje remoto.
                    return {"aplicado": False, "conflicto": False, "motivo": "es_directorio"}
            except OSError:
                pass
            _eliminar_version_archivo(proyecto, ruta)
            await enviar_todos({
                "tipo": "file_changed", "proyecto": proyecto, "carpeta": carpeta,
                "ruta": ruta, "version": version, "autor": autor, "operacion": "delete",
            })
            await difundir_arbol()
            return {"aplicado": True, "conflicto": False}
        # Conflicto de borrado: se mantiene el archivo local. No hay
        # contenido entrante que respaldar (un delete no trae contenido).
        await enviar_todos({
            "tipo": "file_changed", "proyecto": proyecto, "carpeta": carpeta,
            "ruta": ruta, "version": local_version, "autor": autor, "operacion": "delete",
            "conflicto": True, "conflicto_archivo": "",
        })
        return {"aplicado": False, "conflicto": True, "version": local_version}

    if operacion == "rename":
        origen = str(msg.get("origen") or "")
        try:
            p_origen = ruta_segura(sala, origen)
        except ValueError as e:
            return {"aplicado": False, "conflicto": False, "motivo": str(e)}
        version_origen = _version_archivo(proyecto, origen)
        if not divergente and version_origen == base_version:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p_origen.is_dir():
                # Rename de carpeta: mover la versión de CADA archivo
                # (sin cambiar su valor) y renombrar la carpeta.
                pares = pares_renombre_directorio(proyecto, origen, ruta)
                if p_origen.exists():
                    p_origen.rename(p)
                for rel_viejo, rel_nuevo in pares:
                    _mover_version_archivo(proyecto, rel_viejo, rel_nuevo)
            else:
                if p_origen.exists():
                    p_origen.rename(p)
                _mover_version_archivo(proyecto, origen, ruta)
                _fijar_version_archivo(proyecto, ruta, version, autor)
            await enviar_todos({
                "tipo": "file_changed", "proyecto": proyecto, "carpeta": carpeta,
                "ruta": ruta, "version": version, "autor": autor, "operacion": "rename",
                "origen": origen, "destino": ruta,
            })
            await difundir_arbol()
            return {"aplicado": True, "conflicto": False}
        backup = ""
        if contenido is not None:
            backup = nombre_conflicto(ruta, autor)
            try:
                pb = ruta_segura(sala, backup)
                pb.parent.mkdir(parents=True, exist_ok=True)
                pb.write_text(contenido, encoding="utf-8")
            except ValueError:
                backup = ""
        await enviar_todos({
            "tipo": "file_changed", "proyecto": proyecto, "carpeta": carpeta,
            "ruta": ruta, "version": local_version, "autor": autor, "operacion": "rename",
            "origen": origen, "destino": ruta,
            "conflicto": True, "conflicto_archivo": backup,
        })
        await difundir_arbol()
        return {"aplicado": False, "conflicto": True,
                "conflicto_archivo": backup, "version": local_version}

    return {"aplicado": False, "conflicto": False, "motivo": "operación desconocida"}


async def aplicar_file_big(proyecto: str, msg: dict) -> dict:
    """Aplica un file_big entrante: el archivo COMPLETO viaja en un solo
    mensaje (bytes en base64 + sha256 del archivo completo).

    Reglas (las mismas garantías que aplicar_file_sync, sin atajos):
    - El sha256 del archivo COMPLETO se verifica ANTES de escribir nada.
      Si no coincide: se descarta todo, error real, cero bytes escritos.
    - base_version == versión local → se escribe y se fija la versión.
    - En cualquier otro caso → CONFLICTO: se mantiene el contenido local
      y el entrante se guarda como backup ``<ruta>.conflict.<autor>.<ts>``.
      Nunca se sobrescribe en silencio.
    - duplicado (sync_id ya visto) → no-op.
    Retorna {aplicado, conflicto, motivo, ...}. Nunca registra contenido.
    """
    ok, motivo = validar_mensaje_sync(msg)
    if not ok:
        return {"aplicado": False, "conflicto": False, "motivo": motivo}

    sync_id = msg.get("sync_id") or ""
    if sync_id and not _recordar_sync_id(sync_id):
        return {"aplicado": False, "conflicto": False, "motivo": "duplicado"}

    if msg.get("operacion") != "upsert":
        return {"aplicado": False, "conflicto": False,
                "motivo": "operación no soportada en file_big"}

    carpeta = msg["carpeta"]
    ruta = msg["ruta"]
    base_version = int(msg["base_version"])
    version = int(msg["version"])
    autor = str(msg.get("autor", "peer") or "peer")
    divergente = bool(msg.get("divergente"))
    esperado_sha = str(msg["file_sha256"]).lower()
    total_bytes = int(msg["total_bytes"])

    try:
        datos = base64.b64decode(msg["datos_b64"], validate=True)
    except Exception:
        return {"aplicado": False, "conflicto": False, "motivo": "datos_corruptos"}
    if len(datos) != total_bytes:
        return {"aplicado": False, "conflicto": False, "motivo": "tamano_no_coincide"}
    # Verificación del archivo COMPLETO antes de tocar el disco.
    if _hash_bytes(datos) != esperado_sha:
        return {"aplicado": False, "conflicto": False, "motivo": "hash_invalido"}

    try:
        sala = sala_de(proyecto)
        p = ruta_segura(sala, ruta)
    except ValueError as e:
        return {"aplicado": False, "conflicto": False, "motivo": str(e)}

    local_version = _version_archivo(proyecto, ruta)

    # Ya lo tengo idéntico: no reescribir (evita bucles y escrituras inútiles).
    if p.is_file() and local_version == version:
        try:
            if _hash_bytes(p.read_bytes()) == esperado_sha:
                return {"aplicado": True, "conflicto": False,
                        "motivo": "sin_cambios", "version": version}
        except OSError:
            pass

    if not divergente and local_version == base_version:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(datos)
        except OSError as e:
            return {"aplicado": False, "conflicto": False,
                    "motivo": f"no se pudo escribir: {e}"}
        _fijar_version_archivo(proyecto, ruta, version, autor)
        await enviar_todos({
            "tipo": "file_changed", "proyecto": proyecto, "carpeta": carpeta,
            "ruta": ruta, "version": version, "autor": autor, "operacion": "upsert",
            "size": len(datos), "contenido": None,
        })
        await difundir_arbol()
        return {"aplicado": True, "conflicto": False, "version": version}

    # Conflicto: mantener local, guardar el entrante (verificado) como backup.
    backup = nombre_conflicto(ruta, autor)
    try:
        pb = ruta_segura(sala, backup)
        pb.parent.mkdir(parents=True, exist_ok=True)
        pb.write_bytes(datos)
    except (ValueError, OSError):
        backup = ""
    await enviar_todos({
        "tipo": "file_changed", "proyecto": proyecto, "carpeta": carpeta,
        "ruta": ruta, "version": local_version, "autor": autor, "operacion": "upsert",
        "conflicto": True, "conflicto_archivo": backup,
    })
    await difundir_arbol()
    return {"aplicado": False, "conflicto": True,
            "conflicto_archivo": backup, "version": local_version}


# ------------------------------------------------------------------ equipos (registro del relay)
# Equipos de proyecto que viven en ESTE servidor (cuando actúa como relay).
# Persistentes en JSON bajo ~/.atlantis/ (estado local del servidor,
# nunca en el zip): un reinicio del relay no borra los equipos. Solo
# metadato: códigos, miembros y tokens de invitado. NUNCA contenido de
# archivos. OJO: el HubStore solo persiste sus claves tabulares; el resto
# es overlay en memoria y se perdería al reiniciar. Por eso estos
# registros usan su propio archivo con escritura atómica (tmp+replace).
def _ruta_registro(nombre: str) -> Path:
    return config.ATLANTIS_USER_DIR / f"{nombre}.json"


def _cargar_registro(nombre: str, por_defecto):
    try:
        return json.loads(_ruta_registro(nombre).read_text(encoding="utf-8"))
    except Exception:
        return por_defecto


def _guardar_registro(nombre: str, datos) -> None:
    try:
        path = _ruta_registro(nombre)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _cargar_equipos() -> dict:
    datos = _cargar_registro("sync_equipos", {})
    if not isinstance(datos, dict):
        return {}
    # Migración: cada miembro tiene agent_id estable + color. Los equipos
    # viejos (sin estos campos) los reciben aquí; se guarda solo si cambió.
    cambiado = False
    for equipo in datos.values():
        if not isinstance(equipo, dict):
            continue
        for m in equipo.get("miembros", []) or []:
            if not isinstance(m, dict):
                continue
            if not m.get("agent_id"):
                m["agent_id"] = _nuevo_agent_id()
                cambiado = True
            if not m.get("agent_color"):
                m["agent_color"] = _color_agente(m["agent_id"])
                cambiado = True
    if cambiado:
        _guardar_equipos(datos)
    return datos


def _guardar_equipos(equipos: dict) -> None:
    _guardar_registro("sync_equipos", equipos)


def _generar_codigo_equipo() -> str:
    return "ATL-" + secrets.token_hex(3).upper()


def _nuevo_equipo_id() -> str:
    return "team-" + secrets.token_hex(6)


def miembros_publicos(equipo: dict) -> list[dict]:
    """Miembros sin guest_token: los tokens nunca salen del servidor.
    Cada miembro lleva su identidad de agente (id, nombre, color, dueño)
    para que siempre se sepa qué agente habla."""
    out = []
    for m in (equipo or {}).get("miembros", []) or []:
        copia = {k: v for k, v in m.items() if k != "guest_token"}
        copia["agente"] = identidad_agente(m)
        out.append(copia)
    return out


def equipo_publico(equipo: dict) -> dict:
    """Copia del equipo sin tokens (para respuestas y broadcasts)."""
    if not equipo:
        return {}
    copia = dict(equipo)
    copia["miembros"] = miembros_publicos(equipo)
    return copia


def equipo_por_id(equipo_id: str) -> dict | None:
    if not _equipo_id_valido(equipo_id):
        return None
    return _cargar_equipos().get(equipo_id)


def equipo_de_codigo(codigo: str) -> tuple[dict | None, str]:
    """(equipo, equipo_id) del código, o (None, ""). Solo códigos
    vigentes cuentan: revocado/expirado no resuelve equipo."""
    codigo = str(codigo or "").strip().upper()
    if not codigo:
        return None, ""
    for equipo_id, equipo in _cargar_equipos().items():
        obj = _buscar_codigo(equipo.get("codigos"), codigo)
        if obj is not None and _estado_codigo(obj) == "vigente":
            return equipo, equipo_id
    return None, ""


def equipo_de_token(token: str) -> tuple[dict | None, str]:
    """(equipo, equipo_id) al que pertenece un guest_token de sync."""
    if not token:
        return None, ""
    for equipo_id, equipo in _cargar_equipos().items():
        for m in equipo.get("miembros", []) or []:
            if m.get("guest_token") == token:
                return equipo, equipo_id
    return None, ""


def equipo_de_proyecto(proyecto: str, carpeta: str) -> tuple[dict | None, str]:
    """(equipo, equipo_id) de una carpeta, o (None, ""). Un proyecto
    tiene UN equipo: compartir de nuevo la misma carpeta reutiliza el
    equipo existente y añade un código nuevo (equipo abierto)."""
    for equipo_id, equipo in _cargar_equipos().items():
        if equipo.get("proyecto") == proyecto and equipo.get("carpeta") == carpeta:
            return equipo, equipo_id
    return None, ""


def _nuevo_codigo_objeto(expira_minutos: int | None = None) -> dict:
    """Un código nuevo: {code, expira_en, revocado}. Por defecto expira
    en 24 h; expira_minutos=0 (o None) significa sin expiración."""
    if expira_minutos is None:
        expira_minutos = CODIGO_EXPIRA_DEFECTO_MIN
    expira_en = None
    if expira_minutos and expira_minutos > 0:
        expira_en = (datetime.now() + timedelta(minutes=int(expira_minutos))).isoformat()
    return {"code": _generar_codigo_equipo(), "expira_en": expira_en,
            "revocado": False}


def crear_equipo_sync(proyecto: str, carpeta: str, host_info: dict,
                      expira_minutos: int | None = None) -> dict:
    """Crea el equipo de una carpeta o, si ya existe, le añade un código
    nuevo (equipo abierto: el mismo equipo, más códigos). Los códigos
    expiran (por defecto 24 h) y se pueden revocar. Retorna
    {equipo_id, code, expira_en, equipo, nuevo}."""
    if not _proyecto_seguro(proyecto):
        raise ValueError("proyecto inválido")
    if not carpeta_segura(carpeta):
        raise ValueError("carpeta inválida")
    ahora = datetime.now().isoformat()
    equipos = _cargar_equipos()
    equipo, equipo_id = None, None
    for eid, e in equipos.items():
        if e.get("proyecto") == proyecto and e.get("carpeta") == carpeta:
            equipo, equipo_id = e, eid
            break
    nuevo_codigo = _nuevo_codigo_objeto(expira_minutos)
    if equipo is not None:
        codigos = equipo.setdefault("codigos", [])
        if _buscar_codigo(codigos, nuevo_codigo["code"]) is None:
            codigos.append(nuevo_codigo)
        equipos[equipo_id] = equipo
        _guardar_equipos(equipos)
        return {"equipo_id": equipo_id, "code": nuevo_codigo["code"],
                "expira_en": nuevo_codigo["expira_en"],
                "equipo": equipo_publico(equipo), "nuevo": False}
    equipo_id = _nuevo_equipo_id()
    host_id = _nuevo_agent_id()
    equipo = {
        "equipo_id": equipo_id,
        "proyecto": proyecto,
        "carpeta": carpeta,
        "estado": "activo",
        "codigos": [nuevo_codigo],
        "miembros": [{**host_info, "unido_en": ahora, "es_host": True,
                      "guest_token": "", "agent_id": host_id,
                      "agent_color": _color_agente(host_id)}],
        "creado": ahora,
    }
    equipos[equipo_id] = equipo
    _guardar_equipos(equipos)
    return {"equipo_id": equipo_id, "code": nuevo_codigo["code"],
            "expira_en": nuevo_codigo["expira_en"],
            "equipo": equipo_publico(equipo), "nuevo": True}


def listar_codigos_sync(equipo_id: str) -> list[dict]:
    """Códigos del equipo con su estado (vigente/expirado/revocado)."""
    equipo = equipo_por_id(equipo_id)
    if equipo is None:
        raise ValueError("Equipo no encontrado.")
    out = []
    for c in equipo.get("codigos", []) or []:
        norm = _normalizar_codigo(c)
        out.append({"code": norm["code"], "expira_en": norm["expira_en"],
                    "revocado": norm["revocado"],
                    "estado": _estado_codigo(norm)})
    return out


def revocar_codigo_sync(equipo_id: str, codigo: str) -> dict:
    """Revoca UN código del equipo sin cerrar el equipo ni invalidar
    los demás códigos. El código revocado se rechaza al unirse."""
    equipos = _cargar_equipos()
    equipo = equipos.get(equipo_id)
    if equipo is None:
        raise ValueError("Equipo no encontrado.")
    codigo_norm = str(codigo or "").strip().upper()
    encontrado = None
    for c in equipo.get("codigos", []) or []:
        if isinstance(c, dict) and str(c.get("code", "")).strip().upper() == codigo_norm:
            encontrado = c
            break
        if isinstance(c, str) and c.strip().upper() == codigo_norm:
            encontrado = c
            break
    if encontrado is None:
        raise ValueError("Código no encontrado en este equipo.")
    if isinstance(encontrado, str):
        # Código legado (str): se reemplaza por su objeto revocado.
        equipo["codigos"] = [
            {"code": encontrado.strip().upper(), "expira_en": None, "revocado": True}
            if (isinstance(x, str) and x.strip().upper() == codigo_norm) else x
            for x in equipo.get("codigos", [])]
    else:
        encontrado["revocado"] = True
    equipos[equipo_id] = equipo
    _guardar_equipos(equipos)
    return {"ok": True, "code": codigo_norm}


def unirse_equipo_sync(codigo: str, miembro: dict,
                       carpeta_esperada: str | None = None) -> dict:
    """Une un miembro al equipo de un código. Idempotente por
    agente+usuario: si ya estaba dentro, devuelve su token existente.
    Si se da ``carpeta_esperada`` y el código es de otra carpeta, se
    rechaza ANTES de añadir al miembro (sin dejar miembros fantasma).
    Códigos revocados → "Código revocado."; expirados → "Código
    expirado. Genera uno nuevo."; inexistentes → "Código inválido.".
    Retorna {equipo_id, guest_token, equipo}."""
    equipos = _cargar_equipos()
    equipo, equipo_id = None, None
    codigo_norm = str(codigo or "").strip().upper()
    obj_codigo = None
    for eid, e in equipos.items():
        obj = _buscar_codigo(e.get("codigos"), codigo_norm)
        if obj is not None:
            equipo, equipo_id, obj_codigo = e, eid, obj
            break
    if equipo is None or obj_codigo is None:
        raise ValueError("Código inválido.")
    estado = _estado_codigo(obj_codigo)
    if estado == "revocado":
        raise ValueError("Código revocado.")
    if estado == "expirado":
        raise ValueError("Código expirado. Genera uno nuevo.")
    if carpeta_esperada and equipo.get("carpeta") != carpeta_esperada:
        raise ValueError(
            f"Ese código es del proyecto «{equipo.get('carpeta')}», no de «{carpeta_esperada}».")
    miembros = equipo.setdefault("miembros", [])
    # Idempotencia POR USUARIO (no por nombre de agente): si el usuario
    # renombró su agente y vuelve a unirse, recupera SU miembro con el
    # nombre renombrado, sin crear un duplicado. La entrada del anfitrión
    # nunca se devuelve en un join (el anfitrión no se une por código):
    # unirse con el nombre del anfitrión se rechaza (anti-suplantación).
    usuario_clave = str(miembro.get("user_name", "")).strip().lower()
    for m in miembros:
        if m.get("es_host") and str(m.get("user_name", "")).strip().lower() == usuario_clave:
            raise ValueError("Ese nombre ya está en uso en este equipo.")
    for m in miembros:
        if m.get("es_host"):
            continue
        if str(m.get("user_name", "")).strip().lower() == usuario_clave:
            token = m.get("guest_token") or ""
            if not token:
                token = secrets.token_hex(24)
                m["guest_token"] = token
                equipos[equipo_id] = equipo
                _guardar_equipos(equipos)
            # El guest_token también vale para endpoints normales (no
            # admin) de este servidor: así el invitado puede usar el
            # hub desde su teléfono con su token del equipo.
            config.registrar_invitado(token, codigo_norm, m.get("user_name", ""),
                                      m.get("agent_name", ""))
            return {"equipo_id": equipo_id, "guest_token": token,
                    "equipo": equipo_publico(equipo), "ya_miembro": True}
    if len(miembros) >= 8:
        raise ValueError("El equipo está completo (máximo 8 miembros).")
    ahora = datetime.now().isoformat()
    token = secrets.token_hex(24)
    agent_id = _nuevo_agent_id()
    miembros.append({**miembro, "unido_en": ahora, "es_host": False,
                     "guest_token": token, "agent_id": agent_id,
                     "agent_color": _color_agente(agent_id)})
    equipos[equipo_id] = equipo
    _guardar_equipos(equipos)
    # El guest_token también vale para endpoints normales (no admin)
    # de este servidor (invitado desde su teléfono).
    config.registrar_invitado(token, codigo_norm, miembro.get("user_name", ""),
                              miembro.get("agent_name", ""))
    return {"equipo_id": equipo_id, "guest_token": token,
            "equipo": equipo_publico(equipo), "ya_miembro": False}


def _miembro_por_token(equipo: dict, token: str) -> dict | None:
    """Miembro dueño del token: host si es el TOKEN del dueño, o el
    invitado con ese guest_token. None si el token no es del equipo."""
    if not token:
        return None
    import hmac as _hmac
    if _hmac.compare_digest(str(token), str(config.TOKEN or "")):
        for m in equipo.get("miembros", []) or []:
            if m.get("es_host"):
                return m
        return None
    for m in equipo.get("miembros", []) or []:
        if m.get("guest_token") == token:
            return m
    return None


def renombrar_agente_sync(equipo_id: str, token: str, nuevo_nombre: str) -> dict:
    """Renombra el agente DEL QUE LLAMA (solo el suyo). El token manda:
    dueño → su agente anfitrión; invitado → su propio agente. Nadie
    puede renombrar el agente de otro. Retorna {ok, agente}."""
    equipos = _cargar_equipos()
    equipo = equipos.get(equipo_id)
    if equipo is None:
        raise ValueError("Equipo no encontrado.")
    miembro = _miembro_por_token(equipo, token)
    if miembro is None:
        raise ValueError("No perteneces a este equipo.")
    nombre = str(nuevo_nombre or "").strip()
    if not nombre or len(nombre) > 40:
        raise ValueError("El nombre del agente debe tener entre 1 y 40 caracteres.")
    if not re.fullmatch(r"[A-Za-z0-9 _\-.áéíóúÁÉÍÓÚñÑ]+", nombre):
        raise ValueError("El nombre del agente tiene caracteres no permitidos.")
    miembro["agent_name"] = nombre
    equipos[equipo_id] = equipo
    _guardar_equipos(equipos)
    return {"ok": True, "agente": identidad_agente(miembro)}


def salir_equipo_sync(equipo_id: str, token: str, agent_name: str,
                      user_name: str, es_dueno: bool = False) -> dict:
    """Saca un miembro de un equipo. Un invitado solo puede sacarse a sí
    mismo (su guest_token manda); el dueño o una petición local pueden
    sacar a cualquiera. Si sale el anfitrión o no queda nadie, el equipo
    se cierra. Retorna {ok, cerrado, equipo}."""
    equipos = _cargar_equipos()
    equipo = equipos.get(equipo_id)
    if equipo is None:
        raise ValueError("Equipo no encontrado.")
    miembros = equipo.get("miembros", [])
    # El saliente se identifica por el user_name del body (el
    # agent_name puede estar desactualizado tras un renombre; el
    # user_name nunca cambia). El token solo AUTORIZA: un invitado
    # solo puede sacar a su propio miembro (su guest_token manda).
    saliente = None
    usuario_clave = str(user_name).strip().lower()
    for m in miembros:
        if str(m.get("user_name", "")).strip().lower() == usuario_clave:
            saliente = m
            break
    if saliente is None:
        raise ValueError("No estás en este equipo.")
    # Sin privilegio de dueño/local, el token DEBE ser el guest_token
    # del saliente (un token vacío jamás autoriza sacar a nadie).
    if not es_dueno:
        if not token or saliente.get("guest_token") != token:
            raise ValueError("Solo puedes salir tú mismo del equipo.")
    es_host = bool(saliente.get("es_host"))
    restantes = [m for m in miembros if m is not saliente]
    if es_host or not restantes:
        # El equipo se cierra: todos los tokens de invitado se revocan.
        for m in miembros:
            config.revocar_invitado(m.get("guest_token") or "")
        equipos.pop(equipo_id, None)
        _guardar_equipos(equipos)
        return {"ok": True, "cerrado": True, "equipo": {}}
    # El que sale pierde su token de invitado.
    config.revocar_invitado(saliente.get("guest_token") or "")
    equipo["miembros"] = restantes
    equipos[equipo_id] = equipo
    _guardar_equipos(equipos)
    return {"ok": True, "cerrado": False, "equipo": equipo_publico(equipo)}


# ---------------------------------------------------------------- aprobación dual
def _aprobaciones_de(equipo: dict) -> dict:
    """Registro de aprobaciones del equipo: {fingerprint: entrada}."""
    return equipo.setdefault("aprobaciones", {})


def _falta_aprobar(equipo: dict, aprobaciones: list) -> list[str]:
    """Quién falta por aprobar esta versión: el anfitrión (si no
    aprobó) y/o un compañero (si ningún invitado aprobó)."""
    aprobados = {a.get("usuario") for a in aprobaciones}
    falta = []
    miembros = equipo.get("miembros", []) or []
    host = next((m for m in miembros if m.get("es_host")), None)
    host_nombre = host.get("user_name") if host else None
    if host is not None and host_nombre not in aprobados:
        falta.append(host_nombre)
    if not any(a.get("usuario") != host_nombre for a in aprobaciones):
        falta.append("un compañero")
    return falta


async def aprobar_proyecto_sync(equipo_id: str, token: str, fingerprint: str,
                                 nota: str = "") -> dict:
    """Aprueba la versión ``fingerprint`` del proyecto (el "Me gusta").
    Solo cuando el anfitrión Y un invitado aprueban LA MISMA versión,
    el estado pasa a "terminando" y se publica la tarea de terminar el
    desarrollo en el bus del equipo (la recogen los dos agentes)."""
    equipos = _cargar_equipos()
    equipo = equipos.get(equipo_id)
    if equipo is None:
        raise ValueError("Equipo no encontrado.")
    miembro = _miembro_por_token(equipo, token)
    if miembro is None:
        raise ValueError("No perteneces a este equipo.")
    fingerprint = str(fingerprint or "").strip()
    if not fingerprint:
        raise ValueError("fingerprint requerido.")
    registro = _aprobaciones_de(equipo)
    entrada = registro.setdefault(fingerprint, {
        "aprobaciones": [], "estado": "esperando", "resumen": "",
        "tarea_id": ""})
    aprobaciones = entrada["aprobaciones"]
    usuario = miembro.get("user_name")
    ahora = datetime.now().isoformat()
    existente = next((a for a in aprobaciones if a.get("usuario") == usuario), None)
    if existente is not None:
        existente["nota"] = str(nota or "")[:280]
        existente["fecha"] = ahora
    else:
        aprobaciones.append({"usuario": usuario, "nota": str(nota or "")[:280],
                             "fecha": ahora})
    # ¿Ambos? Anfitrión + al menos un invitado aprobaron esta versión.
    host = next((m for m in equipo.get("miembros", []) or []
                 if m.get("es_host")), None)
    host_aprobo = host is not None and any(
        a.get("usuario") == host.get("user_name") for a in aprobaciones)
    invitado_aprobo = any(
        a.get("usuario") != (host.get("user_name") if host else None)
        for a in aprobaciones)
    ambos = bool(host_aprobo and invitado_aprobo)
    if ambos and entrada["estado"] == "esperando":
        entrada["estado"] = "terminando"
        entrada["tarea_id"] = "fin-" + secrets.token_hex(4)
        equipos[equipo_id] = equipo
        _guardar_equipos(equipos)
        # Tarea real al bus del equipo: los dos agentes la recogen.
        notas = [{"usuario": a["usuario"], "nota": a.get("nota", "")}
                 for a in aprobaciones]
        await publicar_en_bus_equipo(
            equipo.get("proyecto", "general"), equipo.get("carpeta", ""),
            equipo_id, de="Sistema", para="todos",
            texto=(f"Ambos aprobaron la versión {fingerprint} del proyecto "
                   f"«{equipo.get('carpeta')}». Agentes del equipo: terminen "
                   "el desarrollo de forma real — lean el proyecto, las "
                   "notas de aprobación y la spec/TODO/README si existe, "
                   "hagan ediciones reales de código vía sync, corran los "
                   "tests reales del proyecto y entreguen un resumen de qué "
                   "cambiaron. Si algo no se puede terminar, digan qué "
                   "queda pendiente."),
            tipo="tarea_terminar",
            extra={"tarea_id": entrada["tarea_id"], "fingerprint": fingerprint,
                   "notas": notas, "carpeta": equipo.get("carpeta", "")})
    else:
        equipos[equipo_id] = equipo
        _guardar_equipos(equipos)
    return {"ok": True, "fingerprint": fingerprint,
            "aprobaciones": aprobaciones, "ambos": ambos,
            "estado": entrada["estado"], "falta": _falta_aprobar(equipo, aprobaciones),
            "resumen": entrada.get("resumen", ""),
            "tarea_id": entrada.get("tarea_id", "")}


def retirar_aprobacion_sync(equipo_id: str, token: str) -> dict:
    """Retira la aprobación del que llama (todas las versiones). Si la
    tarea ya se disparó ("terminando"), el trabajo en curso no se
    cancela (no se puede desenviar), pero la aprobación queda retirada
    y una aprobación en versión nueva ya no dispara con la vieja."""
    equipos = _cargar_equipos()
    equipo = equipos.get(equipo_id)
    if equipo is None:
        raise ValueError("Equipo no encontrado.")
    miembro = _miembro_por_token(equipo, token)
    if miembro is None:
        raise ValueError("No perteneces a este equipo.")
    usuario = miembro.get("user_name")
    registro = _aprobaciones_de(equipo)
    for entrada in registro.values():
        entrada["aprobaciones"] = [a for a in entrada.get("aprobaciones", [])
                                   if a.get("usuario") != usuario]
    equipos[equipo_id] = equipo
    _guardar_equipos(equipos)
    return {"ok": True, "usuario": usuario}


def completar_tarea_sync(equipo_id: str, token: str, tarea_id: str,
                         resumen: str) -> dict:
    """Un agente del equipo reporta que terminó el desarrollo (tarea
    de aprobación dual): el estado pasa a "terminado" con su resumen."""
    equipos = _cargar_equipos()
    equipo = equipos.get(equipo_id)
    if equipo is None:
        raise ValueError("Equipo no encontrado.")
    if _miembro_por_token(equipo, token) is None:
        raise ValueError("No perteneces a este equipo.")
    for fingerprint, entrada in _aprobaciones_de(equipo).items():
        if entrada.get("tarea_id") == tarea_id:
            if entrada.get("estado") == "terminando":
                entrada["estado"] = "terminado"
                entrada["resumen"] = str(resumen or "")[:2000]
                equipos[equipo_id] = equipo
                _guardar_equipos(equipos)
                return {"ok": True, "fingerprint": fingerprint,
                        "estado": "terminado", "resumen": entrada["resumen"],
                        "aprobaciones": entrada.get("aprobaciones", [])}
            return {"ok": False, "error": "La tarea no está en curso.",
                    "estado": entrada.get("estado")}
    raise ValueError("Tarea no encontrada en este equipo.")


def estado_aprobaciones_sync(equipo_id: str, fingerprint: str) -> dict:
    """Estado de aprobación de UNA versión (fingerprint) del proyecto."""
    equipo = equipo_por_id(equipo_id)
    if equipo is None:
        raise ValueError("Equipo no encontrado.")
    fingerprint = str(fingerprint or "").strip()
    entrada = (equipo.get("aprobaciones", {}) or {}).get(fingerprint) or {
        "aprobaciones": [], "estado": "esperando", "resumen": "", "tarea_id": ""}
    aprobaciones = entrada.get("aprobaciones", [])
    host = next((m for m in equipo.get("miembros", []) or []
                 if m.get("es_host")), None)
    host_aprobo = host is not None and any(
        a.get("usuario") == host.get("user_name") for a in aprobaciones)
    invitado_aprobo = any(
        a.get("usuario") != (host.get("user_name") if host else None)
        for a in aprobaciones)
    return {"ok": True, "fingerprint": fingerprint,
            "aprobaciones": aprobaciones,
            "ambos": bool(host_aprobo and invitado_aprobo),
            "estado": entrada.get("estado", "esperando"),
            "falta": _falta_aprobar(equipo, aprobaciones),
            "resumen": entrada.get("resumen", ""),
            "tarea_id": entrada.get("tarea_id", "")}


def listar_equipos_sync() -> list[dict]:
    return [equipo_publico(e) for e in _cargar_equipos().values()]


# ------------------------------------------------------------------ relay
# Peers registrados en ESTE servidor: ws -> {peer_id, proyecto, carpeta, equipo_id, autor}.
PEERS: dict[WebSocket, dict] = {}
_FETCH_RR: dict[str, int] = {}


def registrar_peer(ws: WebSocket, peer_id: str, proyecto: str, carpeta: str,
                   equipo_id: str, autor: str) -> None:
    PEERS[ws] = {"peer_id": peer_id, "proyecto": proyecto, "carpeta": carpeta,
                 "equipo_id": equipo_id, "autor": autor}


def olvidar_peer(ws: WebSocket) -> None:
    PEERS.pop(ws, None)


def es_peer(ws: WebSocket) -> bool:
    return ws in PEERS


def peers_de_equipo(equipo_id: str) -> list[tuple[WebSocket, dict]]:
    return [(ws, info) for ws, info in list(PEERS.items())
            if info.get("equipo_id") == equipo_id]


async def _enviar_peer(ws: WebSocket, msg: dict) -> bool:
    """Envía a un peer. Sin logs del contenido. False si la conexión murió."""
    try:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))
        return True
    except Exception:
        olvidar_peer(ws)
        return False


async def reenviar_a_peers(msg: dict, equipo_id: str, excepto: WebSocket | None = None) -> int:
    """Relay: reenvía a todos los peers del MISMO equipo salvo el emisor.

    Nunca persiste contenido. Retorna cuántos peers lo recibieron.
    """
    entregados = 0
    for ws, _info in peers_de_equipo(equipo_id):
        if ws is excepto:
            continue
        if await _enviar_peer(ws, msg):
            entregados += 1
    return entregados


async def reenviar_fetch(msg: dict, equipo_id: str, excepto: WebSocket | None = None) -> bool:
    """Relay de sync_fetch: va a UN solo peer (el pedido, o round-robin).

    Así una sola respuesta llega al que pidió, y no hay duplicados que
    disparen conflictos espurios.
    """
    destino = msg.get("para_peer")
    if destino:
        # Enviar a TODOS los peers con ese id: un registro viejo (conexión
        # caída aún no detectada) no debe tapar al peer vivo.
        entregado = False
        for ws, info in peers_de_equipo(equipo_id):
            if info.get("peer_id") == destino and ws is not excepto:
                if await _enviar_peer(ws, msg):
                    entregado = True
        return entregado
    candidatos = [ws for ws, _info in peers_de_equipo(equipo_id) if ws is not excepto]
    if not candidatos:
        return False
    idx = _FETCH_RR.get(equipo_id, 0) % len(candidatos)
    _FETCH_RR[equipo_id] = idx + 1
    return await _enviar_peer(candidatos[idx], msg)


async def difundir_equipo_update(equipo_id: str, miembros: list[dict]) -> int:
    """Avisa a los peers de un equipo que cambió su membresía."""
    return await reenviar_a_peers(
        {"tipo": "sync_equipo_update", "equipo_id": equipo_id, "miembros": miembros},
        equipo_id, excepto=None)


async def difundir_aprobacion_update(equipo_id: str, fingerprint: str,
                                     estado: str, aprobaciones: list,
                                     resumen: str = "") -> None:
    """Avisa a los peers y a los frontends locales que cambió el estado
    de aprobación de una versión (para que la UI muestre
    "Esperando al otro" / "Ambos aprobaron → terminando" / "Terminado")."""
    msg = {"tipo": "aprobacion_update", "equipo_id": equipo_id,
           "fingerprint": fingerprint, "estado": estado,
           "aprobaciones": aprobaciones, "resumen": resumen}
    await reenviar_a_peers(msg, equipo_id, excepto=None)
    try:
        await enviar_todos(msg)
    except Exception:
        pass


def validar_peer_join(token: str, equipo_id=None, pairing_code=None,
                      es_local_conn: bool = False, public_mode: bool = False) -> tuple[bool, str]:
    """¿Puede este token registrarse como peer de ese equipo?

    - Token de dueño → cualquier equipo.
    - guest_token de sync → SOLO su equipo. Un token de OTRO equipo se
      rechaza aunque el modo público esté activo.
    - Sin token → solo conexiones locales (o modo público, como antes).
    - Token desconocido → solo modo público (no se distingue del dueño);
      en modo privado se rechaza (el handshake /ws ya lo habría filtrado).
    - Sin equipo_id → se rechaza (el modo legado de pairing global
      está retirado: todo peer pertenece a un equipo de proyecto).
    """
    if equipo_id is None:
        return False, "equipo_id requerido"

    equipo = equipo_por_id(equipo_id)
    if equipo is None:
        return False, "equipo inexistente"
    if not token:
        if es_local_conn or public_mode:
            return True, ""
        return False, "sin token"
    import hmac as _hmac
    if _hmac.compare_digest(str(token), str(config.TOKEN or "")):
        return True, ""
    equipo_token, equipo_token_id = equipo_de_token(token)
    if equipo_token is not None:
        if equipo_token_id == equipo_id:
            return True, ""
        return False, "el token de invitado pertenece a otro equipo"
    if public_mode:
        return True, ""
    return False, "token no autorizado para este equipo"

# ------------------------------------------------------------------ cliente peer
_ID_LOCAL = secrets.token_hex(6)


def _nuevo_sync_id() -> str:
    return f"{_ID_LOCAL}-{int(time.time() * 1000)}-{secrets.token_hex(4)}"


def _entrada_local(proyecto: str, ruta: str) -> dict | None:
    """Entrada {version, hash, binario, grande} de UN archivo local."""
    try:
        sala = sala_de(proyecto)
        p = ruta_segura(sala, ruta)
    except ValueError:
        return None
    if not p.is_file():
        return None
    version = _version_archivo(proyecto, ruta)
    try:
        size = p.stat().st_size
    except OSError:
        return None
    if size > MAX_SYNC_ARBOL_BYTES:
        return {"version": version, "hash": None, "grande": True}
    try:
        datos = p.read_bytes()
    except OSError:
        return None
    try:
        datos.decode("utf-8")
        return {"version": version, "hash": _hash_bytes(datos)}
    except UnicodeDecodeError:
        return {"version": version, "hash": _hash_bytes(datos), "binario": True}


def _decidir_fetch(local: dict | None, remoto: dict | None) -> str:
    """¿Qué hacer con una entrada remota? "fetch" | "nada" | "binario".

    - remoto ausente → nada.
    - remoto binario/grande y voy por detrás/divergente → "binario"
      (no se puede traer contenido; se reporta como no soportado).
    - falta local, remoto más nuevo, o misma versión con hash distinto
      (divergente) → "fetch".
    """
    if not remoto:
        return "nada"
    if remoto.get("binario") or remoto.get("grande"):
        if local is None:
            return "binario"
        if remoto.get("version", 0) > local.get("version", 0):
            return "binario"
        if (remoto.get("version", 0) == local.get("version", 0)
                and remoto.get("hash") is not None
                and local.get("hash") is not None
                and remoto.get("hash") != local.get("hash")):
            return "binario"
        return "nada"
    if local is None:
        return "fetch"
    if remoto.get("version", 0) > local.get("version", 0):
        return "fetch"
    if (remoto.get("version", 0) == local.get("version", 0)
            and remoto.get("hash") is not None
            and local.get("hash") is not None
            and remoto.get("hash") != local.get("hash")):
        return "fetch"
    return "nada"


class PeerClient:
    """Cliente peer: conecta ESTE servidor al relay por /ws, para UN
    proyecto (carpeta) y SU equipo.

    Envía cambios locales (outbox persistente entre reconexiones) y aplica
    los remotos. Nunca deja caer el proceso: reconecta con backoff.
    """

    def __init__(self, hub_url: str, token: str, proyecto: str,
                 peer_id: str, autor: str, carpeta: str = "",
                 equipo_id: str = ""):
        self.hub_url = (hub_url or "").rstrip("/")
        self.token = token or ""
        self.proyecto = proyecto or "general"
        self.peer_id = peer_id or f"peer-{_ID_LOCAL}"
        self.autor = autor or "peer"
        self.carpeta = carpeta or ""
        self.equipo_id = equipo_id or ""
        self.miembros: list[dict] = []
        self._ws = None
        self._outbox: deque = deque(maxlen=1000)
        self._outbox_event = asyncio.Event()
        self._descartados = 0
        self._pendientes: dict[str, dict] = {}
        self._conectado = asyncio.Event()
        self._corriendo = False
        self._tarea = None
        # Transferencias de archivos grandes en vivo: transfer_id ->
        # {ruta, direccion, estado, total_bytes, ts, motivo}. Solo metadato.
        self._transferencias: dict[str, dict] = {}

    # -- ciclo de vida -------------------------------------------------
    async def iniciar(self) -> None:
        if self._corriendo:
            return
        self._corriendo = True
        self._tarea = asyncio.create_task(self._bucle())

    async def detener(self) -> None:
        self._corriendo = False
        if self._tarea:
            self._tarea.cancel()
            try:
                await self._tarea
            except asyncio.CancelledError:
                pass
            self._tarea = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    def encolar(self, msg: dict) -> None:
        # Si ya hay un file_big en cola para la misma ruta, el viejo queda
        # obsoleto: la versión más nueva lo reemplaza (evita mandar bytes
        # viejos y saturar la cola con archivos grandes).
        if msg.get("tipo") == "file_big":
            for m in list(self._outbox):
                if m.get("tipo") == "file_big" and m.get("ruta") == msg.get("ruta"):
                    self._outbox.remove(m)
                    tid = m.get("transfer_id")
                    if tid in self._transferencias:
                        self._transferencias[tid]["estado"] = "descartado"
                        self._transferencias[tid]["motivo"] = \
                            "reemplazado por versión más nueva"
                        self._transferencias[tid]["ts"] = time.time()
        if len(self._outbox) >= self._outbox.maxlen:
            self._descartados += 1
        self._outbox.append(msg)
        self._outbox_event.set()

    # -- conexión -------------------------------------------------------
    async def _bucle(self) -> None:
        backoff = 1.0
        while self._corriendo:
            try:
                await self._sesion()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if not self._corriendo:
                break
            await asyncio.sleep(min(backoff, 30.0))
            backoff = min(backoff * 2, 30.0)

    async def _sesion(self) -> None:
        try:
            import websockets
        except ImportError:
            await asyncio.sleep(30)
            return
        url = (f"{self.hub_url}/ws?token={urllib.parse.quote(self.token)}"
               f"&nombre={urllib.parse.quote(self.autor)}")
        # max_size 32 MB: un file_big de 10 MB viaja como ~13.4 MB en
        # base64+JSON en un solo mensaje; el límite anterior (8 MB) lo
        # habría rechazado y tumbado la conexión al recibirlo.
        ws = await websockets.connect(url, max_size=32 * 1024 * 1024)
        self._ws = ws
        try:
            await ws.send(json.dumps({
                "tipo": "peer_join", "proyecto": self.proyecto,
                "carpeta": self.carpeta, "equipo_id": self.equipo_id,
                "peer_id": self.peer_id, "autor": self.autor,
            }, ensure_ascii=False))
            sender = asyncio.create_task(self._sender())
            retry = asyncio.create_task(self._reintentos())
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    await self._despachar(msg)
            finally:
                sender.cancel()
                retry.cancel()
                for t in (sender, retry):
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
        finally:
            self._ws = None
            self._conectado.clear()

    async def _sender(self) -> None:
        while self._corriendo:
            await self._outbox_event.wait()
            while self._outbox:
                if self._ws is None:
                    break
                msg = self._outbox[0]
                try:
                    await self._ws.send(json.dumps(msg, ensure_ascii=False))
                except Exception:
                    break  # conexión caída: queda en cola para el reconnect
                tid = msg.get("transfer_id")
                if msg.get("tipo") == "file_big" and tid in self._transferencias:
                    # Progreso real: el mensaje completo salió al relay.
                    self._transferencias[tid]["estado"] = "enviado"
                    self._transferencias[tid]["ts"] = time.time()
                    await enviar_todos({
                        "tipo": "sync_big_progreso", "proyecto": self.proyecto,
                        "carpeta": self.carpeta,
                        "ruta": self._transferencias[tid].get("ruta", ""),
                        "direccion": "subida", "estado": "enviado",
                        "total_bytes": self._transferencias[tid].get("total_bytes", 0),
                        "motivo": "",
                    })
                self._outbox.popleft()
            if not self._outbox:
                self._outbox_event.clear()
            await asyncio.sleep(0.05)

    async def _reintentos(self) -> None:
        while self._corriendo:
            await asyncio.sleep(2)
            ahora = time.time()
            for fid, info in list(self._pendientes.items()):
                if ahora - info.get("ultimo", 0) < 2:
                    continue
                if info.get("intentos", 0) >= 3:
                    self._pendientes.pop(fid, None)
                    await enviar_todos({
                        "tipo": "sync_unsupported", "proyecto": self.proyecto,
                        "carpeta": self.carpeta,
                        "ruta": info.get("ruta", ""), "motivo": "sin_respuesta",
                    })
                    continue
                entrada = _entrada_local(self.proyecto, info.get("ruta", ""))
                info["intentos"] = info.get("intentos", 0) + 1
                info["ultimo"] = ahora
                info["version"] = (entrada or {}).get("version", 0)
                info["hash"] = (entrada or {}).get("hash")
                await self._enviar({
                    "tipo": "sync_fetch", "proyecto": self.proyecto,
                    "carpeta": self.carpeta, "equipo_id": self.equipo_id,
                    "peer_id": self.peer_id, "para_peer": info.get("para_peer"),
                    "ruta": info.get("ruta", ""), "version": info["version"],
                    "hash": info["hash"], "fetch_id": fid,
                })

    async def _enviar(self, msg: dict) -> bool:
        if self._ws is None:
            return False
        try:
            await self._ws.send(json.dumps(msg, ensure_ascii=False))
            return True
        except Exception:
            return False

    # -- despacho de mensajes del relay --------------------------------
    async def _despachar(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        tipo = msg.get("tipo")
        if tipo == "peer_ok":
            self._conectado.set()
            if isinstance(msg.get("miembros"), list):
                self.miembros = msg["miembros"]
                _actualizar_miembros_sesion(self.proyecto, self.carpeta, self.miembros)
            await self._enviar({
                "tipo": "sync_request", "proyecto": self.proyecto,
                "carpeta": self.carpeta, "equipo_id": self.equipo_id,
                "peer_id": self.peer_id,
                "arbol": construir_arbol_sync(self.proyecto, self.carpeta),
            })
            return
        if tipo == "sync_equipo_update":
            if msg.get("equipo_id") != self.equipo_id:
                return
            if isinstance(msg.get("miembros"), list):
                self.miembros = msg["miembros"]
                _actualizar_miembros_sesion(self.proyecto, self.carpeta, self.miembros)
                # Reenviar a los frontends de ESTA laptop para que la
                # lista de miembros se actualice sin recargar.
                await enviar_todos({
                    "tipo": "sync_equipo_update", "equipo_id": self.equipo_id,
                    "proyecto": self.proyecto, "carpeta": self.carpeta,
                    "miembros": self.miembros,
                })
            return
        if tipo == "file_sync":
            if msg.get("proyecto") != self.proyecto:
                return
            if msg.get("carpeta") != self.carpeta:
                return
            if msg.get("equipo_id") != self.equipo_id:
                return
            fid = msg.get("fetch_id") or ""
            if fid:
                if fid not in self._pendientes:
                    return  # respuesta para otro peer
                self._pendientes.pop(fid, None)
            await aplicar_file_sync(self.proyecto, msg)
            return
        if tipo == "file_big":
            # Archivo grande/binario COMPLETO en un solo mensaje: solo del
            # equipo de esta sesión. Se verifica el sha256 ANTES de escribir.
            if msg.get("equipo_id") != self.equipo_id:
                return
            if msg.get("proyecto") != self.proyecto:
                return
            if msg.get("carpeta") != self.carpeta:
                return
            fid = msg.get("fetch_id") or ""
            if fid:
                if fid not in self._pendientes:
                    return  # respuesta para otro peer
                self._pendientes.pop(fid, None)
            tid = str(msg.get("transfer_id") or "")
            if tid:
                self._transferencias[tid] = {
                    "ruta": msg.get("ruta", ""), "direccion": "bajada",
                    "estado": "recibiendo",
                    "total_bytes": msg.get("total_bytes", 0),
                    "ts": time.time(), "motivo": "",
                }
                _podar_transferencias(self)
                await _emitir_big_progreso(self, tid, "verificando")
            res = await aplicar_file_big(self.proyecto, msg)
            if tid and tid in self._transferencias:
                if res.get("aplicado") or res.get("conflicto"):
                    # Conflicto no es error: los bytes se verificaron y se
                    # guardaron como backup sin tocar el archivo local.
                    await _emitir_big_progreso(
                        self, tid, "escrito",
                        "conflicto (backup)" if res.get("conflicto") else "")
                elif res.get("motivo") in ("duplicado", "sin_cambios"):
                    await _emitir_big_progreso(self, tid, "escrito",
                                              str(res.get("motivo")))
                else:
                    await _emitir_big_progreso(
                        self, tid, "error", str(res.get("motivo") or "error"))
            elif not res.get("aplicado") and res.get("motivo") not in (
                    "duplicado", "sin_cambios"):
                # Sin transfer_id (no debería pasar): igual avisar el error.
                await enviar_todos({
                    "tipo": "sync_big_progreso", "proyecto": self.proyecto,
                    "carpeta": self.carpeta, "ruta": msg.get("ruta", ""),
                    "direccion": "bajada", "estado": "error",
                    "total_bytes": msg.get("total_bytes", 0),
                    "motivo": str(res.get("motivo") or "error"),
                })
            return
        if tipo == "sync_state":
            if msg.get("equipo_id") != self.equipo_id:
                return
            await self._manejar_sync_state(msg)
            return
        if tipo == "sync_request":
            if msg.get("equipo_id") != self.equipo_id:
                return
            await self._manejar_sync_request(msg)
            return
        if tipo == "sync_fetch":
            if msg.get("equipo_id") != self.equipo_id:
                return
            await self._manejar_sync_fetch(msg)
            return
        if tipo == "agent_msg":
            # Mensaje del bus de agentes de este equipo: lo aplica el
            # agente de ESTA laptop (queda en su bus local, con la
            # identidad del agente que habla).
            if msg.get("equipo_id") != self.equipo_id:
                return
            if not _recordar_sync_id(str(msg.get("sync_id") or "")):
                return  # ya procesado (loop)
            mensaje = msg.get("mensaje") or {}
            await self._aplicar_agent_msg(mensaje, msg.get("agente") or {})
            return
        if tipo == "aprobacion_update":
            # Estado de aprobación de una versión: llega a los
            # frontends de ESTA laptop para actualizar la UI en vivo.
            if msg.get("equipo_id") != self.equipo_id:
                return
            await enviar_todos({
                "tipo": "aprobacion_update", "equipo_id": self.equipo_id,
                "proyecto": self.proyecto, "carpeta": self.carpeta,
                "fingerprint": msg.get("fingerprint"),
                "estado": msg.get("estado"),
                "aprobaciones": msg.get("aprobaciones") or [],
                "resumen": msg.get("resumen", ""),
            })
            return
        # peer_ok/error/otros: ignorar (el relay no manda nada más a peers)

    async def _aplicar_agent_msg(self, mensaje: dict, agente: dict) -> None:
        """Un mensaje del bus del equipo llegó por el relay: queda en el
        bus local de ESTA laptop (con equipo_id + identidad del agente)
        para que su agente lo recoja en su bandeja."""
        try:
            msg = _bus_post(
                de=str(mensaje.get("de", "agente")),
                para=str(mensaje.get("para", "todos")),
                texto=str(mensaje.get("texto", "")),
                tipo=str(mensaje.get("tipo", "mensaje")),
                task_ref=mensaje.get("task_ref"),
                proyecto=self.proyecto,
                respuesta_a=mensaje.get("respuesta_a"),
                equipo_id=self.equipo_id,
                agente=agente or None,
                extra={k: v for k, v in mensaje.items()
                       if k in ("tarea_id", "fingerprint", "notas", "carpeta")},
            )
            # El mensaje local conserva su propio id; la deduplicación
            # entre servidores la hace sync_id (vía _recordar_sync_id).
            await _bus_difundir(msg)
        except Exception as e:
            print(f"[Sync] Aviso: no se pudo aplicar agent_msg: {e}")

    def estado(self) -> dict:
        """Estado REAL de sync de esta sesión (para el indicador de la
        UI): conectado según el socket, pendientes según el outbox.
        Incluye las transferencias de archivos grandes en vivo."""
        conectado = bool(self._conectado.is_set() and self._ws is not None)
        pendientes = len(self._outbox)
        if not conectado:
            estado = "sin_conexion"
        elif pendientes:
            estado = "pendiente"
        else:
            estado = "sincronizado"
        ahora = time.time()
        transferencias = []
        for tid, t in list(self._transferencias.items()):
            if t.get("estado") in ("escrito", "error", "descartado", "enviado") \
                    and ahora - t.get("ts", 0) > 300:
                del self._transferencias[tid]
                continue
            transferencias.append({
                "id": tid, "ruta": t.get("ruta", ""),
                "direccion": t.get("direccion", ""),
                "estado": t.get("estado", ""),
                "total_bytes": t.get("total_bytes", 0),
                "motivo": t.get("motivo", ""),
            })
        transferencias.sort(key=lambda t: t["ruta"])
        return {"estado": estado, "pendientes": pendientes,
                "conectado": conectado, "transferencias": transferencias}

    async def _manejar_sync_state(self, msg: dict) -> None:
        remoto_peer = msg.get("peer_id")
        mi_arbol = construir_arbol_sync(self.proyecto, self.carpeta)
        for ruta, entrada_remota in (msg.get("arbol") or {}).items():
            decision = _decidir_fetch(mi_arbol.get(ruta), entrada_remota)
            if decision == "fetch":
                await self._pedir_fetch(ruta, mi_arbol.get(ruta), remoto_peer)
            elif decision == "binario":
                # El peer lo tiene grande/binario: pedirlo; si cabe en el
                # cap llega como file_big completo, si no el peer responde
                # con la bandera y se reporta como no soportado (como antes).
                await self._pedir_fetch(ruta, mi_arbol.get(ruta), remoto_peer)

    async def _manejar_sync_request(self, msg: dict) -> None:
        # 1. Responder con mi árbol (solo mi carpeta).
        mi_arbol = construir_arbol_sync(self.proyecto, self.carpeta)
        await self._enviar({
            "tipo": "sync_state", "proyecto": self.proyecto,
            "carpeta": self.carpeta, "equipo_id": self.equipo_id,
            "peer_id": self.peer_id, "arbol": mi_arbol,
        })
        # 2. Si el que pide va por delante (o divergió), pedirle a ÉL.
        for ruta, entrada_remota in (msg.get("arbol") or {}).items():
            decision = _decidir_fetch(mi_arbol.get(ruta), entrada_remota)
            if decision == "fetch":
                await self._pedir_fetch(ruta, mi_arbol.get(ruta), msg.get("peer_id"))
            elif decision == "binario":
                await self._pedir_fetch(ruta, mi_arbol.get(ruta), msg.get("peer_id"))

    async def _pedir_fetch(self, ruta: str, mi_entrada, para_peer) -> None:
        fetch_id = secrets.token_hex(8)
        self._pendientes[fetch_id] = {
            "ruta": ruta, "intentos": 1, "ultimo": time.time(),
            "para_peer": para_peer,
        }
        await self._enviar({
            "tipo": "sync_fetch", "proyecto": self.proyecto,
            "carpeta": self.carpeta, "equipo_id": self.equipo_id,
            "peer_id": self.peer_id, "para_peer": para_peer,
            "ruta": ruta,
            "version": (mi_entrada or {}).get("version", 0),
            "hash": (mi_entrada or {}).get("hash"),
            "fetch_id": fetch_id,
        })

    async def _manejar_sync_fetch(self, msg: dict) -> None:
        if msg.get("para_peer") and msg.get("para_peer") != self.peer_id:
            return
        ruta = msg.get("ruta", "")
        req_version = msg.get("version", 0)
        req_hash = msg.get("hash")
        entrada = _entrada_local(self.proyecto, ruta)
        if entrada is None:
            return
        mi_version = entrada.get("version", 0)
        mi_hash = entrada.get("hash")
        if mi_version < req_version:
            return
        es_grande = bool(entrada.get("binario") or entrada.get("grande"))
        if mi_version == req_version and mi_hash == req_hash and not es_grande:
            # Para archivos grandes/binarios el hash del árbol es None en
            # ambos lados, así que esta igualdad no significa "ya lo tiene":
            # un fetch explícito siempre se responde (el receptor solo lo
            # pide si vio que le falta o va por detrás). Sin esto, un
            # binario sin versión (el caso normal: se copió a la carpeta)
            # jamás se sincronizaría en el catch-up.
            return
        divergente = (mi_version == req_version and mi_hash != req_hash)
        base = {
            "tipo": "file_sync", "proyecto": self.proyecto,
            "carpeta": self.carpeta, "equipo_id": self.equipo_id,
            "ruta": ruta, "operacion": "upsert", "base_version": req_version,
            "version": mi_version, "autor": self.autor,
            "sync_id": _nuevo_sync_id(), "fetch_id": msg.get("fetch_id"),
            "divergente": divergente, "hash": mi_hash,
        }
        if es_grande:
            # Catch-up de archivo grande/binario: transferencia COMPLETA en
            # vivo si cabe en el cap; si no, la bandera de siempre (el
            # receptor lo reporta como no soportado, como antes).
            datos = _leer_bytes_big(self.proyecto, ruta)
            if datos is not None:
                big = _construir_file_big(
                    self.proyecto, self.carpeta, self.equipo_id, ruta,
                    req_version, mi_version, self.autor, datos,
                    fetch_id=msg.get("fetch_id"), divergente=divergente)
                if big is not None:
                    tid = big["transfer_id"]
                    _recordar_sync_id(big["sync_id"])
                    self._transferencias[tid] = {
                        "ruta": ruta, "direccion": "subida", "estado": "en_cola",
                        "total_bytes": len(datos), "ts": time.time(), "motivo": "",
                    }
                    _podar_transferencias(self)
                    ok_envio = await self._enviar(big)
                    await _emitir_big_progreso(
                        self, tid, "enviado" if ok_envio else "error",
                        "" if ok_envio else "no se pudo enviar")
                    return
            # Sobre el cap (o ilegible): la bandera de siempre.
            flag = "grande" if entrada.get("grande") and not entrada.get("binario") else "binario"
            await self._enviar({**base, "contenido": None, **{flag: True}})
            return
        try:
            sala = sala_de(self.proyecto)
            contenido = ruta_segura(sala, ruta).read_text(encoding="utf-8")
        except (ValueError, OSError, UnicodeDecodeError):
            return
        if len(contenido.encode("utf-8")) > MAX_SYNC_CONTENIDO:
            await self._enviar({**base, "contenido": None, "grande": True})
            return
        await self._enviar({**base, "contenido": contenido})


# ------------------------------------------------------------------ sesiones por proyecto
# Registro de sesiones activas de ESTE servidor: (proyecto, carpeta) -> PeerClient.
# Persistente en JSON bajo ~/.atlantis/ ("sync_sesiones"): al reiniciar
# el servidor, vuelve a unirse a sus equipos. Nunca en el zip (es estado
# local). Ver el comentario en el registro de equipos: el HubStore no
# persistiría esto (overlay en memoria).
_SESIONES: dict[tuple[str, str], PeerClient] = {}


def _cargar_sesiones() -> list:
    datos = _cargar_registro("sync_sesiones", [])
    return datos if isinstance(datos, list) else []


def _guardar_sesiones(sesiones: list) -> None:
    _guardar_registro("sync_sesiones", sesiones)


def _buscar_sesion_guardada(proyecto: str, carpeta: str) -> dict | None:
    for s in _cargar_sesiones():
        if s.get("proyecto") == proyecto and s.get("carpeta") == carpeta:
            return s
    return None


def registrar_sesion(proyecto: str, carpeta: str, equipo_id: str, hub_url: str,
                     token: str, peer_id: str, autor: str, rol: str,
                     miembros: list | None = None, agent_name: str = "") -> None:
    """Guarda (o actualiza) una sesión en el registro persistente."""
    sesiones = _cargar_sesiones()
    guardada = None
    for s in sesiones:
        if s.get("proyecto") == proyecto and s.get("carpeta") == carpeta:
            guardada = s
            break
    datos = {"proyecto": proyecto, "carpeta": carpeta, "equipo_id": equipo_id,
             "hub_url": hub_url, "token": token, "peer_id": peer_id,
             "autor": autor, "rol": rol, "agent_name": agent_name,
             "miembros": miembros if miembros is not None
             else (guardada or {}).get("miembros", [])}
    if guardada is not None:
        guardada.update(datos)
    else:
        sesiones.append(datos)
    _guardar_sesiones(sesiones)


def olvidar_sesion(proyecto: str, carpeta: str) -> None:
    sesiones = _cargar_sesiones()
    sesiones = [s for s in sesiones
                if not (s.get("proyecto") == proyecto and s.get("carpeta") == carpeta)]
    _guardar_sesiones(sesiones)


def _actualizar_miembros_sesion(proyecto: str, carpeta: str, miembros: list) -> None:
    sesiones = _cargar_sesiones()
    cambiado = False
    for s in sesiones:
        if s.get("proyecto") == proyecto and s.get("carpeta") == carpeta:
            s["miembros"] = miembros
            cambiado = True
    if cambiado:
        _guardar_sesiones(sesiones)


def sesiones_activas() -> list[dict]:
    """Sesiones persistentes (sin tokens) para el frontend. Cada sesión
    lleva su estado REAL de sync (conectado / pendientes / estado) y
    ``yo`` (el usuario de ESTA laptop en ese equipo) para saber qué
    agente es el propio (renombre solo del dueño)."""
    out = []
    for s in _cargar_sesiones():
        copia = {k: v for k, v in s.items() if k != "token"}
        copia["yo"] = {"user_name": s.get("autor", "")}
        cliente = _SESIONES.get((s.get("proyecto"), s.get("carpeta")))
        if cliente is not None:
            copia["sync_estado"] = cliente.estado()
        else:
            copia["sync_estado"] = {"estado": "sin_conexion",
                                    "pendientes": 0, "conectado": False}
        out.append(copia)
    return out


def huella_proyecto(proyecto: str, carpeta: str) -> str:
    """Huella de la versión actual del proyecto (árbol de archivos con
    hashes). Dos laptops con la misma versión calculan la misma huella:
    así la aprobación dual solo dispara si AMBOS aprobaron LA MISMA
    versión."""
    arbol = construir_arbol_sync(proyecto, carpeta)
    canon = json.dumps(arbol, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


async def publicar_en_bus_equipo(proyecto: str, carpeta: str, equipo_id: str,
                                 de: str, para: str, texto: str,
                                 tipo: str = "mensaje",
                                 agente: dict | None = None,
                                 extra: dict | None = None,
                                 task_ref: str | None = None,
                                 respuesta_a: str | None = None,
                                 estado: str | None = None) -> dict | None:
    """Publica un mensaje en el bus de ESTA laptop y lo retransmite al
    equipo (relay) para que llegue al bus de la otra laptop. El bus
    queda con alcance al equipo del proyecto: los agentes del proyecto
    X no ven ni actúan en el proyecto Y."""
    msg = _bus_post(
        de=de, para=para, texto=texto, tipo=tipo, proyecto=proyecto,
        equipo_id=equipo_id, agente=agente, extra=extra, task_ref=task_ref,
        respuesta_a=respuesta_a,
    )
    if estado is not None:
        msg["estado"] = estado
    try:
        await _bus_difundir(msg)
    except Exception:
        pass
    cliente = _SESIONES.get((proyecto, carpeta))
    if cliente is not None and cliente.equipo_id == equipo_id:
        # Retransmisión al peer: best-effort. El mensaje YA quedó
        # persistido localmente; un fallo del relay no convierte el
        # post en error (el peer lo recuperará al reconectar).
        try:
            await cliente._enviar({
                "tipo": "agent_msg", "proyecto": proyecto, "carpeta": carpeta,
                "equipo_id": equipo_id,
                "sync_id": "bus-" + secrets.token_hex(8),
                "mensaje": msg, "agente": agente or {},
            })
        except Exception as e:
            print(f"[Sync] Aviso: no se pudo retransmitir agent_msg al peer: {e}")
    return msg


def sesion_para(proyecto: str, carpeta: str) -> PeerClient | None:
    return _SESIONES.get((proyecto, carpeta))


def iniciar_sesion(proyecto: str, carpeta: str, equipo_id: str, hub_url: str,
                   token: str, peer_id: str, autor: str) -> PeerClient:
    """Arranca (o reutiliza) la sesión peer de un proyecto. Idempotente."""
    clave = (proyecto, carpeta)
    existente = _SESIONES.get(clave)
    if existente is not None and existente.equipo_id == equipo_id:
        return existente
    if existente is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(existente.detener())
        except RuntimeError:
            pass
        _SESIONES.pop(clave, None)
    cliente = PeerClient(hub_url, token, proyecto, peer_id, autor,
                         carpeta=carpeta, equipo_id=equipo_id)
    _SESIONES[clave] = cliente
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cliente.iniciar())
    except RuntimeError:
        pass
    return cliente


async def detener_sesion(proyecto: str, carpeta: str) -> None:
    cliente = _SESIONES.pop((proyecto, carpeta), None)
    if cliente is not None:
        await cliente.detener()


def iniciar_sesiones_persistidas() -> list[PeerClient]:
    """Re-une este servidor a todos sus equipos guardados (arranque).

    No-op si no hay sesiones guardadas (proyectos en solitario).
    """
    out = []
    for s in list(_cargar_sesiones()):
        try:
            out.append(iniciar_sesion(
                s.get("proyecto", "general"), s.get("carpeta", ""),
                s.get("equipo_id", ""), s.get("hub_url", ""),
                s.get("token", ""), s.get("peer_id", "") or f"peer-{_ID_LOCAL}",
                s.get("autor", "peer")))
        except Exception:
            pass
    return out


def iniciar_cliente_peer_si_configurado():
    """Compatibilidad: el arranque legado con SYNC_* ya no crea sesiones.

    Las sesiones por proyecto se crean al compartir/unirse a un proyecto
    y persisten en el registro local; aquí solo se reanudan las guardadas.
    """
    return iniciar_sesiones_persistidas()


def configurar_cliente_peer(hub_url: str, token: str, proyecto: str,
                            peer_id: str, autor: str, carpeta: str = "",
                            equipo_id: str = "") -> PeerClient:
    """Compatibilidad para tests: crea un PeerClient suelto (sin sesión)."""
    return PeerClient(hub_url, token, proyecto, peer_id, autor,
                      carpeta=carpeta, equipo_id=equipo_id)


def _podar_transferencias(sesion) -> None:
    """Poda el registro de transferencias: terminadas de hace >5 min fuera,
    y nunca más de 30 entradas. Solo metadato (ruta/estado), jamás contenido."""
    ahora = time.time()
    trans = sesion._transferencias
    for tid in [t for t, d in trans.items()
                if d.get("estado") in ("escrito", "error", "descartado", "enviado")
                and ahora - d.get("ts", 0) > 300]:
        del trans[tid]
    while len(trans) > 30:
        vieja = min(trans, key=lambda t: trans[t].get("ts", 0))
        del trans[vieja]


async def _emitir_big_progreso(sesion, transfer_id: str, estado: str,
                              motivo: str = "") -> None:
    """Actualiza el estado de una transferencia y lo anuncia a los
    frontends locales (progreso en vivo)."""
    t = sesion._transferencias.get(transfer_id)
    if t is None:
        return
    t["estado"] = estado
    t["ts"] = time.time()
    if motivo:
        t["motivo"] = motivo
    _podar_transferencias(sesion)
    await enviar_todos({
        "tipo": "sync_big_progreso", "proyecto": sesion.proyecto,
        "carpeta": sesion.carpeta, "ruta": t.get("ruta", ""),
        "direccion": t.get("direccion", ""), "estado": estado,
        "total_bytes": t.get("total_bytes", 0), "motivo": t.get("motivo", ""),
    })


def _construir_file_big(proyecto: str, carpeta: str, equipo_id: str, ruta: str,
                        base_version: int, version: int, autor: str,
                        datos: bytes, fetch_id: str | None = None,
                        divergente: bool = False) -> dict | None:
    """Construye el mensaje file_big para unos bytes ya leídos. Retorna
    None si exceden el cap (el llamador reporta como no soportado)."""
    if len(datos) > MAX_FILE_BIG:
        return None
    transfer_id = secrets.token_hex(8)
    sync_id = _nuevo_sync_id()
    msg = {
        "tipo": "file_big", "proyecto": proyecto, "carpeta": carpeta,
        "equipo_id": equipo_id, "ruta": ruta, "operacion": "upsert",
        "base_version": int(base_version), "version": int(version),
        "autor": autor or "peer", "sync_id": sync_id,
        "transfer_id": transfer_id,
        "file_sha256": _hash_bytes(datos), "total_bytes": len(datos),
        "datos_b64": base64.b64encode(datos).decode("ascii"),
        "divergente": bool(divergente),
    }
    if fetch_id:
        msg["fetch_id"] = fetch_id
    return msg


def _leer_bytes_big(proyecto: str, ruta: str) -> bytes | None:
    """Lee los bytes de un archivo para file_big. None si no existe, no se
    puede leer o excede el cap (el llamador usa el flujo viejo)."""
    try:
        sala = sala_de(proyecto)
        datos = ruta_segura(sala, ruta).read_bytes()
    except (ValueError, OSError):
        return None
    if len(datos) > MAX_FILE_BIG:
        return None
    return datos


async def notificar_archivo_big(proyecto: str, ruta: str, base_version: int,
                                version: int, autor: str,
                                contenido_texto: str | None = None) -> dict | None:
    """Envía un archivo grande/binario COMPLETO por el sync en vivo: un
    solo mensaje file_big (bytes + sha256) a la cola de la sesión, que lo
    manda por la sesión WS abierta en tiempo real. Sin partir en pedazos,
    sin botón manual, sin job diferido: igual que el sync de archivos
    chicos. Si se interrumpe, la cola lo reenvía completo al reconectar.
    Sobre el cap: no se envía nada a medias (sync_unsupported).
    Retorna el mensaje encolado o None."""
    carpeta = carpeta_de_ruta(ruta)
    if not carpeta:
        return None  # raíz del workspace: sin proyecto, sin sync
    sesion = sesion_para(proyecto, carpeta)
    if sesion is None:
        return None  # proyecto en solitario: sin equipo, sin sync
    try:
        if contenido_texto is not None:
            datos = contenido_texto.encode("utf-8")
        else:
            sala = sala_de(proyecto)
            datos = ruta_segura(sala, ruta).read_bytes()
    except (ValueError, OSError, UnicodeError):
        return None
    if len(datos) > MAX_FILE_BIG:
        await enviar_todos({
            "tipo": "sync_unsupported", "proyecto": proyecto,
            "carpeta": carpeta, "ruta": ruta, "motivo": "demasiado_grande",
        })
        return None
    msg = _construir_file_big(proyecto, carpeta, sesion.equipo_id, ruta,
                              base_version, version, autor, datos)
    if msg is None:  # no debería pasar (ya se midió), pero sin atajos
        await enviar_todos({
            "tipo": "sync_unsupported", "proyecto": proyecto,
            "carpeta": carpeta, "ruta": ruta, "motivo": "demasiado_grande",
        })
        return None
    transfer_id = msg["transfer_id"]
    # Registrar el sync_id ANTES de enviar: el eco de mi propio mensaje
    # se descarta como duplicado en vez de crear un conflicto espurio.
    _recordar_sync_id(msg["sync_id"])
    sesion._transferencias[transfer_id] = {
        "ruta": ruta, "direccion": "subida", "estado": "en_cola",
        "total_bytes": len(datos), "ts": time.time(), "motivo": "",
    }
    _podar_transferencias(sesion)
    sesion.encolar(msg)
    await _emitir_big_progreso(sesion, transfer_id, "en_cola")
    return msg


async def notificar_cambio_local(proyecto: str, ruta: str, base_version: int,
                                 version: int, autor: str,
                                 contenido: str | None = None,
                                 operacion: str = "upsert",
                                 origen: str | None = None,
                                 binario: bool = False) -> dict | None:
    """Cola un cambio local para sync + lo reenvía a los peers de este servidor.

    Enrutado POR CARPETA: la ruta ``X/a.txt`` solo viaja al equipo del
    proyecto ``X``. Rutas de la raíz (sin carpeta) o carpetas sin equipo
    (proyectos en solitario) no se sincronizan. No bloquea: encola y
    retorna. Nunca registra contenido. Los backups ``*.conflict.*``
    jamás se sincronizan.
    """
    if es_archivo_conflicto(ruta):
        return None
    if operacion not in _OPERACIONES:
        return None
    carpeta = carpeta_de_ruta(ruta)
    if not carpeta:
        return None  # raíz del workspace: sin proyecto, sin sync
    sesion = sesion_para(proyecto, carpeta)
    if sesion is None:
        return None  # proyecto en solitario: sin equipo, sin sync

    # Archivos grandes/binarios en upsert: transferencia COMPLETA en vivo
    # (file_big: bytes + sha256 en un solo mensaje), en tiempo real por la
    # sesión abierta. Nada de bandera "binario" sin contenido.
    if operacion == "upsert" and (binario or (
            contenido is not None
            and len(contenido.encode("utf-8")) > MAX_SYNC_CONTENIDO)):
        return await notificar_archivo_big(
            proyecto, ruta, base_version, version, autor,
            contenido_texto=None if binario else contenido)

    msg: dict = {
        "tipo": "file_sync", "proyecto": proyecto, "carpeta": carpeta,
        "equipo_id": sesion.equipo_id, "ruta": ruta,
        "operacion": operacion, "base_version": int(base_version),
        "version": int(version), "autor": autor or "peer",
        "sync_id": _nuevo_sync_id(),
    }
    if operacion == "rename":
        msg["origen"] = origen or ""
    if binario:
        msg["binario"] = True
        msg["contenido"] = None
        msg["hash"] = None
    elif contenido is not None:
        if len(contenido.encode("utf-8")) > MAX_SYNC_CONTENIDO:
            await enviar_todos({
                "tipo": "sync_unsupported", "proyecto": proyecto,
                "carpeta": carpeta, "ruta": ruta, "motivo": "demasiado_grande",
            })
            return None
        msg["contenido"] = contenido
        msg["hash"] = _hash_contenido(contenido)
    else:
        msg["contenido"] = None
        msg["hash"] = None

    # Registrar el sync_id ANTES de enviar: el eco de mi propio mensaje
    # (relay local, doble ruta) se descarta como duplicado en vez de
    # crear un conflicto espurio contra mi propio guardado.
    _recordar_sync_id(msg["sync_id"])
    sesion.encolar(msg)
    if peers_de_equipo(sesion.equipo_id):
        asyncio.create_task(reenviar_a_peers(msg, sesion.equipo_id, excepto=None))
    return msg
