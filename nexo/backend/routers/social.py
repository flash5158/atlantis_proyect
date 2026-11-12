"""Router matriz social — movido intacto desde server.py (NEXO 2.0).

Comportamiento idéntico: mismas rutas, métodos, formas de request/response
y códigos de estado. Solo cambió el andamiaje (package backend/).
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import social_matrix
from backend.core.config import config, es_local, token_ok
from backend.core.state import (
    difundir_arbol,
    enviar_todos,
)

router = APIRouter()

@router.get("/api/social/feed")
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


@router.get("/api/social/entities")
async def api_social_entities(request: Request, token: str = Query("")):
    return {
        "entidades": social_matrix.ENTIDADES,
        "presencia": social_matrix.ESTADO_PRESENCIA,
    }


@router.post("/api/social/post")
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
                cwd=config.WORKSPACE,
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
                cwd=config.WORKSPACE,
                on_ia_reply=_on_ia_rep,
            )
        asyncio.create_task(_run_menciones())

    return {"ok": True, "post": nuevo_post}


@router.post("/api/social/reply")
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
            cwd=config.WORKSPACE,
            on_ia_reply=_on_ia_rep,
        )
    asyncio.create_task(_run_menciones_rep())

    return {"ok": True, "respuesta": resp}


@router.post("/api/social/react")
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


@router.post("/api/social/debate")
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
            cwd=config.WORKSPACE,
            on_evento=_on_ev,
        )
    asyncio.create_task(_run_debate())
    return {"ok": True, "mensaje": "Debate IA iniciado"}


@router.post("/api/social/apply_code")
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

    res = social_matrix.aplicar_codigo_a_archivo(ruta, codigo, cwd=config.WORKSPACE)
    if res.get("ok"):
        await difundir_arbol()
        await enviar_todos({
            "tipo": "sistema",
            "texto": f"💾 Código aplicado a `{ruta}` ({res.get('bytes', 0)} bytes).",
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return res
