"""Router equipos por proyecto (co-edición en vivo). Build v0.6.0.

Dos caras del mismo sistema:

1. **Relay** (``/api/sync/equipos/*``): el registro de equipos vive en
   el servidor que hace de relay. Solo metadato (códigos, miembros,
   tokens de invitado, aprobaciones); NUNCA contenido de archivos. Un
   proyecto tiene UN equipo; compartir de nuevo la misma carpeta
   reutiliza el equipo y añade un código nuevo (equipo abierto).
2. **Local** (``/api/proyectos/*``): la laptop del usuario comparte un
   proyecto o se une a uno. El backend habla con el relay y arranca la
   sesión peer de ese proyecto.

El flujo real (sin variables ni URLs: pegar solo el código basta):
- Anfitrión: POST /api/proyectos/<carpeta>/compartir → {code, join_link}.
  Le pasa el código (o el enlace de unión) a su amigo.
- Invitado: POST /api/proyectos/unirse {code, carpeta} → entra al
  equipo existente con catch-up de los archivos de ESA carpeta.
- Cada uno puede compartir de nuevo el mismo proyecto en cualquier
  momento: el código nuevo se une al equipo existente.
- El relay por defecto es el hub central (sin configurar nada);
  SYNC_HUB_URL / hub_url solo son overrides avanzados.
"""
from __future__ import annotations

import os
import time
import urllib.parse

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from backend import sync as sync_mod
from backend.core.config import config, es_host, es_local, token_es_invitado, token_ok
from backend.core.state import _bus_difundir, _bus_post

router = APIRouter()

# Rate-limit de unirse por código: 5 fallos por IP/minuto (igual que pairing).
_JOIN_FALLOS: dict[str, list[float]] = {}
_JOIN_FALLOS_VENTANA = 60.0
_JOIN_FALLOS_MAX = 5


def _join_bloqueado(ip: str) -> bool:
    ahora = time.monotonic()
    intentos = [t for t in _JOIN_FALLOS.get(ip, []) if ahora - t < _JOIN_FALLOS_VENTANA]
    _JOIN_FALLOS[ip] = intentos
    return len(intentos) >= _JOIN_FALLOS_MAX


def _registrar_fallo_join(ip: str) -> None:
    _JOIN_FALLOS.setdefault(ip, []).append(time.monotonic())


# Relay por defecto del hub central (ítem 1): unirse/compartir no
# requiere configurar SYNC_HUB_URL ni pegar ninguna URL. Se lee en
# CADA llamada (no a nivel de módulo): si se leyera al importar, un
# test que importe el módulo sin ATLANTIS_RELAY_DEFAULT dejaría el
# relay congelado en el hub real para toda la suite. Los tests ponen
# ATLANTIS_RELAY_DEFAULT=local para usar el relay en proceso.
def _relay_predeterminado() -> str:
    return os.environ.get(
        "ATLANTIS_RELAY_DEFAULT", "https://atlantis-hub.onrender.com")


def _es_dueno_o_local(req: Request, token: str) -> bool:
    """Solo el dueño o una petición local cambian el compartir."""
    return es_local(req) or es_host(req, token)


def _token_req(req: Request, token: str = "") -> str:
    """Token efectivo de la petición: query ``?token=`` o cabecera
    ``Authorization: Bearer …``. Los clientes remotos (el hub) mandan
    el token por cabecera; sin esto, toda llamada al relay remoto
    fallaba con 403 aunque el token fuera correcto."""
    if token:
        return token
    auth = (req.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _relay_http_base(hub_url: str | None) -> str | None:
    """Base HTTP del relay.

    Resolución (ítem 1: sin variables ni URLs en el camino normal):
    1. ``hub_url`` explícito (override avanzado / relay del enlace).
    2. ``SYNC_HUB_URL`` (override avanzado por env).
    3. El hub central por defecto (sin configurar nada).

    SYNC_HUB_URL es ws(s)://…; los endpoints REST usan http(s)://…
    Devuelve None si el relay es ESTE servidor ("local", o una URL
    a 127.0.0.1/localhost con el puerto de este servidor: las sesiones
    guardan la URL ws local y resolverla a HTTP rompería el camino
    en proceso).
    """
    raw = (hub_url or os.environ.get("SYNC_HUB_URL", "")
           or _relay_predeterminado()).strip()
    if not raw or raw.lower() == "local":
        return None
    if raw.startswith("ws://"):
        http_base = "http://" + raw[len("ws://"):]
    elif raw.startswith("wss://"):
        http_base = "https://" + raw[len("wss://"):]
    else:
        http_base = raw
    http_base = http_base.rstrip("/")
    # ¿Es este mismo servidor? Entonces en proceso (None).
    try:
        parsed = urllib.parse.urlparse(http_base)
        host = (parsed.hostname or "").lower()
        puerto = parsed.port or (443 if parsed.scheme == "https" else 80)
        if host in ("127.0.0.1", "localhost", "::1") and puerto == config.PORT:
            return None
    except Exception:
        pass
    return http_base


def _relay_ws_url(hub_url: str | None) -> str:
    """URL ws del relay para la sesión peer. Sin relay explícito ni
    env, el hub central por defecto ("local" → este mismo servidor)."""
    raw = (hub_url or os.environ.get("SYNC_HUB_URL", "")
           or _relay_predeterminado()).strip()
    if not raw or raw.lower() == "local":
        return f"ws://127.0.0.1:{config.PORT}"
    if raw.startswith(("ws://", "wss://")):
        return raw.rstrip("/")
    if raw.startswith(("http://", "https://")):
        return ("ws://" if raw.startswith("http://") else "wss://") + raw.split("://", 1)[1].rstrip("/")
    return raw.rstrip("/")


def _relay_display_url(base: str | None) -> str:
    """URL del relay para mostrar/copiar (la base HTTP, o 'local')."""
    if base is None:
        return "local"
    return base


def _join_link(relay_base: str | None, codigo: str) -> str:
    """Enlace de unión pasteable: lleva el código + el relay. El
    diálogo de unirse acepta el código solo o este enlace."""
    relay = _relay_display_url(relay_base)
    return f"atlantis://unirse?code={codigo}&relay={urllib.parse.quote(relay, safe='')}"


def _parsear_codigo_o_enlace(entrada: str) -> tuple[str, str | None]:
    """Acepta un código (ATL-XXXXXX) o un enlace de unión
    (atlantis://unirse?code=…&relay=… o https://…/unirse?code=…&relay=…).
    Retorna (código, relay_override o None)."""
    texto = str(entrada or "").strip()
    if not texto:
        return "", None
    if "://" in texto or texto.startswith("atlantis:"):
        try:
            parsed = urllib.parse.urlparse(texto)
            qs = urllib.parse.parse_qs(parsed.query)
            codigo = (qs.get("code") or [""])[0].strip().upper()
            relay = (qs.get("relay") or [None])[0]
            if codigo:
                return codigo, (relay.strip() if relay else None)
        except Exception:
            pass
    # Código suelto (con o sin prefijo ATL-)
    return texto.strip().upper(), None


def _token_relay(base: str | None) -> str:
    """Token de dueño para hablar con el relay.

    Relay propio (base None) → el token de ESTE servidor. Relay remoto →
    SYNC_TOKEN (el token de dueño en ese relay), o el propio si no hay.
    """
    if base is None:
        return config.TOKEN
    return os.environ.get("SYNC_TOKEN", "") or config.TOKEN


def _nombre(req_body: dict, clave: str, defecto: str) -> str:
    return str(req_body.get(clave, "") or "").strip() or defecto


# ------------------------------------------------------------------ relay: equipos
@router.post("/api/sync/equipos")
async def api_sync_equipos_crear(req: Request, token: str = Query("")):
    """Crea el equipo de un proyecto, o añade un código nuevo si el
    equipo ya existe (equipo abierto). Solo dueño o local. El código
    expira (por defecto 24 h; ``expira_minutos`` cambia la duración,
    0 = sin expiración)."""
    token = _token_req(req, token)
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "Solo el anfitrión puede compartir un proyecto."},
                            status_code=403)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    carpeta = str(body.get("carpeta", "") or "")
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    host_info = {
        "agent_name": _nombre(body, "agent_name", "Hermes"),
        "user_name": _nombre(body, "user_name", config.CFG.get("nombre", "Daniel")),
    }
    try:
        expira = body.get("expira_minutos")
        expira_minutos = int(expira) if expira is not None else None
        res = sync_mod.crear_equipo_sync(proyecto, carpeta, host_info,
                                         expira_minutos=expira_minutos)
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    await sync_mod.difundir_equipo_update(res["equipo_id"], res["equipo"]["miembros"])
    return {"ok": True, "equipo_id": res["equipo_id"], "code": res["code"],
            "expira_en": res.get("expira_en"),
            "proyecto": proyecto, "carpeta": carpeta,
            "miembros": res["equipo"]["miembros"], "nuevo": res["nuevo"]}


@router.post("/api/sync/equipos/unirse")
async def api_sync_equipos_unirse(req: Request):
    """Unirse a un equipo con su código. El código ES la credencial:
    no se exige token previo. Códigos expirados/revocados → 410 con
    error claro; inválidos → 404."""
    ip = req.client.host if req.client else "desconocida"
    if _join_bloqueado(ip):
        return JSONResponse(
            {"ok": False, "error": "Demasiados intentos fallidos. Espera un minuto."},
            status_code=429)
    try:
        body = await req.json()
    except Exception:
        body = {}
    codigo = str(body.get("code", body.get("pair_code", "")) or "").strip().upper()
    miembro = {
        "agent_name": _nombre(body, "agent_name", "Hermes-Amigo"),
        "user_name": _nombre(body, "user_name", "Amigo"),
    }
    carpeta_esperada = str(body.get("carpeta", "") or "").strip() or None
    try:
        res = sync_mod.unirse_equipo_sync(codigo, miembro, carpeta_esperada)
    except ValueError as e:
        _registrar_fallo_join(ip)
        mensaje = str(e)
        if "revocado" in mensaje or "expirado" in mensaje:
            status = 410
        elif "completo" in mensaje:
            status = 403
        elif "no de" in mensaje or "ya está en uso" in mensaje:
            status = 400
        else:
            status = 404
        return JSONResponse({"ok": False, "error": mensaje}, status_code=status)
    equipo = res["equipo"]
    await sync_mod.difundir_equipo_update(res["equipo_id"], equipo["miembros"])
    msg = _bus_post(
        de="Sistema", para="todos",
        texto=f"⚡ {miembro['agent_name']} se unió al proyecto «{equipo['carpeta']}».",
        tipo="sistema")
    await _bus_difundir(msg)
    return {"ok": True, "equipo_id": res["equipo_id"],
            "guest_token": res["guest_token"],
            "proyecto": equipo["proyecto"], "carpeta": equipo["carpeta"],
            "miembros": equipo["miembros"], "ya_miembro": res["ya_miembro"]}


@router.get("/api/sync/equipos")
async def api_sync_equipos_listar(req: Request, token: str = Query("")):
    """Equipos de este relay (sin tokens). Solo dueño o local."""
    token = _token_req(req, token)
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "Solo el anfitrión puede listar los equipos."},
                            status_code=403)
    return {"ok": True, "equipos": sync_mod.listar_equipos_sync()}


@router.get("/api/sync/equipos/codigos")
async def api_sync_equipos_codigos(req: Request, equipo_id: str = Query(""),
                                   token: str = Query("")):
    """Códigos de un equipo con su estado (vigente/expirado/revocado).
    Solo dueño o local: los códigos son credenciales."""
    token = _token_req(req, token)
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "Solo el anfitrión puede ver los códigos."},
                            status_code=403)
    try:
        codigos = sync_mod.listar_codigos_sync(equipo_id)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return {"ok": True, "equipo_id": equipo_id, "codigos": codigos}


@router.post("/api/sync/equipos/codigos/revocar")
async def api_sync_equipos_codigos_revocar(req: Request, token: str = Query("")):
    """Revoca UN código sin cerrar el equipo ni invalidar los demás.
    Solo dueño o local."""
    token = _token_req(req, token)
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "Solo el anfitrión puede revocar códigos."},
                            status_code=403)
    try:
        body = await req.json()
    except Exception:
        body = {}
    equipo_id = str(body.get("equipo_id", "") or "")
    codigo = str(body.get("code", "") or "")
    if not equipo_id or not codigo:
        return JSONResponse({"error": "equipo_id y code requeridos."}, status_code=400)
    try:
        res = sync_mod.revocar_codigo_sync(equipo_id, codigo)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return res


@router.post("/api/sync/equipos/agente/renombrar")
async def api_sync_equipos_agente_renombrar(req: Request, token: str = Query("")):
    """Renombra el agente DEL QUE LLAMA (solo el suyo): el token manda
    (dueño → su agente anfitrión; invitado → su propio agente). Nadie
    puede renombrar el agente de otro."""
    token = _token_req(req, token)
    try:
        body = await req.json()
    except Exception:
        body = {}
    equipo_id = str(body.get("equipo_id", "") or "")
    nuevo_nombre = str(body.get("nuevo_nombre", "") or "")
    if not equipo_id or not nuevo_nombre:
        return JSONResponse({"error": "equipo_id y nuevo_nombre requeridos."},
                            status_code=400)
    try:
        res = sync_mod.renombrar_agente_sync(equipo_id, token, nuevo_nombre)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
    await sync_mod.difundir_equipo_update(
        equipo_id, sync_mod.equipo_por_id(equipo_id)["miembros"])
    return res


@router.post("/api/sync/equipos/aprobar")
async def api_sync_equipos_aprobar(req: Request, token: str = Query("")):
    """"Me gusta" de un miembro a una versión del proyecto
    (``fingerprint``). Solo cuando anfitrión Y compañero aprueban la
    misma versión, el estado pasa a "terminando" y la tarea de
    terminar el desarrollo se publica en el bus del equipo."""
    token = _token_req(req, token)
    try:
        body = await req.json()
    except Exception:
        body = {}
    equipo_id = str(body.get("equipo_id", "") or "")
    fingerprint = str(body.get("fingerprint", "") or "")
    nota = str(body.get("nota", "") or "")
    if not equipo_id or not fingerprint:
        return JSONResponse({"error": "equipo_id y fingerprint requeridos."},
                            status_code=400)
    try:
        res = await sync_mod.aprobar_proyecto_sync(equipo_id, token, fingerprint, nota)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
    equipo = sync_mod.equipo_por_id(equipo_id) or {}
    await sync_mod.difundir_equipo_update(equipo_id, equipo.get("miembros", []))
    await sync_mod.difundir_aprobacion_update(
        equipo_id, fingerprint, res["estado"], res["aprobaciones"],
        res.get("resumen", ""))
    return res


@router.post("/api/sync/equipos/retirar-aprobacion")
async def api_sync_equipos_retirar_aprobacion(req: Request, token: str = Query("")):
    """Retira la aprobación del que llama (bloquea el disparo si la
    tarea aún no se disparó)."""
    token = _token_req(req, token)
    try:
        body = await req.json()
    except Exception:
        body = {}
    equipo_id = str(body.get("equipo_id", "") or "")
    if not equipo_id:
        return JSONResponse({"error": "equipo_id requerido."}, status_code=400)
    try:
        res = sync_mod.retirar_aprobacion_sync(equipo_id, token)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
    return res


@router.post("/api/sync/equipos/aprobacion/completada")
async def api_sync_equipos_aprobacion_completada(req: Request, token: str = Query("")):
    """Un agente del equipo reporta que terminó el desarrollo de la
    versión aprobada: el estado pasa a "terminado" con su resumen."""
    token = _token_req(req, token)
    try:
        body = await req.json()
    except Exception:
        body = {}
    equipo_id = str(body.get("equipo_id", "") or "")
    tarea_id = str(body.get("tarea_id", "") or "")
    resumen = str(body.get("resumen", "") or "")
    if not equipo_id or not tarea_id:
        return JSONResponse({"error": "equipo_id y tarea_id requeridos."},
                            status_code=400)
    try:
        res = sync_mod.completar_tarea_sync(equipo_id, token, tarea_id, resumen)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    if res.get("ok"):
        await sync_mod.difundir_aprobacion_update(
            equipo_id, res.get("fingerprint", ""), "terminado",
            res.get("aprobaciones", []), res.get("resumen", ""))
    return res


@router.get("/api/sync/equipos/aprobaciones")
async def api_sync_equipos_aprobaciones(req: Request, equipo_id: str = Query(""),
                                        fingerprint: str = Query(""),
                                        token: str = Query("")):
    """Estado de aprobación de UNA versión (fingerprint) del proyecto.
    Solo miembros del equipo (el token manda): un extraño con el
    equipo_id no lee las aprobaciones."""
    token = _token_req(req, token)
    if not equipo_id or not fingerprint:
        return JSONResponse({"error": "equipo_id y fingerprint requeridos."},
                            status_code=400)
    equipo = sync_mod.equipo_por_id(equipo_id)
    if equipo is None:
        return JSONResponse({"ok": False, "error": "Equipo no encontrado."},
                            status_code=404)
    if sync_mod._miembro_por_token(equipo, token) is None:
        return JSONResponse({"ok": False, "error": "No perteneces a este equipo."},
                            status_code=403)
    try:
        res = sync_mod.estado_aprobaciones_sync(equipo_id, fingerprint)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return res


@router.post("/api/sync/equipos/salir")
async def api_sync_equipos_salir(req: Request, token: str = Query("")):
    """Salir de un equipo. Un invitado solo se saca a sí mismo (su
    guest_token manda); el dueño o una petición local sacan a cualquiera.
    Si sale el anfitrión o no queda nadie, el equipo se cierra."""
    token = _token_req(req, token)
    try:
        body = await req.json()
    except Exception:
        body = {}
    equipo_id = str(body.get("equipo_id", "") or "")
    agent_name = str(body.get("agent_name", "") or "")
    user_name = str(body.get("user_name", "") or "")
    if not equipo_id or not agent_name or not user_name:
        return JSONResponse({"error": "equipo_id, agent_name y user_name requeridos."},
                            status_code=400)
    es_dueno = _es_dueno_o_local(req, token) and not token_es_invitado(token)
    # Un invitado es invitado aunque la petición llegue por socket local
    # (el token manda, no el origen): solo puede sacarse a sí mismo.
    try:
        res = sync_mod.salir_equipo_sync(equipo_id, token, agent_name, user_name,
                                         es_dueno=es_dueno)
    except ValueError as e:
        mensaje = str(e)
        status = 403 if "Solo puedes salir" in mensaje else 404
        return JSONResponse({"ok": False, "error": mensaje}, status_code=status)
    if not res["cerrado"]:
        await sync_mod.difundir_equipo_update(equipo_id, res["equipo"]["miembros"])
    msg = _bus_post(de="Sistema", para="todos",
                    texto=f"{agent_name} salió del proyecto.",
                    tipo="sistema")
    await _bus_difundir(msg)
    return {"ok": True, "cerrado": res["cerrado"], "equipo": res["equipo"]}


# ------------------------------------------------------------------ local: compartir / unirse
async def _relay_llamar(base: str | None, metodo: str, ruta: str,
                        payload: dict, token: str = "") -> dict:
    """Llama a un endpoint del relay (en proceso si es este servidor)."""
    if base is None:
        if ruta == "/api/sync/equipos" and metodo == "POST":
            res = sync_mod.crear_equipo_sync(
                payload.get("proyecto", "general"), payload.get("carpeta", ""),
                {"agent_name": payload.get("agent_name", "Hermes"),
                 "user_name": payload.get("user_name", "")},
                expira_minutos=payload.get("expira_minutos"))
            return {"ok": True, "equipo_id": res["equipo_id"], "code": res["code"],
                    "expira_en": res.get("expira_en"),
                    "proyecto": payload.get("proyecto", "general"),
                    "carpeta": payload.get("carpeta", ""),
                    "miembros": res["equipo"]["miembros"], "nuevo": res["nuevo"]}
        if ruta == "/api/sync/equipos/unirse" and metodo == "POST":
            res = sync_mod.unirse_equipo_sync(
                payload.get("code", ""),
                {"agent_name": payload.get("agent_name", "Hermes-Amigo"),
                 "user_name": payload.get("user_name", "Amigo")},
                payload.get("carpeta") or None)
            equipo = res["equipo"]
            return {"ok": True, "equipo_id": res["equipo_id"],
                    "guest_token": res["guest_token"],
                    "proyecto": equipo["proyecto"], "carpeta": equipo["carpeta"],
                    "miembros": equipo["miembros"], "ya_miembro": res["ya_miembro"]}
        if ruta == "/api/sync/equipos/codigos/revocar" and metodo == "POST":
            return sync_mod.revocar_codigo_sync(payload.get("equipo_id", ""),
                                                payload.get("code", ""))
        if ruta == "/api/sync/equipos/agente/renombrar" and metodo == "POST":
            return sync_mod.renombrar_agente_sync(
                payload.get("equipo_id", ""), token,
                payload.get("nuevo_nombre", ""))
        if ruta == "/api/sync/equipos/aprobar" and metodo == "POST":
            return await sync_mod.aprobar_proyecto_sync(
                payload.get("equipo_id", ""), token,
                payload.get("fingerprint", ""), payload.get("nota", ""))
        if ruta == "/api/sync/equipos/retirar-aprobacion" and metodo == "POST":
            return sync_mod.retirar_aprobacion_sync(payload.get("equipo_id", ""), token)
        if ruta == "/api/sync/equipos/aprobacion/completada" and metodo == "POST":
            return sync_mod.completar_tarea_sync(
                payload.get("equipo_id", ""), token,
                payload.get("tarea_id", ""), payload.get("resumen", ""))
        raise ValueError(f"ruta de relay no soportada en proceso: {metodo} {ruta}")
    url = f"{base}{ruta}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=15.0, trust_env=False) as cliente:
        if metodo == "POST":
            r = await cliente.post(url, json=payload, headers=headers)
        else:
            r = await cliente.get(url, headers=headers)
    try:
        datos = r.json()
    except Exception:
        datos = {"ok": False, "error": f"respuesta no-JSON del relay ({r.status_code})"}
    if r.status_code >= 400 and not datos.get("error"):
        datos["error"] = f"relay devolvió {r.status_code}"
    datos["_status"] = r.status_code
    return datos


def _token_sesion(proyecto: str, carpeta: str, token_fallback: str = "") -> str:
    """Token de ESTA laptop para ese equipo (owner si es anfitrión,
    guest_token si es invitada). El que se reenvía al relay."""
    for s in sync_mod._cargar_sesiones():
        if s.get("proyecto") == proyecto and s.get("carpeta") == carpeta:
            if s.get("token"):
                return s["token"]
    return token_fallback


@router.post("/api/proyectos/{carpeta}/compartir")
async def api_proyecto_compartir(carpeta: str, req: Request, token: str = Query("")):
    """Comparte UN proyecto (carpeta): crea su equipo o añade un código
    nuevo al equipo existente. Solo dueño o local. Arranca la sesión
    peer de este proyecto en esta laptop. El código expira (por
    defecto 24 h; ``expira_minutos`` cambia la duración, 0 = sin
    expiración). La respuesta incluye ``relay_url`` y ``join_link``
    para compartir sin pedir ninguna URL al invitado."""
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "Solo el anfitrión puede compartir un proyecto."},
                            status_code=403)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    base = _relay_http_base(body.get("hub_url"))
    try:
        expira = body.get("expira_minutos")
        expira_minutos = int(expira) if expira is not None else None
    except (TypeError, ValueError):
        return JSONResponse({"error": "expira_minutos inválido"}, status_code=400)
    payload = {
        "proyecto": proyecto, "carpeta": carpeta,
        "agent_name": _nombre(body, "agent_name", "Hermes"),
        "user_name": _nombre(body, "user_name", config.CFG.get("nombre", "Daniel")),
        "expira_minutos": expira_minutos,
    }
    token_relay = _token_relay(base)
    try:
        res = await _relay_llamar(base, "POST", "/api/sync/equipos", payload,
                                  token=token_relay)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"No se pudo contactar al relay: {e}"},
                            status_code=502)
    if res.get("_status", 200) >= 400 or not res.get("ok"):
        return JSONResponse({"error": res.get("error", "el relay rechazó el equipo")},
                            status_code=res.get("_status", 400) or 400)
    equipo_id = res["equipo_id"]
    ws_url = _relay_ws_url(body.get("hub_url"))
    peer_id = f"peer-{config.CFG.get('nombre', 'host')}-{carpeta}"
    sync_mod.registrar_sesion(proyecto, carpeta, equipo_id, ws_url,
                              token_relay, peer_id,
                              payload["user_name"], "host",
                              miembros=res.get("miembros", []),
                              agent_name=payload["agent_name"])
    sync_mod.iniciar_sesion(proyecto, carpeta, equipo_id, ws_url,
                            token_relay, peer_id, payload["user_name"])
    if base is None:
        await sync_mod.difundir_equipo_update(equipo_id, res.get("miembros", []))
    return {"ok": True, "equipo_id": equipo_id, "code": res["code"],
            "expira_en": res.get("expira_en"),
            "relay_url": _relay_display_url(base),
            "join_link": _join_link(base, res["code"]),
            "proyecto": proyecto, "carpeta": carpeta,
            "miembros": res.get("miembros", []), "nuevo": res.get("nuevo", False)}


@router.post("/api/proyectos/unirse")
async def api_proyecto_unirse(req: Request):
    """Unirse a UN proyecto con su código. El código ES la credencial.
    Acepta el código solo o el enlace de unión (que trae el relay):
    pegar solo el código basta — la app resuelve el relay sola.
    Entra al equipo existente de esa carpeta (con catch-up) y arranca
    la sesión peer de ese proyecto en esta laptop."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    codigo, relay_enlace = _parsear_codigo_o_enlace(
        body.get("code", body.get("pair_code", "")))
    carpeta = str(body.get("carpeta", "") or "").strip()
    proyecto = str(body.get("proyecto", "general") or "general")
    if not codigo or not carpeta:
        return JSONResponse({"error": "code y carpeta requeridos."}, status_code=400)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    # El relay del enlace manda sobre el hub_url del body (si lo hay).
    hub_url = relay_enlace or body.get("hub_url")
    base = _relay_http_base(hub_url)
    payload = {
        "code": codigo,
        "carpeta": carpeta,  # el relay valida que el código sea de esta carpeta
        "agent_name": _nombre(body, "agent_name", "Hermes-Amigo"),
        "user_name": _nombre(body, "user_name", config.CFG.get("nombre", "Amigo")),
    }
    try:
        res = await _relay_llamar(base, "POST", "/api/sync/equipos/unirse", payload)
    except ValueError as e:
        mensaje = str(e)
        if "revocado" in mensaje or "expirado" in mensaje:
            status = 410
        elif "completo" in mensaje:
            status = 403
        elif "no de" in mensaje or "ya está en uso" in mensaje:
            status = 400
        else:
            status = 404
        return JSONResponse({"ok": False, "error": mensaje}, status_code=status)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"No se pudo contactar al relay: {e}"},
                            status_code=502)
    if res.get("_status", 200) >= 400 or not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error", "código inválido")},
                            status_code=res.get("_status", 404) or 404)
    equipo_id = res["equipo_id"]
    # Defensa en profundidad: el relay ya validó la carpeta.
    if res.get("carpeta") != carpeta or res.get("proyecto") != proyecto:
        return JSONResponse(
            {"ok": False,
             "error": f"Ese código es del proyecto «{res.get('carpeta')}», no de «{carpeta}»."},
            status_code=400)
    ws_url = _relay_ws_url(hub_url)
    peer_id = f"peer-{payload['user_name']}-{carpeta}"
    guest_token = res["guest_token"]
    sync_mod.registrar_sesion(proyecto, carpeta, equipo_id, ws_url,
                              guest_token, peer_id, payload["user_name"], "guest",
                              miembros=res.get("miembros", []),
                              agent_name=payload["agent_name"])
    sync_mod.iniciar_sesion(proyecto, carpeta, equipo_id, ws_url,
                            guest_token, peer_id, payload["user_name"])
    return {"ok": True, "equipo_id": equipo_id, "proyecto": proyecto,
            "carpeta": carpeta, "miembros": res.get("miembros", []),
            "ya_miembro": res.get("ya_miembro", False)}


@router.get("/api/proyectos/compartidos")
async def api_proyectos_compartidos(req: Request, token: str = Query("")):
    """Proyectos que esta laptop comparte (o en los que participa),
    con sus miembros. Solo local o token válido."""
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {"ok": True, "proyectos": sync_mod.sesiones_activas()}


@router.post("/api/proyectos/{carpeta}/salir")
async def api_proyecto_salir(carpeta: str, req: Request, token: str = Query("")):
    """Salir del equipo de UN proyecto: detiene su sesión y borra el
    registro local. Si era el anfitrión, el equipo se cierra en el relay.
    Solo dueño o local: un invitado remoto no puede cerrar las sesiones
    de esta laptop."""
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "Solo el dueño puede cerrar una sesión de esta laptop."},
                            status_code=403)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    guardada = sync_mod._buscar_sesion_guardada(proyecto, carpeta)
    if guardada is None:
        return JSONResponse({"error": "No estás compartiendo ese proyecto."},
                            status_code=404)
    agent_name = _nombre(body, "agent_name", guardada.get("agent_name", "Hermes"))
    user_name = _nombre(body, "user_name", guardada.get("autor", ""))
    base = _relay_http_base(body.get("hub_url") or guardada.get("hub_url"))
    # Avisar al relay (best-effort: aunque falle, la sesión local se cierra).
    try:
        if base is None:
            sync_mod.salir_equipo_sync(
                guardada["equipo_id"], guardada.get("token", ""),
                agent_name, user_name, es_dueno=True)
        else:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as cliente:
                await cliente.post(f"{base}/api/sync/equipos/salir", json={
                    "equipo_id": guardada["equipo_id"],
                    "agent_name": agent_name,
                    "user_name": user_name,
                }, headers={"Authorization": f"Bearer {guardada.get('token', '')}"})
    except Exception:
        pass
    await sync_mod.detener_sesion(proyecto, carpeta)
    sync_mod.olvidar_sesion(proyecto, carpeta)
    return {"ok": True, "carpeta": carpeta}


@router.get("/api/proyectos/{carpeta}/codigos")
async def api_proyecto_codigos(carpeta: str, req: Request, token: str = Query("")):
    """Códigos del equipo de este proyecto con su estado. Solo local."""
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "Solo el dueño puede ver los códigos."},
                            status_code=403)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    proyecto = req.query_params.get("proyecto", "general") or "general"
    guardada = sync_mod._buscar_sesion_guardada(proyecto, carpeta)
    if guardada is None:
        return JSONResponse({"error": "No estás compartiendo ese proyecto."},
                            status_code=404)
    base = _relay_http_base(guardada.get("hub_url"))
    try:
        if base is None:
            codigos = sync_mod.listar_codigos_sync(guardada["equipo_id"])
            return {"ok": True, "equipo_id": guardada["equipo_id"], "codigos": codigos}
        res = await _relay_llamar(
            base, "GET",
            f"/api/sync/equipos/codigos?equipo_id={urllib.parse.quote(guardada['equipo_id'], safe='')}",
            {}, token=_token_relay(base))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": f"No se pudo contactar al relay: {e}"},
                            status_code=502)
    if res.get("_status", 200) >= 400 or not res.get("ok"):
        return JSONResponse({"error": res.get("error", "el relay rechazó la petición")},
                            status_code=res.get("_status", 400) or 400)
    return {"ok": True, "equipo_id": guardada["equipo_id"],
            "codigos": res.get("codigos", [])}


@router.post("/api/proyectos/{carpeta}/codigos/revocar")
async def api_proyecto_codigos_revocar(carpeta: str, req: Request, token: str = Query("")):
    """Revoca UN código del equipo de este proyecto. Solo el anfitrión
    (dueño o local): el equipo sigue vivo y los demás códigos valen."""
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "Solo el anfitrión puede revocar códigos."},
                            status_code=403)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    codigo = str(body.get("code", "") or "").strip()
    if not codigo:
        return JSONResponse({"error": "code requerido."}, status_code=400)
    guardada = sync_mod._buscar_sesion_guardada(proyecto, carpeta)
    if guardada is None:
        return JSONResponse({"error": "No estás compartiendo ese proyecto."},
                            status_code=404)
    # Solo el anfitrión revoca: la sesión de un invitado no tiene
    # permiso (el relay lo haría valer, pero se frena aquí también).
    if guardada.get("rol") != "host":
        return JSONResponse({"error": "Solo el anfitrión puede revocar códigos."},
                            status_code=403)
    base = _relay_http_base(body.get("hub_url") or guardada.get("hub_url"))
    try:
        res = await _relay_llamar(
            base, "POST", "/api/sync/equipos/codigos/revocar",
            {"equipo_id": guardada["equipo_id"], "code": codigo},
            token=_token_relay(base))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": f"No se pudo contactar al relay: {e}"},
                            status_code=502)
    if res.get("_status", 200) >= 400 or not res.get("ok"):
        return JSONResponse({"error": res.get("error", "el relay rechazó la petición")},
                            status_code=res.get("_status", 400) or 400)
    return {"ok": True, "code": res.get("code", codigo)}


@router.post("/api/proyectos/{carpeta}/agente/renombrar")
async def api_proyecto_agente_renombrar(carpeta: str, req: Request, token: str = Query("")):
    """Renombra MI agente en el equipo de este proyecto (solo el mío:
    el relay lo hace valer por token). El nombre nuevo se propaga al
    equipo (sync_equipo_update) y a mi propio estado de agente."""
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "token inválido"}, status_code=403)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    nuevo_nombre = str(body.get("nuevo_nombre", "") or "")
    if not nuevo_nombre:
        return JSONResponse({"error": "nuevo_nombre requerido."}, status_code=400)
    guardada = sync_mod._buscar_sesion_guardada(proyecto, carpeta)
    if guardada is None:
        return JSONResponse({"error": "No estás compartiendo ese proyecto."},
                            status_code=404)
    base = _relay_http_base(body.get("hub_url") or guardada.get("hub_url"))
    token_sesion = _token_sesion(proyecto, carpeta, token)
    try:
        res = await _relay_llamar(
            base, "POST", "/api/sync/equipos/agente/renombrar",
            {"equipo_id": guardada["equipo_id"], "nuevo_nombre": nuevo_nombre},
            token=token_sesion)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
    except Exception as e:
        return JSONResponse({"error": f"No se pudo contactar al relay: {e}"},
                            status_code=502)
    if res.get("_status", 200) >= 400 or not res.get("ok"):
        return JSONResponse({"error": res.get("error", "el relay rechazó la petición")},
                            status_code=res.get("_status", 400) or 400)
    # El nombre nuevo es MÍO (el relay solo deja renombrar el propio):
    # actualizar mi estado de agente local para que la UI lo muestre.
    from backend.core.state import HERMES_STATE
    if res.get("agente", {}).get("dueno") == guardada.get("autor"):
        HERMES_STATE["my_agent"]["name"] = nuevo_nombre
    return res


@router.post("/api/proyectos/{carpeta}/aprobar")
async def api_proyecto_aprobar(carpeta: str, req: Request, token: str = Query("")):
    """"Me gusta" a la versión ACTUAL del proyecto (huella del árbol).
    Solo cuando anfitrión Y compañero aprueban la misma versión, los
    agentes terminan el desarrollo de forma real."""
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "token inválido"}, status_code=403)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    nota = str(body.get("nota", "") or "")
    guardada = sync_mod._buscar_sesion_guardada(proyecto, carpeta)
    if guardada is None:
        return JSONResponse({"error": "No estás compartiendo ese proyecto."},
                            status_code=404)
    fingerprint = sync_mod.huella_proyecto(proyecto, carpeta)
    base = _relay_http_base(body.get("hub_url") or guardada.get("hub_url"))
    token_sesion = _token_sesion(proyecto, carpeta, token)
    try:
        res = await _relay_llamar(
            base, "POST", "/api/sync/equipos/aprobar",
            {"equipo_id": guardada["equipo_id"], "fingerprint": fingerprint,
             "nota": nota}, token=token_sesion)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
    except Exception as e:
        return JSONResponse({"error": f"No se pudo contactar al relay: {e}"},
                            status_code=502)
    if res.get("_status", 200) >= 400 or not res.get("ok"):
        return JSONResponse({"error": res.get("error", "el relay rechazó la petición")},
                            status_code=res.get("_status", 400) or 400)
    # El relay ya difundió el cambio de aprobación a todos los peers.
    return res


@router.post("/api/proyectos/{carpeta}/retirar-aprobacion")
async def api_proyecto_retirar_aprobacion(carpeta: str, req: Request,
                                          token: str = Query("")):
    """Retira MI aprobación (bloquea el disparo si la tarea aún no se
    disparó)."""
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "token inválido"}, status_code=403)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    try:
        body = await req.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    guardada = sync_mod._buscar_sesion_guardada(proyecto, carpeta)
    if guardada is None:
        return JSONResponse({"error": "No estás compartiendo ese proyecto."},
                            status_code=404)
    base = _relay_http_base(body.get("hub_url") or guardada.get("hub_url"))
    token_sesion = _token_sesion(proyecto, carpeta, token)
    try:
        res = await _relay_llamar(
            base, "POST", "/api/sync/equipos/retirar-aprobacion",
            {"equipo_id": guardada["equipo_id"]}, token=token_sesion)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
    except Exception as e:
        return JSONResponse({"error": f"No se pudo contactar al relay: {e}"},
                            status_code=502)
    if res.get("_status", 200) >= 400 or not res.get("ok"):
        return JSONResponse({"error": res.get("error", "el relay rechazó la petición")},
                            status_code=res.get("_status", 400) or 400)
    return res


@router.get("/api/proyectos/{carpeta}/aprobaciones")
async def api_proyecto_aprobaciones(carpeta: str, req: Request, token: str = Query("")):
    """Estado de aprobación de la versión ACTUAL del proyecto."""
    if not _es_dueno_o_local(req, token):
        return JSONResponse({"error": "token inválido"}, status_code=403)
    if not sync_mod.carpeta_segura(carpeta):
        return JSONResponse({"error": "carpeta inválida"}, status_code=400)
    proyecto = req.query_params.get("proyecto", "general") or "general"
    guardada = sync_mod._buscar_sesion_guardada(proyecto, carpeta)
    if guardada is None:
        return JSONResponse({"error": "No estás compartiendo ese proyecto."},
                            status_code=404)
    fingerprint = sync_mod.huella_proyecto(proyecto, carpeta)
    base = _relay_http_base(guardada.get("hub_url"))
    try:
        if base is None:
            res = sync_mod.estado_aprobaciones_sync(guardada["equipo_id"], fingerprint)
            return res
        res = await _relay_llamar(
            base, "GET",
            f"/api/sync/equipos/aprobaciones?equipo_id={urllib.parse.quote(guardada['equipo_id'], safe='')}&fingerprint={urllib.parse.quote(fingerprint, safe='')}",
            {}, token=_token_sesion(proyecto, carpeta, token))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": f"No se pudo contactar al relay: {e}"},
                            status_code=502)
    if res.get("_status", 200) >= 400 or not res.get("ok"):
        return JSONResponse({"error": res.get("error", "el relay rechazó la petición")},
                            status_code=res.get("_status", 400) or 400)
    return res
