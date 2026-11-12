"""Router agentes, bus, websockets y relay de sync — movido intacto desde server.py (NEXO 2.0).

Comportamiento idéntico: mismas rutas, métodos, formas de request/response
y códigos de estado. Solo cambió el andamiaje (package backend/).

El modo equipo viejo a nivel de app (/api/pairing/*) está retirado:
compartir se hace POR PROYECTO (routers/sync_teams.py) y los agentes
son miembros del equipo del proyecto (bus con alcance al equipo).
"""
from __future__ import annotations

from typing import Any
import asyncio
import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

import ai_engine
import social_matrix
from backend.core import state
from backend.core.config import (
    config,
    es_host,
    es_local,
    token_ok,
)
from backend.core.state import (
    Sala,
    _arbol,
    _bump_version_archivo,
    _bus_difundir,
    _bus_esperar_respuesta,
    _bus_inbox,
    _bus_post,
    _color_para,
    _difundir_presencia,
    difundir_arbol,
    enviar,
    enviar_sala,
    enviar_todos,
    get_bus_wake_event,
    ruta_segura,
    sala_de,
)
from backend.store import get_store
from backend import sync as sync_mod

router = APIRouter()

@router.post("/api/chat")
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
    if len(texto) > config.MAX_CHAT:
        return JSONResponse({"error": f"el mensaje supera {config.MAX_CHAT} caracteres"}, status_code=413)

    nombre = str(body.get("de") or config.CFG.get("nombre", "dev")).strip()[:120] or "dev"
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sala = sala_de(proyecto)
    sala.guardar_chat(nombre, texto, fecha)
    evento = {"tipo": "chat", "proyecto": sala.nombre, "de": nombre, "texto": texto, "fecha": fecha}
    await enviar_sala(sala, evento)
    return {"ok": True, "mensaje": evento}


@router.get("/api/ai/inbox")
async def api_ai_inbox(request: Request, agente: str = Query(""), proyecto: str = Query(""), pendientes: bool = Query(False), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    mensajes = get_store().get("mensajes", [])
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


@router.post("/api/ai/msg")
async def api_ai_msg(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    mid = f"ai-{int(time.time())}-{secrets.token_hex(2)}"
    nuevo_msg = {
        "id": mid,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "de": body.get("de", config.CFG.get("ia_nombre", "antigravity")),
        "para": body.get("para", "todos"),
        "proyecto": body.get("proyecto", "general"),
        "tipo": body.get("tipo", "tarea"),
        "titulo": body.get("titulo", "Mensaje IA"),
        "contenido": body.get("contenido", ""),
        "archivos": body.get("archivos", []),
        "codigo": body.get("codigo", ""),
        "estado": body.get("estado", "pendiente"),
    }
    get_store().setdefault("mensajes", []).append(nuevo_msg)

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
            get_store()["mensajes"].append(rep_msg)
            await enviar_todos({"tipo": "ai_msg", "mensaje": rep_msg})
            # Chat en sala
            sala.guardar_chat(rep_msg["de"], resp_ia, rep_msg["fecha"])
            await enviar_sala(sala, {
                "tipo": "chat", "proyecto": sala.nombre,
                "de": f"🤖 {rep_msg['de']}", "texto": resp_ia,
                "fecha": rep_msg["fecha"], "codigo": cod_ia,
                "agente": {"nombre": rep_msg["de"],
                           "color": _color_para(rep_msg["de"])},
            })
        asyncio.create_task(procesar_tarea_ia())

    return {"ok": True, "mensaje": nuevo_msg}


@router.get("/api/ai/tasks")
async def api_ai_tasks(request: Request, proyecto: str = Query(""), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    tareas = get_store().get("tareas", [])
    if proyecto:
        tareas = [t for t in tareas if t.get("proyecto") == proyecto]
    return {"tareas": list(tareas)}


@router.post("/api/ai/task")
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
    tareas = get_store().setdefault("tareas", [])
    existente = next((t for t in tareas if t["id"] == tid), None)
    if existente:
        existente.update(tarea)
    else:
        tareas.append(tarea)
    await enviar_todos({"tipo": "ai_task_update", "tarea": tarea})

    # Si la tarea se asigna a un agente concreto, también viaja por el bus
    # para que el agente (vía hermes_link) la recoja.
    asignado = str(tarea.get("asignado", "")).strip()
    if asignado and asignado.lower() not in ("todos", "nadie", ""):
        msg = _bus_post(
            de=str(tarea.get("creador", "usuario")),
            para=asignado,
            texto=f"Tarea asignada: {tarea.get('titulo', '')}\n{tarea.get('descripcion', '')}".strip(),
            tipo="tarea",
            task_ref=tarea["id"],
            proyecto=tarea.get("proyecto", "general"),
        )
        await _bus_difundir(msg)

    return {"ok": True, "tarea": tarea}


@router.post("/api/agents/message")
async def api_agents_message(req: Request, token: str = Query("")):
    """Un agente (o humano) envía un mensaje a otro agente por el bus.
    Con ``equipo_id`` (+ ``carpeta``), el mensaje queda con alcance a
    ese equipo del proyecto y viaja al bus de la otra laptop (relay):
    los agentes de X no ven ni actúan en Y. ``agente`` es la identidad
    de quien habla (nombre + color) para que siempre se sepa qué
    agente habla."""
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    texto = str(body.get("text", body.get("texto", ""))).strip()
    if not texto:
        return JSONResponse({"error": "texto requerido"}, status_code=400)
    equipo_id = str(body.get("equipo_id", "") or "")
    carpeta = str(body.get("carpeta", "") or "")
    agente = body.get("agente") if isinstance(body.get("agente"), dict) else None
    proyecto = str(body.get("proyecto", "general"))
    if equipo_id:
        try:
            msg = await sync_mod.publicar_en_bus_equipo(
                proyecto, carpeta, equipo_id,
                de=str(body.get("from", body.get("de", "usuario"))),
                para=str(body.get("to", body.get("para", "todos"))),
                texto=texto,
                tipo=str(body.get("tipo", "mensaje")),
                task_ref=body.get("task_ref"),
                agente=agente,
            )
        except Exception as e:
            # Error real, no éxito fingido: si no se pudo persistir,
            # el mensaje NO quedó en el bus.
            return JSONResponse(
                {"ok": False, "error": f"no se pudo guardar el mensaje en el bus: {e}"},
                status_code=500)
        return {"ok": True, "mensaje": msg}
    try:
        msg = _bus_post(
            de=str(body.get("from", body.get("de", "usuario"))),
            para=str(body.get("to", body.get("para", "todos"))),
            texto=texto,
            tipo=str(body.get("tipo", "mensaje")),
            task_ref=body.get("task_ref"),
            proyecto=proyecto,
            agente=agente,
        )
        await _bus_difundir(msg)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"no se pudo guardar el mensaje en el bus: {e}"},
            status_code=500)
    return {"ok": True, "mensaje": msg}


@router.get("/api/agents/inbox")
async def api_agents_inbox(request: Request, agent: str = Query(""),
                           pendientes: bool = Query(False), token: str = Query(""),
                           equipo_id: str = Query("")):
    """Bandeja del bus para un agente (la usa hermes_link para recoger trabajo).
    Con ``equipo_id``, solo los mensajes de ese equipo."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    mensajes = _bus_inbox(agent, solo_pendientes=pendientes,
                          equipo_id=equipo_id or None)
    return {"mensajes": mensajes, "total": len(mensajes)}


@router.get("/api/agents/bus")
async def api_agents_bus(request: Request, proyecto: str = Query(""),
                         token: str = Query(""), equipo_id: str = Query("")):
    """Historial del bus de agentes (lo muestra la pestaña Agentes).
    Con ``equipo_id``, solo los mensajes de ese equipo."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    mensajes = get_store().get("agent_bus", [])
    if proyecto:
        mensajes = [m for m in mensajes if not m.get("proyecto") or m.get("proyecto") == proyecto]
    if equipo_id:
        mensajes = [m for m in mensajes if m.get("equipo_id") == equipo_id]
    return {"mensajes": list(mensajes), "total": len(mensajes)}


@router.post("/api/agents/bus/conectar")
async def api_agents_bus_conectar(req: Request, token: str = Query("")):
    """(Re)conexión de un agente al bus persistente de su equipo.

    Devuelve los mensajes perdidos del equipo con ``seq > last_seq``
    (los publicados mientras estaba desconectado), en orden, y avanza
    el cursor. Sin duplicados: la segunda llamada sin mensajes nuevos
    devuelve lista vacía. Un equipo nunca recibe mensajes de otro."""
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    from backend import bus_entrega
    try:
        body = await req.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    equipo_id = str(body.get("equipo_id", "") or "").strip()
    agente = str(body.get("agente", body.get("agent", "")) or "").strip()
    if not equipo_id or not agente:
        return JSONResponse(
            {"ok": False, "error": "equipo_id y agente requeridos"}, status_code=400)
    try:
        res = bus_entrega.conectar(equipo_id, agente)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"no se pudo leer el bus: {e}"}, status_code=500)
    res["ok"] = True
    return res


@router.post("/api/agents/ack")
async def api_agents_ack(req: Request, token: str = Query("")):
    """Un agente confirma un mensaje del bus con su resultado."""
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    mid = str(body.get("id", "")).strip()
    resultado = str(body.get("result", body.get("resultado", "")))
    estado = str(body.get("estado", "completado"))
    if not mid:
        return JSONResponse({"error": "id requerido"}, status_code=400)
    target = next((m for m in get_store().get("agent_bus", []) if m.get("id") == mid), None)
    if not target:
        return JSONResponse({"error": "mensaje no encontrado"}, status_code=404)
    target["estado"] = estado
    target["resultado"] = resultado
    # La respuesta viaja como mensaje propio para que el otro extremo la vea.
    # Si el mensaje original era de un equipo, la respuesta vuelve a ese
    # equipo (relay) con la identidad del agente que responde.
    agente = body.get("agente") if isinstance(body.get("agente"), dict) else None
    equipo_id = str(target.get("equipo_id") or "")
    if equipo_id:
        resp = await sync_mod.publicar_en_bus_equipo(
            target.get("proyecto", "general"), str(target.get("carpeta", "") or ""),
            equipo_id,
            de=str(body.get("from", body.get("de", target.get("para", "agente")))),
            para=str(target.get("de", "usuario")),
            texto=resultado or f"Completado: {target.get('tipo', 'tarea')}",
            tipo="respuesta",
            task_ref=target.get("task_ref"),
            agente=agente,
            respuesta_a=mid,
            estado="completado",
        )
    else:
        resp = _bus_post(
            de=str(body.get("from", body.get("de", target.get("para", "agente")))),
            para=str(target.get("de", "usuario")),
            texto=resultado or f"Completado: {target.get('tipo', 'tarea')}",
            tipo="respuesta",
            task_ref=target.get("task_ref"),
            proyecto=target.get("proyecto", "general"),
            respuesta_a=mid,
            agente=agente,
        )
        await _bus_difundir(resp)
    if resp is not None:
        resp["estado"] = "completado"
        resp["respuesta_a"] = mid
    return {"ok": True, "respuesta": resp}


# ------------------------------------------------------------------
# Modo equipo viejo RETIRADO (v0.6.0): los endpoints /api/pairing/*
# (create/join/leave/unpair/status) y el estado app-level `paired`
# ya no existen. Compartir se hace POR PROYECTO (ver
# routers/sync_teams.py): cada equipo vive dentro de un proyecto,
# con sus agentes como miembros del equipo. Los códigos ATL- viejos
# de equipo no funcionan; el anfitrión vuelve a compartir cada
# proyecto con el flujo nuevo. La tabla Pairing queda sin uso.
# ------------------------------------------------------------------

@router.post("/api/ai/hermes/run")
async def api_ai_hermes_run(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt requerido"}, status_code=400)

    proyecto = body.get("proyecto", "")
    sala = sala_de(proyecto) if proyecto else None
    cwd_dir = Path(sala.directorio) if (sala and Path(sala.directorio).is_dir()) else config.WORKSPACE

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

    ret, salida = await ai_engine.ejecutar_agente(prompt, cwd=cwd_dir, timeout=300, on_chunk=on_chunk)

    await enviar_todos({
        "tipo": "hermes_stream_end",
        "retcode": ret,
        "salida": salida,
        "proyecto": proyecto or "general",
    })

    await difundir_arbol()
    return {"ok": ret == 0, "retcode": ret, "salida": salida}


@router.post("/api/ai/gemini/run")
async def api_ai_gemini_run(req: Request, token: str = Query("")):
    # Endpoint de nube eliminado: Atlantis Studio no usa API keys.
    return JSONResponse(
        {"ok": False, "error": "gone",
         "detalle": "Las consultas en la nube se eliminaron. Usa tu Hermes conectado (/api/ai/hermes/run)."},
        status_code=410,
    )


@router.post("/api/agents/stream")
async def api_agents_stream(req: Request, token: str = Query("")):
    """Chat con streaming REAL (Server-Sent Events) del asistente activo.

    Body: {"prompt": str, "slot": str (opcional, nombre de slot o ""=activo),
           "proyecto": str (opcional)}.
    Eventos: `start` → `data: {"chunk": ...}` (tantos como lleguen) →
    `done: {"retcode": int, "full": str}` o `error: {...}`.
    Si el cliente cierra la conexión (botón Detener), el subproceso se mata.
    """
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        body = {}
    prompt = str(body.get("prompt", "") or "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt requerido"}, status_code=400)
    slot = str(body.get("slot", "") or "").strip()
    proyecto = str(body.get("proyecto", "") or "").strip()
    sala = sala_de(proyecto) if proyecto else None
    cwd_dir = Path(sala.directorio) if (sala and Path(sala.directorio).is_dir()) else config.WORKSPACE

    async def gen():
        yield f"event: start\ndata: {json.dumps({'slot': slot or 'activo'})}\n\n"
        try:
            async for kind, payload in ai_engine.ejecutar_agente_stream(
                prompt, adapter_id=slot, cwd=cwd_dir, timeout=300
            ):
                if kind == "chunk":
                    yield f"data: {json.dumps({'chunk': payload})}\n\n"
                elif kind == "done":
                    ret, full = payload
                    yield f"event: done\ndata: {json.dumps({'retcode': ret, 'full': full})}\n\n"
                    return
        except asyncio.CancelledError:
            # Cliente desconectado (Detener): el adaptador ya mató el proceso.
            yield "event: cancelled\ndata: {}\n\n"
            raise
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)[:300]})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class _BusAdapter:
    """Adaptador del bus de agentes para ai_engine.colaboracion_dual.

    El "agente amigo" ya no es un vínculo a nivel de app: es el otro
    miembro del equipo del proyecto (si el proyecto está compartido).
    Sin equipo compartido, no hay amigo (modo individual honesto)."""

    def __init__(self, proyecto: str = "general", carpeta: str = ""):
        self.proyecto = proyecto
        self.carpeta = carpeta

    def _equipo_de_proyecto(self) -> tuple[dict | None, str]:
        """(sesión, equipo_id) del equipo de este proyecto, o (None, '').
        Sin carpeta, usa la primera sesión con otro miembro."""
        for s in sync_mod.sesiones_activas():
            if self.carpeta and s.get("carpeta") != self.carpeta:
                continue
            if self.proyecto and s.get("proyecto") != self.proyecto:
                continue
            miembros = s.get("miembros", []) or []
            yo = config.CFG.get("nombre", "")
            otros = [m for m in miembros
                     if str(m.get("user_name", "")).strip().lower()
                     != str(yo).strip().lower()]
            if otros:
                return s, s.get("equipo_id", "")
        return None, ""

    async def post(self, de: str, para: str, texto: str, tipo: str = "tarea",
                   task_ref: str | None = None) -> dict:
        sesion, equipo_id = self._equipo_de_proyecto()
        if sesion is not None and equipo_id:
            # Con equipo: el mensaje viaja al bus del otro agente (relay).
            msg = await sync_mod.publicar_en_bus_equipo(
                sesion.get("proyecto", self.proyecto),
                sesion.get("carpeta", self.carpeta), equipo_id,
                de=de, para=para, texto=texto, tipo=tipo, task_ref=task_ref)
            return msg or {}
        msg = _bus_post(de=de, para=para, texto=texto, tipo=tipo, task_ref=task_ref)
        await _bus_difundir(msg)
        return msg

    async def wait_reply(self, respuesta_a: str, timeout: float = 180.0) -> dict | None:
        return await _bus_esperar_respuesta(respuesta_a, timeout)

    def my_agent_name(self) -> str:
        return state.HERMES_STATE.get("my_agent", {}).get("name", "Hermes")

    def paired_agent_name(self) -> str | None:
        """Nombre del agente del compañero en este proyecto, o None
        si el proyecto no está compartido (modo individual)."""
        sesion, _ = self._equipo_de_proyecto()
        if sesion is None:
            return None
        yo = config.CFG.get("nombre", "")
        for m in sesion.get("miembros", []) or []:
            if str(m.get("user_name", "")).strip().lower() != str(yo).strip().lower():
                return m.get("agent_name")
        return None


@router.post("/api/ai/dual/run")
async def api_ai_dual_run(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    objetivo = body.get("objetivo", "").strip()
    if not objetivo:
        return JSONResponse({"error": "objetivo requerido"}, status_code=400)

    proyecto = body.get("proyecto", "")
    sala = sala_de(proyecto) if proyecto else None
    cwd_dir = Path(sala.directorio) if (sala and Path(sala.directorio).is_dir()) else config.WORKSPACE

    def on_event(ev: dict):
        asyncio.create_task(enviar_todos(ev))

    resultado = await ai_engine.colaboracion_dual(
        objetivo, cwd=cwd_dir, on_event=on_event,
        bus=_BusAdapter(proyecto=proyecto, carpeta=str(body.get("carpeta", "") or "")),
    )
    await difundir_arbol()
    return resultado


@router.get("/api/hermes/detect")
async def api_hermes_detect(request: Request):
    hermes_dir = config.HOME / ".hermes"
    hermes_bin = ai_engine.detectar_hermes_bin()
    cfg_yaml = hermes_dir / "config.yaml"

    # Defaults neutrales: jamás suplantar el agente de Daniel. Solo la config
    # real de ESTA máquina (~/.hermes/config.yaml|json o HERMES_AGENT_NAME)
    # pone nombre/modelo. Sin Hermes local, quedan vacíos (installed=false).
    agent_name = ""
    model_name = ""
    provider_name = ""
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


@router.get("/api/hermes/status")
async def api_hermes_status(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {
        "ok": True,
        "state": state.HERMES_STATE,
        "workspace": str(config.WORKSPACE),
        "workspace_name": config.WORKSPACE.name,
    }


# ---------------------------------------------------------------- Hermes Worker & Diagnósticos
_hermes_worker_task: asyncio.Task | None = None
_hermes_worker_stats: dict[str, Any] = {
    "active": False,
    "last_poll": 0.0,
    "tasks_processed": 0,
    "errors": 0,
}


def _hermes_worker_is_running() -> bool:
    global _hermes_worker_task
    return _hermes_worker_task is not None and not _hermes_worker_task.done()


async def _hermes_bus_worker_loop() -> None:
    """Procesador reactivo en segundo plano para tareas y mensajes de agentes en el bus."""
    global _hermes_worker_stats
    _hermes_worker_stats["active"] = True
    ev = get_bus_wake_event()
    while True:
        try:
            # Despertar reactivo: responde al instante cuando hay un mensaje nuevo en el bus
            try:
                await asyncio.wait_for(ev.wait(), timeout=1.0)
            except TimeoutError:
                pass
            ev.clear()
            _hermes_worker_stats["last_poll"] = time.time()

            import agents as agent_registry
            active_slot_name = agent_registry.get_active_slot_name() or "Koko"

            # Mensajes del store persistente (HubStore / SQLite)
            mensajes = get_store().get("agent_bus", [])
            for msg in list(mensajes):
                if not isinstance(msg, dict):
                    continue
                if msg.get("estado") != "pendiente":
                    continue

                para = str(msg.get("para", "")).strip().lower()
                target_names = {
                    active_slot_name.lower(),
                    "hermes",
                    "koko",
                    "antigravity",
                    "todos",
                }
                # Añadir nombres de slots disponibles
                slots_by_name = {s["name"].lower(): s["name"] for s in agent_registry.load_slots()}
                for s_key in slots_by_name:
                    target_names.add(s_key)

                if para not in target_names:
                    continue

                msg["estado"] = "procesando"
                texto = str(msg.get("texto", "")).strip()
                de = str(msg.get("de", "usuario")).strip()
                task_ref = msg.get("task_ref")
                tipo = msg.get("tipo", "tarea")

                # Identificar nombre de ejecutor
                if para in slots_by_name:
                    target_agent_name = slots_by_name[para]
                elif para == "antigravity":
                    target_agent_name = "Antigravity"
                elif para in ("hermes", "koko"):
                    target_agent_name = active_slot_name if active_slot_name else "Hermes"
                else:
                    target_agent_name = active_slot_name

                if tipo == "tarea":
                    prompt = (
                        f"Eres {target_agent_name}, agente inteligente en Atlantis Studio.\n"
                        f"{de} te asignó la tarea:\n{texto}\n\n"
                        "Ejecútala con tus herramientas en el workspace y resume el resultado de forma clara y directa."
                    )
                else:
                    prompt = (
                        f"Eres {target_agent_name}, agente inteligente en Atlantis Studio.\n"
                        f"Mensaje de {de}:\n{texto}"
                    )

                cwd = config.WORKSPACE
                try:
                    if agent_registry.get_slot(target_agent_name):
                        ret, salida = await agent_registry.run_slot(target_agent_name, prompt, cwd=cwd, timeout=180)
                    else:
                        adapter = agent_registry.get_adapter(target_agent_name.lower())
                        if adapter and adapter.to_info().get("detected"):
                            ret, salida = await adapter.arun(prompt, cwd=cwd, timeout=180)
                        elif ai_engine.detectar_hermes_bin():
                            ret, salida = await ai_engine.ejecutar_hermes(prompt, cwd=cwd, yolo=True, timeout=180)
                        else:
                            ret, salida = -1, f"No se encontró un ejecutor activo para '{target_agent_name}'."
                except Exception as ex:
                    ret, salida = -1, f"Error ejecutando {target_agent_name}: {ex}"

                resultado_final = salida.strip() or f"(código de salida {ret})"
                msg["estado"] = "completado" if ret == 0 else "error"
                msg["resultado"] = resultado_final
                _hermes_worker_stats["tasks_processed"] += 1

                # Si es una tarea registrada en la colección de tareas, actualizarla
                if task_ref:
                    tareas = get_store().get("tareas", [])
                    for t in tareas:
                        if t.get("id") == task_ref:
                            t["estado"] = "done" if ret == 0 else "error"
                            t["actualizado"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            t["resultado"] = resultado_final
                            try:
                                await enviar_todos({"tipo": "ai_task_update", "tarea": t})
                            except Exception:
                                pass
                            break

                # Notificar al chat del estudio
                try:
                    await enviar_todos({
                        "tipo": "chat",
                        "msg": {
                            "id": f"msg-{int(time.time() * 1000)}",
                            "autor": target_agent_name,
                            "texto": resultado_final,
                            "tiempo": datetime.now().strftime("%H:%M"),
                            "proyecto": msg.get("proyecto", "general"),
                            "es_ia": True,
                            "agente": target_agent_name,
                        }
                    })
                except Exception:
                    pass

                # Confirmar en el bus y notificar reactivamente
                try:
                    ack_payload = {
                        "tipo": "ack",
                        "id": f"ack-{int(time.time())}-{secrets.token_hex(2)}",
                        "respuesta_a": msg.get("id"),
                        "de": target_agent_name,
                        "para": de,
                        "texto": resultado_final,
                        "resultado": resultado_final,
                        "estado": msg["estado"],
                    }
                    get_store().setdefault("agent_bus", []).append(ack_payload)
                    await _bus_difundir(ack_payload)
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            _hermes_worker_stats["errors"] += 1
            await asyncio.sleep(2)
    _hermes_worker_stats["active"] = False


def iniciar_hermes_worker() -> asyncio.Task | None:
    """Inicia el worker autónomo del bus de Hermes si el event loop está corriendo."""
    global _hermes_worker_task
    if _hermes_worker_task is None or _hermes_worker_task.done():
        try:
            loop = asyncio.get_running_loop()
            _hermes_worker_task = loop.create_task(_hermes_bus_worker_loop())
        except RuntimeError:
            pass
    return _hermes_worker_task


@router.get("/api/hermes/health")
async def api_hermes_health(request: Request, token: str = Query("")):
    """Diagnóstico integral de salud, conectividad y telemetría de Hermes Agent."""
    if not es_host(request, token):
        return JSONResponse({"error": "Solo el anfitrión puede hacer esto."}, status_code=403)

    hermes_dir = config.HOME / ".hermes"
    hermes_bin = ai_engine.detectar_hermes_bin()
    installed = hermes_bin is not None

    version_str = ""
    if installed:
        try:
            proc = await asyncio.create_subprocess_exec(
                str(hermes_bin), "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            lines = out.decode("utf-8", errors="replace").strip().splitlines()
            version_str = lines[0] if lines else ""
        except Exception:
            version_str = "Hermes CLI presente"

    agent_name = "Koko"
    model_name = "gemini-3-flash-preview"
    provider_name = "gemini"
    telegram_active = False

    cfg_yaml = hermes_dir / "config.yaml"
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
        m = cfg_data.get("model", {})
        if isinstance(m, dict):
            model_name = m.get("default", m.get("name", model_name))
            provider_name = m.get("provider", provider_name)
        elif isinstance(m, str):
            model_name = m

        ag = cfg_data.get("agent", {})
        if isinstance(ag, dict) and ag.get("name"):
            agent_name = ag["name"]
        elif cfg_data.get("name"):
            agent_name = str(cfg_data["name"]).strip()

        tg = cfg_data.get("telegram", {})
        if isinstance(tg, dict) and (tg.get("bot_token") or tg.get("default_chat_id")):
            telegram_active = True

    if os.environ.get("HERMES_AGENT_NAME"):
        agent_name = os.environ["HERMES_AGENT_NAME"].strip()

    gw_sock = hermes_dir / "gateway.sock"
    gw_state_file = hermes_dir / "gateway_state.json"
    gateway_running = gw_sock.exists() or gw_state_file.exists()

    import agents as agent_registry
    active_slot = agent_registry.get_active_slot_name() or agent_name

    if installed and not _hermes_worker_is_running():
        iniciar_hermes_worker()

    return {
        "ok": True,
        "status": "online" if installed else "offline",
        "installed": installed,
        "hermes_bin": str(hermes_bin) if hermes_bin else "",
        "version": version_str,
        "agent_name": agent_name,
        "model": model_name,
        "provider": provider_name,
        "gateway_running": gateway_running,
        "telegram_configured": telegram_active,
        "active_slot": active_slot,
        "worker": {
            "active": _hermes_worker_is_running(),
            "tasks_processed": _hermes_worker_stats["tasks_processed"],
            "last_poll": _hermes_worker_stats["last_poll"],
        },
        "state": state.HERMES_STATE,
        "workspace": str(config.WORKSPACE),
    }


@router.post("/api/hermes/ping")
async def api_hermes_ping(request: Request, token: str = Query("")):
    """Medición de latencia en milisegundos y prueba viva de respuesta de Hermes CLI."""
    if not es_host(request, token):
        return JSONResponse({"error": "Solo el anfitrión puede hacer esto."}, status_code=403)

    t0 = time.perf_counter()
    hermes_bin = ai_engine.detectar_hermes_bin()
    if not hermes_bin:
        return JSONResponse({
            "ok": False,
            "error": "Hermes no está instalado en esta máquina.",
            "status": "offline",
        }, status_code=404)

    try:
        proc = await asyncio.create_subprocess_exec(
            str(hermes_bin), "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        ver = out.decode("utf-8", errors="replace").strip().splitlines()
        ver_str = ver[0] if ver else ""

        import agents as agent_registry
        active = agent_registry.get_active_slot_name() or "Koko"

        return {
            "ok": True,
            "latency_ms": latency_ms,
            "status": "online",
            "agent": active,
            "version": ver_str,
            "bin": str(hermes_bin),
        }
    except Exception as e:
        return JSONResponse({
            "ok": False,
            "error": f"Fallo en la prueba de latencia con Hermes: {e}",
            "status": "error",
        }, status_code=500)


# ---------------------------------------------------------------- Asistentes IA universales (cualquier marca)
@router.get("/api/agents/available")
async def api_agents_available(request: Request, token: str = Query("")):
    """Lista honesta de adaptadores + slots configurados con su estado real."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    import agents as agent_registry
    return {"ok": True, "adapters": agent_registry.detect_all(),
            "slots": agent_registry.list_slots_status(),
            "selected": agent_registry.get_selected_id()}


@router.get("/api/agents/slots")
async def api_agents_slots(request: Request, token: str = Query("")):
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    return {"ok": True, "slots": agent_registry.list_slots_status(),
            "active": agent_registry.get_active_slot_name()}


@router.post("/api/agents/slots")
async def api_agents_slots_save(request: Request, token: str = Query("")):
    """Crea o actualiza un slot de asistente (nombres únicos: identidad en el bus)."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    try:
        body = await request.json()
        name = str(body.get("name", "") or "").strip()
        adapter = str(body.get("adapter", "") or "").strip() or "hermes"
        command = str(body.get("command", "") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "El asistente necesita un nombre."}, status_code=400)
        if adapter == "custom" and not command:
            return JSONResponse({"ok": False, "error": "El CLI personalizado necesita un comando."}, status_code=400)
        if adapter == "openai_compat" and not str(body.get("base_url", "") or "").strip():
            return JSONResponse({"ok": False, "error": "El endpoint OpenAI-compatible necesita una URL base."}, status_code=400)
        if adapter != "custom" and not agent_registry.get_adapter(adapter):
            return JSONResponse({"ok": False, "error": f"Adaptador desconocido: {adapter}"}, status_code=400)
        slot = agent_registry.save_slot(
            name, adapter, command=command,
            model=str(body.get("model", "") or "").strip(),
            timeout=int(body.get("timeout", 120) or 120),
            base_url=str(body.get("base_url", "") or "").strip(),
            api_key=str(body.get("api_key", "") or "").strip())
        if body.get("make_active"):
            agent_registry.set_active_slot_name(name)
        return {"ok": True, "slot": agent_registry.slot_status(slot),
                "slots": agent_registry.list_slots_status()}
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.delete("/api/agents/slots/{name}")
async def api_agents_slots_delete(name: str, request: Request, token: str = Query("")):
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    ok = agent_registry.remove_slot(name)
    if not ok:
        return JSONResponse({"ok": False, "error": f"No existe el asistente '{name}'."}, status_code=404)
    return {"ok": True, "slots": agent_registry.list_slots_status(),
            "active": agent_registry.get_active_slot_name()}


@router.post("/api/agents/slots/active")
async def api_agents_slots_activate(request: Request, token: str = Query("")):
    """Cambia el asistente ACTIVO (solo cambia el enrutamiento por defecto)."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    body = await request.json()
    name = str(body.get("name", "") or "").strip()
    if not agent_registry.set_active_slot_name(name):
        return JSONResponse({"ok": False, "error": f"No existe el asistente '{name}'."}, status_code=404)
    slot = agent_registry.get_slot(name)
    if slot:
        agent_registry.set_selected_id(slot["adapter"])
    try:
        await enviar_todos({"tipo": "agent_slot_active",
                            "slot": agent_registry.slot_status(slot) if slot else None})
    except Exception:
        pass
    return {"ok": True, "active": name, "slots": agent_registry.list_slots_status()}


@router.post("/api/agents/slots/{name}/test")
async def api_agents_slots_test(name: str, request: Request, token: str = Query("")):
    """Prueba REAL del slot: ejecuta un prompt de eco con su adaptador."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    ret, out = await agent_registry.run_slot(name, "Responde exactamente: ENLACE_OK", timeout=60)
    return {"ok": ret == 0, "retcode": ret, "salida": out[:2000]}


@router.post("/api/agents/connect")
async def api_agents_connect(request: Request, token: str = Query("")):
    """Conecta el asistente elegido (cualquier marca). Sin claves, sin nube."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    return await _conectar_asistente(body)


async def _conectar_asistente(body: dict) -> dict:
    """Núcleo del conector universal de asistentes (cualquier marca, multi-slot).

    Crea o actualiza el slot con el nombre dado y lo deja como activo.
    """
    import agents as agent_registry
    try:
        adapter_id = str(body.get("adapter", "") or "").strip()
        name = str(body.get("name", "Mi Asistente")).strip() or "Mi Asistente"
        role = str(body.get("role", "Creador Autónomo")).strip()
        model = str(body.get("model", "auto")).strip()
        endpoint = str(body.get("endpoint", ""))
        command = str(body.get("command", "") or "").strip()

        # Valida el adaptador (o el comando si es personalizado).
        if adapter_id == "custom":
            if not command:
                return JSONResponse({"ok": False, "error": "Configura el comando de tu CLI personalizado."}, status_code=400)
            adapter = agent_registry.GenericCLIAdapter(name=name, command=command)
        else:
            adapter = agent_registry.get_adapter(adapter_id) if adapter_id else None
        if not adapter:
            sel = agent_registry.get_selected_id()
            adapter = agent_registry.get_adapter(sel) if sel else None
            if not adapter:
                return JSONResponse({"ok": False, "error": "Ningún asistente disponible en esta máquina."}, status_code=400)
            adapter_id = sel
        info = adapter.to_info()
        # Conexión tolerante: el slot se registra aunque el binario no esté
        # presente ahora mismo; el estado (detectado/no) se muestra con
        # honestidad en /api/agents/slots y en el momento de ejecutar.

        # Slot: identidad propia en el bus + adaptador. Lo deja activo.
        slot = agent_registry.save_slot(
            name, adapter_id, command=command, model=model,
            base_url=str(body.get("base_url", "") or "").strip(),
            api_key=str(body.get("api_key", "") or "").strip())
        agent_registry.set_active_slot_name(name)
        agent_registry.set_selected_id(adapter_id)

        agent_registry.set_selected_id(adapter_id)
        ai_engine.guardar_config_ia(adapter_id, model)

        label = getattr(adapter, "display_label", adapter.label)
        state.HERMES_STATE["my_agent"].update({
            "name": name,
            "role": role,
            "engine": "local",
            "adapter": adapter_id,
            "provider": adapter_id,
            "model": model or info.get("default_model", "") or "auto",
            "endpoint": endpoint,
            "status": "connected",
            "model_agnostic": True,
            "capabilities": ["terminal_pty", "bash", "filesystem", "git", "antigravity_skills", "dual_pairing"],
        })

        try:
            iniciar_hermes_worker()
        except Exception:
            pass

        try:
            await enviar_todos({"tipo": "hermes_status", "state": state.HERMES_STATE})
        except Exception as e:
            print(f"[AgentsConnect] Aviso en broadcast: {e}")

        return {
            "ok": True,
            "message": f"Asistente {name} activo ({label}). Sin claves, sin nube: toda la IA del estudio es tu propio asistente.",
            "agent": state.HERMES_STATE["my_agent"],
            "adapter": info,
            "slot": slot,
            "slots": agent_registry.list_slots_status(),
            "state": state.HERMES_STATE,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Error conectando asistente: {e}"}, status_code=500)


@router.post("/api/agents/custom")
async def api_agents_custom_save(request: Request, token: str = Query("")):
    """Guarda un CLI personalizado (por máquina, en ~/.atlantis/config.json)."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    body = await request.json()
    name = str(body.get("name", "") or "").strip()
    command = str(body.get("command", "") or "").strip()
    if not command:
        return JSONResponse({"ok": False, "error": "El comando no puede estar vacío."}, status_code=400)
    entry = agent_registry.save_custom_agent(name, command)
    return {"ok": True, "custom": entry, "adapters": agent_registry.detect_all()}


@router.post("/api/agents/custom/test")
async def api_agents_custom_test(request: Request, token: str = Query("")):
    """Prueba REAL del CLI personalizado: ejecuta un prompt de eco y devuelve la salida."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    body = await request.json()
    command = str(body.get("command", "") or "").strip()
    if not command:
        return JSONResponse({"ok": False, "error": "El comando no puede estar vacío."}, status_code=400)
    adapter = agent_registry.GenericCLIAdapter(name="Prueba", command=command, timeout=30)
    ret, out = await adapter.arun("Responde exactamente: ENLACE_OK", timeout=30)
    return {"ok": ret == 0, "retcode": ret, "salida": out[:2000]}


@router.post("/api/agents/openai/test")
async def api_agents_openai_test(request: Request, token: str = Query("")):
    """Prueba REAL de un endpoint OpenAI-compatible: GET /models de verdad."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    body = await request.json()
    base_url = str(body.get("base_url", "") or "").strip()
    api_key = str(body.get("api_key", "") or "").strip()
    if not base_url:
        return JSONResponse({"ok": False, "error": "Falta la URL base del endpoint."}, status_code=400)
    return agent_registry.test_connection(base_url, api_key)


@router.delete("/api/agents/custom")
async def api_agents_custom_delete(request: Request, token: str = Query("")):
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    import agents as agent_registry
    body = await request.json()
    name = str(body.get("name", "") or "").strip()
    ok = agent_registry.remove_custom_agent(name)
    return {"ok": ok}


@router.post("/api/hermes/connect")
async def api_hermes_connect(request: Request, token: str = Query("")):
    """Compatibilidad: el camino Hermes clásico delega en el conector universal con detección inteligente."""
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede hacer esto."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    hermes_dir = config.HOME / ".hermes"
    agent_name = "Koko"
    model_name = "gemini-3-flash-preview"

    cfg_yaml = hermes_dir / "config.yaml"
    if cfg_yaml.exists():
        try:
            import yaml
            cfg_data = yaml.safe_load(cfg_yaml.read_text(encoding="utf-8"))
            if isinstance(cfg_data, dict):
                ag = cfg_data.get("agent", {})
                if isinstance(ag, dict) and ag.get("name"):
                    agent_name = ag["name"]
                m = cfg_data.get("model", {})
                if isinstance(m, dict):
                    model_name = m.get("default", model_name)
        except Exception:
            pass

    body.setdefault("adapter", "hermes")
    body.setdefault("name", body.get("name") or agent_name)
    body.setdefault("model", body.get("model") or model_name)
    res = await _conectar_asistente(body)
    try:
        iniciar_hermes_worker()
    except Exception:
        pass
    return res


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(""), nombre: str = Query("")):
    if not es_local(ws) and not token_ok(token):
        await ws.close(code=4401)
        return

    await ws.accept()
    nombre_usuario = (nombre or config.CFG.get("nombre", "dev")).strip()
    state.CONECTADOS.add(ws)
    mis_salas: set[Sala] = set()

    # Presencia en la matriz social
    entidad_id = "daniel" if "daniel" in nombre_usuario.lower() else ("amigo" if "amigo" in nombre_usuario.lower() else None)
    if entidad_id:
        social_matrix.actualizar_presencia(entidad_id, "online", "En vivo en NEXO Studio")

    await enviar_todos({
        "tipo": "presencia",
        "conectados": len(state.CONECTADOS),
        "agentes": get_store().get("agentes", {}),
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
                                # Identidad del agente que habla (nombre +
                                # color): nunca confundido con un humano.
                                "agente": {"nombre": agente_target,
                                           "color": _color_para(agente_target)},
                            })
                            # Auto-escritura si se solicitó explícitamente modificar el archivo
                            if cod_ia and nombre_archivo and any(k in texto_lower for k in ("escribe", "modifica", "actualiza", "guarda", "reemplaza")):
                                try:
                                    version_antes = state._version_archivo(sala.nombre, nombre_archivo)
                                    p = ruta_segura(sala, nombre_archivo)
                                    p.write_text(cod_ia, encoding="utf-8")
                                    nueva_version = _bump_version_archivo(sala.nombre, nombre_archivo, agente_target)
                                    aviso = f"💾 {agente_target} aplicó los cambios automáticamente en `{nombre_archivo}`."
                                    sala.guardar_sistema(aviso, f_ia)
                                    await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre, "texto": aviso, "fecha": f_ia})
                                    await difundir_arbol()
                                    # Sync entre servidores: el cambio de la IA también viaja.
                                    await sync_mod.notificar_cambio_local(
                                        sala.nombre, nombre_archivo, base_version=version_antes,
                                        version=nueva_version, autor=agente_target,
                                        contenido=cod_ia, operacion="upsert")
                                except Exception as err:
                                    print(f"Error escribiendo archivo por IA: {err}")
                        asyncio.create_task(async_ia_chat())

            elif tipo == "escribiendo":
                sala = sala_de(msg.get("proyecto", "general"))
                await enviar_sala(sala, {
                    "tipo": "escribiendo", "proyecto": sala.nombre,
                    "de": nombre_usuario,
                }, excepto=ws)

            elif tipo == "presence":
                # Presencia colaborativa: quién está, en qué archivo,
                # dónde está su cursor y qué tiene seleccionado.
                state.PRESENCIA[ws] = {
                    "nombre": nombre_usuario,
                    "file": str(msg.get("file", "")),
                    "cursor": msg.get("cursor") or {},
                    "selection": msg.get("selection") or {},
                    "color": _color_para(nombre_usuario),
                }
                await _difundir_presencia()

            elif tipo == "file_lock":
                ruta_lock = msg.get("ruta", "")
                accion_lock = msg.get("accion", "lock")
                if accion_lock == "lock":
                    state.FILE_LOCKS[ruta_lock] = {"usuario": nombre_usuario, "ts": time.time()}
                else:
                    state.FILE_LOCKS.pop(ruta_lock, None)
                await enviar_todos({"tipo": "file_locks", "locks": state.FILE_LOCKS})

            elif tipo == "archivo_escribir":
                sala = sala_de(msg.get("proyecto", "general"))
                try:
                    ruta = msg.get("ruta", "")
                    contenido = msg.get("contenido", "")
                    version_antes = state._version_archivo(sala.nombre, ruta)
                    p = ruta_segura(sala, ruta)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(contenido, encoding="utf-8")
                    nueva_version = _bump_version_archivo(sala.nombre, ruta, nombre_usuario)
                    sala.guardar_sistema(f"{nombre_usuario} guardó {ruta}", fecha)
                    await enviar_sala(sala, {
                        "tipo": "sistema", "proyecto": sala.nombre,
                        "texto": f"💾 {nombre_usuario} guardó {ruta}", "fecha": fecha,
                    })
                    await enviar(ws, {"tipo": "operacion_ok", "accion": "escribir",
                                      "ruta": ruta, "version": nueva_version})
                    # Sincronización en vivo para los demás colaboradores
                    await enviar_todos({
                        "tipo": "file_changed",
                        "proyecto": sala.nombre,
                        "ruta": ruta,
                        "version": nueva_version,
                        "autor": nombre_usuario,
                        "size": len(contenido),
                        "contenido": contenido if len(contenido) <= 65536 else None,
                    })
                    await difundir_arbol()
                    # Sync entre servidores: este guardado viaja a los peers.
                    await sync_mod.notificar_cambio_local(
                        sala.nombre, ruta, base_version=version_antes,
                        version=nueva_version, autor=nombre_usuario,
                        contenido=contenido, operacion="upsert")
                except Exception as e:
                    await enviar(ws, {"tipo": "error", "texto": str(e)})

            elif tipo == "archivo_borrar":
                sala = sala_de(msg.get("proyecto", "general"))
                try:
                    ruta = msg.get("ruta", "")
                    borrados = sync_mod.colectar_borrado(sala.nombre, ruta)
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
                    for ruta_borrada, version_borrada in borrados:
                        await sync_mod.notificar_cambio_local(
                            sala.nombre, ruta_borrada, base_version=version_borrada,
                            version=version_borrada, autor=nombre_usuario,
                            contenido=None, operacion="delete")
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
                    c_dir = Path(s_obj.directorio) if (s_obj and Path(s_obj.directorio).is_dir()) else config.WORKSPACE
                    await enviar_todos({"tipo": "hermes_stream_start", "prompt": p_text, "proyecto": p_proy})
                    def _hk(chunk: str):
                        asyncio.create_task(enviar_todos({"tipo": "hermes_stream_chunk", "chunk": chunk, "proyecto": p_proy}))
                    async def _run_h():
                        ret, sal = await ai_engine.ejecutar_hermes(p_text, cwd=c_dir, yolo=True, on_chunk=_hk)
                        await enviar_todos({"tipo": "hermes_stream_end", "retcode": ret, "salida": sal, "proyecto": p_proy})
                        await difundir_arbol()
                    asyncio.create_task(_run_h())

            elif tipo == "dual_run":
                obj_text = msg.get("objetivo", "").strip()
                if obj_text:
                    p_proy = msg.get("proyecto", "")
                    s_obj = sala_de(p_proy) if p_proy else None
                    c_dir = Path(s_obj.directorio) if (s_obj and Path(s_obj.directorio).is_dir()) else config.WORKSPACE
                    def _ev_cb(ev: dict):
                        asyncio.create_task(enviar_todos(ev))
                    async def _run_d():
                        res = await ai_engine.colaboracion_dual(
                            obj_text, cwd=c_dir, on_event=_ev_cb, bus=_BusAdapter())
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
                                nuevo_p["id"], s_cont, codigo=s_cod, archivo=s_arch, cwd=config.WORKSPACE, on_evento=_on_deb_ev
                            )
                        asyncio.create_task(_run_deb_ws())
                    else:
                        async def _run_menc_ws():
                            def _on_menc_rep(ev):
                                asyncio.create_task(enviar_todos(ev))
                            await social_matrix.procesar_menciones_feed(
                                nuevo_p["id"], s_cont, codigo=s_cod, archivo=s_arch, cwd=config.WORKSPACE, on_ia_reply=_on_menc_rep
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
                                s_pid, s_cont, codigo=s_cod, cwd=config.WORKSPACE, on_ia_reply=_on_menc_ev
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
                            s_pid, s_tema, codigo=s_cod, archivo=s_arch, cwd=config.WORKSPACE, on_evento=_on_ev
                        )
                    asyncio.create_task(_run_manual_deb())

            elif tipo == "social_presence":
                s_ent = msg.get("entidad")
                s_est = msg.get("estado", "online")
                s_act = msg.get("actividad", "")
                if s_ent:
                    pres_act = social_matrix.actualizar_presencia(s_ent, s_est, s_act)
                    await enviar_todos({"tipo": "social_presence_update", "presencia": pres_act})

            # --- sync entre servidores (co-edición en vivo, por proyecto) ---
            elif tipo == "peer_join":
                # Un servidor local se registra como peer de sync del
                # equipo de UN proyecto (carpeta). El guest_token manda:
                # solo vale para su equipo; un token de otro equipo se
                # rechaza (4403). La carpeta del peer_join debe ser la
                # del equipo, o se rechaza.
                ok, motivo = sync_mod.validar_mensaje_sync(msg)
                if not ok:
                    await enviar(ws, {"tipo": "error", "texto": f"peer_join inválido: {motivo}"})
                    await ws.close(code=4403)
                    continue
                equipo = sync_mod.equipo_por_id(msg.get("equipo_id", ""))
                if equipo is None:
                    await enviar(ws, {"tipo": "error", "texto": "equipo inexistente"})
                    await ws.close(code=4403)
                    continue
                if (equipo.get("proyecto") != msg.get("proyecto")
                        or equipo.get("carpeta") != msg.get("carpeta")):
                    await enviar(ws, {"tipo": "error", "texto": "el equipo no es de esa carpeta"})
                    await ws.close(code=4403)
                    continue
                ok_equipo, motivo_equipo = sync_mod.validar_peer_join(
                    token, msg.get("equipo_id"),
                    es_local_conn=config.es_local(ws),
                    public_mode=config.PUBLIC_MODE)
                if not ok_equipo:
                    await enviar(ws, {"tipo": "error", "texto": motivo_equipo})
                    await ws.close(code=4403)
                    continue
                sync_mod.registrar_peer(ws, msg["peer_id"], msg["proyecto"],
                                        msg["carpeta"], msg["equipo_id"],
                                        str(msg.get("autor") or nombre_usuario))
                await enviar(ws, {"tipo": "peer_ok", "proyecto": msg["proyecto"],
                                  "carpeta": msg["carpeta"],
                                  "equipo_id": msg["equipo_id"],
                                  "peer_id": msg["peer_id"],
                                  "miembros": sync_mod.miembros_publicos(equipo)})
            elif tipo in ("file_sync", "sync_request", "sync_state", "sync_fetch", "agent_msg", "file_big"):
                # Relay: reenvía SOLO entre peers del MISMO equipo. Nunca
                # persiste contenido. Solo peers registrados reciben.
                # Cada mensaje debe coincidir con el equipo del peer y
                # llevar sus rutas dentro de la carpeta del proyecto.
                # agent_msg es el bus de agentes del equipo (los agentes
                # de un proyecto trabajan en conjunto, sin cruzar a otros).
                # file_big es la transferencia completa de un archivo
                # grande/binario en un solo mensaje (pass-through, como todo).
                if not sync_mod.es_peer(ws):
                    await enviar(ws, {"tipo": "error", "texto": "no eres peer de sync"})
                    continue
                registro = sync_mod.PEERS[ws]
                if (msg.get("proyecto") != registro.get("proyecto")
                        or msg.get("carpeta") != registro.get("carpeta")
                        or msg.get("equipo_id") != registro.get("equipo_id")):
                    await enviar(ws, {"tipo": "error", "texto": "mensaje fuera de tu equipo"})
                    continue
                ok, motivo = sync_mod.validar_mensaje_sync(msg)
                if not ok:
                    await enviar(ws, {"tipo": "error", "texto": f"sync inválido: {motivo}"})
                    continue
                if tipo == "sync_fetch":
                    await sync_mod.reenviar_fetch(msg, msg["equipo_id"], excepto=ws)
                else:
                    await sync_mod.reenviar_a_peers(msg, msg["equipo_id"], excepto=ws)

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        state.CONECTADOS.discard(ws)
        sync_mod.olvidar_peer(ws)
        for s in mis_salas:
            s.conectados.discard(ws)
        locks_del = [k for k, v in state.FILE_LOCKS.items() if v.get("usuario") == nombre_usuario]
        for k in locks_del:
            state.FILE_LOCKS.pop(k, None)
        salio = state.PRESENCIA.pop(ws, None)
        await enviar_todos({
            "tipo": "presencia",
            "conectados": len(state.CONECTADOS),
            "file_locks": state.FILE_LOCKS,
        })
        if salio:
            await _difundir_presencia()
