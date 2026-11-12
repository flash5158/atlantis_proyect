"""Router estado y capacidades del estudio — movido intacto desde server.py (NEXO 2.0).

Comportamiento idéntico: mismas rutas, métodos, formas de request/response
y códigos de estado. Solo cambió el andamiaje (package backend/).
"""
from __future__ import annotations

import asyncio
import platform
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

import ai_engine
import antigravity_engine
from backend.core import state
from backend.core.config import config, es_host, es_local, token_ok
from backend.core.state import (
    _arbol,
    difundir_arbol,
    enviar_todos,
    sala_de,
)

router = APIRouter()

# NEXO_NO_WEB_UI=1: sin interfaz web del IDE. GET / muestra solo la
# página de descarga; sin JS, sin assets del IDE, sin QR.
_PAGINA_DESCARGA = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Atlantis Studio</title></head>
<body>
<h1>Atlantis Studio — download the desktop app</h1>
<p><a href="https://atlantis-studio.netlify.app/">Download Atlantis Studio</a></p>
<p>Open the app to join a project with a code.</p>
</body>
</html>
"""


@router.get("/")
async def raiz():
    if config.NO_WEB_UI:
        return HTMLResponse(_PAGINA_DESCARGA)
    return FileResponse(config.STATIC_DIR / "index.html")


@router.get("/api/token_local")
async def api_token_local(request: Request):
    """Bootstrap for local sessions and anonymous public deployments."""
    if config.PUBLIC_MODE:
        return {"token": "public", "local": False}
    if es_local(request):
        return {"token": config.TOKEN, "local": True}
    return JSONResponse({"error": "No permitido"}, status_code=403)


@router.get("/api/status")
async def api_status(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    cfg_ia = ai_engine.obtener_config_ia()
    return {
        "sistema": "NEXO 2.0 Atlantis",
        "os": platform.system(),
        "arch": platform.machine(),
        "workspace": str(config.WORKSPACE),
        "terminal_cwd": str(config.CURRENT_TERMINAL_CWD),
        "proyectos": len(_arbol()),
        "conectados": len(state.CONECTADOS),
        "ai_engine": {
            "proveedor": cfg_ia.get("proveedor"),
            "modelo": cfg_ia.get("modelo"),
            "hermes_disponible": cfg_ia.get("hermes_disponible"),
        },
        "config": {
            "nombre": config.CFG.get("nombre"),
            "companero": config.CFG.get("companero"),
            "ia_nombre": config.CFG.get("ia_nombre"),
            "ia_companero": config.CFG.get("ia_companero"),
            "tema": config.CFG.get("tema"),
        },
    }


@router.get("/api/ai/config")
async def api_get_ai_config(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    cfg = ai_engine.obtener_config_ia()
    return {
        "ok": True,
        "proveedor": cfg.get("proveedor", "hermes"),
        "modelo": cfg.get("modelo", "auto"),
        "hermes_disponible": cfg.get("hermes_disponible", False),
        "agente_conectado": state.HERMES_STATE.get("my_agent", {}).get("status") == "connected",
        "nota": "Atlantis Studio no usa API keys: la IA es tu Hermes conectado.",
    }


@router.post("/api/ai/config")
async def api_set_ai_config(req: Request, token: str = Query("")):
    # La configuración por claves se eliminó: no hay nada que configurar.
    return JSONResponse(
        {"ok": False, "error": "gone",
         "detalle": "Atlantis Studio no usa API keys. Conecta tu Hermes en el asistente de inicio."},
        status_code=410,
    )


@router.get("/api/ai/status")
async def api_ai_status(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    status = await ai_engine.verificar_estado_ia()
    return status


@router.get("/api/antigravity/overview")
async def api_antigravity_overview(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
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


@router.get("/api/antigravity/sources")
async def api_antigravity_sources(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    return {"ok": True, "sources": engine.discover_sources()}


@router.post("/api/antigravity/sources/add")
async def api_antigravity_sources_add(req: Request, token: str = Query("")):
    # Solo el dueño: agregar rutas arbitrarias del disco como fuentes
    # de plugins/skills es administración de la máquina.
    if not es_host(req, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede agregar fuentes."}, status_code=403)
    body = await req.json()
    path_str = body.get("path", "").strip()
    if not path_str:
        return JSONResponse({"error": "ruta requerida"}, status_code=400)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    ok = engine.add_custom_source(path_str)
    if not ok:
        return JSONResponse({"error": f"La ruta '{path_str}' no existe"}, status_code=400)
    return {"ok": True, "sources": engine.discover_sources()}


@router.post("/api/antigravity/sources/remove")
async def api_antigravity_sources_remove(req: Request, token: str = Query("")):
    if not es_host(req, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede quitar fuentes."}, status_code=403)
    body = await req.json()
    path_str = body.get("path", "").strip()
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    engine.remove_custom_source(path_str)
    return {"ok": True, "sources": engine.discover_sources()}


@router.get("/api/antigravity/plugins")
async def api_antigravity_plugins(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    return {"ok": True, "plugins": engine.discover_plugins()}


@router.post("/api/antigravity/plugins/toggle")
async def api_antigravity_plugin_toggle(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    plug_id = body.get("id")
    enabled = body.get("enabled", True)
    if not plug_id:
        return JSONResponse({"error": "id requerido"}, status_code=400)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    engine.toggle_item(plug_id, enabled)
    return {"ok": True, "id": plug_id, "enabled": enabled}


@router.post("/api/antigravity/plugins/create")
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
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    res = engine.install_plugin(plug_id, name, desc, version)
    await difundir_arbol()
    return {"ok": True, "plugin": res}


@router.get("/api/antigravity/skills")
async def api_antigravity_skills(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    return {"ok": True, "skills": engine.discover_skills()}


@router.get("/api/antigravity/skills/content")
async def api_antigravity_skill_content(request: Request, id: str = Query(""), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    skills = engine.discover_skills()
    found = next((s for s in skills if s["id"] == id), None)
    if not found:
        return JSONResponse({"error": "Skill no encontrado"}, status_code=404)
    skill_file = Path(found["skill_file"])
    content = skill_file.read_text(encoding="utf-8", errors="replace") if skill_file.is_file() else ""
    return {"ok": True, "skill": found, "content": content}


@router.post("/api/antigravity/skills/toggle")
async def api_antigravity_skill_toggle(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    skill_id = body.get("id")
    enabled = body.get("enabled", True)
    if not skill_id:
        return JSONResponse({"error": "id requerido"}, status_code=400)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    engine.toggle_item(skill_id, enabled)
    return {"ok": True, "id": skill_id, "enabled": enabled}


@router.post("/api/antigravity/skills/create")
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
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    res = engine.install_skill(skill_id, name, desc, instructions)
    await difundir_arbol()
    return {"ok": True, "skill": res}


@router.post("/api/antigravity/skills/run")
async def api_antigravity_skill_run(req: Request, token: str = Query("")):
    if not es_local(req) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    body = await req.json()
    skill_id = body.get("id", "")
    custom_prompt = body.get("prompt", "").strip()
    proyecto = body.get("proyecto", "general")
    sala = sala_de(proyecto) if proyecto else None
    cwd_dir = Path(sala.directorio) if (sala and Path(sala.directorio).is_dir()) else config.WORKSPACE

    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
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


@router.get("/api/antigravity/rules")
async def api_antigravity_rules(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    return {"ok": True, "rules": engine.discover_rules()}


@router.get("/api/antigravity/mcp")
async def api_antigravity_mcp(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    engine = antigravity_engine.get_antigravity_engine(config.WORKSPACE)
    return {"ok": True, "mcp": engine.discover_mcp()}
