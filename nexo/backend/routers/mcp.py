"""Router MCP Builder — movido intacto desde server.py (NEXO 2.0).

Comportamiento idéntico: mismas rutas, métodos, formas de request/response
y códigos de estado. Solo cambió el andamiaje (package backend/).
"""
from __future__ import annotations


from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import mcp_manager
from backend.core.config import es_host, es_local, token_ok
from backend.core.state import (
    difundir_arbol,
)

router = APIRouter()

@router.get("/api/mcps")
async def api_mcps_list(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {"ok": True, "mcps": mcp_manager.list_mcps(),
            "mcp_available": mcp_manager.MCP_AVAILABLE,
            "mcp_error": mcp_manager.MCP_IMPORT_ERROR}


@router.post("/api/mcps")
async def api_mcps_create(request: Request, token: str = Query("")):
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede gestionar servidores MCP."}, status_code=403)
    try:
        body = await request.json()
        manifest = mcp_manager.create_mcp(
            name=body.get("name", ""),
            description=body.get("description", ""),
            tools=body.get("tools", []) or [],
        )
        await difundir_arbol()
        return {"ok": True, "mcp": manifest}
    except (ValueError, RuntimeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Error creando MCP: {e}"}, status_code=500)


@router.delete("/api/mcps/{name}")
async def api_mcps_delete(name: str, request: Request, token: str = Query("")):
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede gestionar servidores MCP."}, status_code=403)
    try:
        res = mcp_manager.delete_mcp(name)
        await difundir_arbol()
        return res
    except (ValueError, RuntimeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/api/mcps/{name}/start")
async def api_mcps_start(name: str, request: Request, token: str = Query("")):
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede gestionar servidores MCP."}, status_code=403)
    try:
        return mcp_manager.start_mcp(name)
    except (ValueError, RuntimeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/api/mcps/{name}/stop")
async def api_mcps_stop(name: str, request: Request, token: str = Query("")):
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede gestionar servidores MCP."}, status_code=403)
    return mcp_manager.stop_mcp(name)


@router.get("/api/mcps/{name}/logs")
async def api_mcps_logs(name: str, request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {"ok": True, "logs": mcp_manager.get_logs(name)}


@router.post("/api/mcps/{name}/tools")
async def api_mcps_tools(name: str, request: Request, token: str = Query("")):
    """list_tools REAL contra el servidor MCP generado (stdio)."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        tools = await mcp_manager.list_tools_live(name)
        return {"ok": True, "tools": tools}
    except (ValueError, RuntimeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Error listando herramientas: {e}"}, status_code=500)


@router.post("/api/mcps/{name}/call")
async def api_mcps_call(name: str, request: Request, token: str = Query("")):
    """call_tool REAL contra el servidor MCP generado (stdio)."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        res = await mcp_manager.call_tool_live(
            name, str(body.get("tool", "")), body.get("args") or {})
        return {"ok": True, **res}
    except (ValueError, RuntimeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Error llamando herramienta: {e}"}, status_code=500)
