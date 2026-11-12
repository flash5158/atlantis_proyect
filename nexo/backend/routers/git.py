"""Router git — movido intacto desde server.py (NEXO 2.0).

Comportamiento idéntico: mismas rutas, métodos, formas de request/response
y códigos de estado. Solo cambió el andamiaje (package backend/).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from backend.core.config import config, es_local, token_ok

router = APIRouter()

@router.get("/api/git/status")
async def api_git_status(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain", "-b",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(config.WORKSPACE)
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


@router.post("/api/git/commit")
async def api_git_commit(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        msg = body.get("mensaje", "chore: actualizacion desde Atlantis Studio").strip()
        p1 = await asyncio.create_subprocess_exec("git", "add", "-A", cwd=str(config.WORKSPACE))
        await p1.wait()
        p2 = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", msg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(config.WORKSPACE)
        )
        out, err = await p2.communicate()
        return {"ok": p2.returncode == 0, "salida": (out or err).decode("utf-8", errors="replace")}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
