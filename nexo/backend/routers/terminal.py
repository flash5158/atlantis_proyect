"""Router terminal y PTY — movido intacto desde server.py (NEXO 2.0).

Comportamiento idéntico: mismas rutas, métodos, formas de request/response
y códigos de estado. Solo cambió el andamiaje (package backend/).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess

try:
    import fcntl
    import pty
    import struct
    import termios
    HAS_PTY = True
except ImportError:
    HAS_PTY = False
from pathlib import Path

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from backend.core.config import config, es_host, es_local, token_ok
from backend.core.state import (
    entorno_limpio,
)

router = APIRouter()

@router.post("/api/terminal/exec")
async def api_terminal_exec(request: Request, token: str = Query("")):
    # Solo el dueño: una shell en su máquina no es algo delegable.
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede ejecutar comandos."}, status_code=403)
    try:
        body = await request.json()
        cmd = body.get("cmd", "").strip()
        if not cmd:
            return JSONResponse({"error": "comando vacío"}, status_code=400)

        # Verify that CURRENT_TERMINAL_CWD exists, fallback to WORKSPACE
        if not config.CURRENT_TERMINAL_CWD.is_dir():
            config.CURRENT_TERMINAL_CWD = config.WORKSPACE

        if body.get("reset_cwd"):
            config.CURRENT_TERMINAL_CWD = config.WORKSPACE
        elif body.get("target_cwd"):
            # target_cwd explícito: debe quedar dentro del workspace activo.
            # (El `cd` dentro del comando sigue funcionando vía el sentinel
            # $PWD como antes; solo se valida el parámetro.)
            cand = Path(body["target_cwd"]).resolve()
            base = config.WORKSPACE.resolve()
            if cand.is_dir() and (cand == base or base in cand.parents):
                config.CURRENT_TERMINAL_CWD = cand
            else:
                return JSONResponse(
                    {"error": "target_cwd fuera del workspace"}, status_code=400)

        start_cwd = config.CURRENT_TERMINAL_CWD

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
            new_cwd = str(config.CURRENT_TERMINAL_CWD)

            if "\n__ATL_CWD__:" in stdout_raw:
                parts = stdout_raw.rsplit("\n__ATL_CWD__:", 1)
                clean_stdout = parts[0]
                extracted_path = parts[1].splitlines()[0].strip()
                if extracted_path:
                    cand = Path(extracted_path).resolve()
                    if cand.is_dir():
                        config.CURRENT_TERMINAL_CWD = cand
                        new_cwd = str(config.CURRENT_TERMINAL_CWD)
            elif stdout_raw.startswith("__ATL_CWD__:"):
                parts = stdout_raw.split("__ATL_CWD__:", 1)
                clean_stdout = ""
                extracted_path = parts[1].splitlines()[0].strip()
                if extracted_path:
                    cand = Path(extracted_path).resolve()
                    if cand.is_dir():
                        config.CURRENT_TERMINAL_CWD = cand
                        new_cwd = str(config.CURRENT_TERMINAL_CWD)

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
        except TimeoutError:
            proc.kill()
            return {"ok": False, "error": "Tiempo de espera agotado (40s)", "exit_code": -1, "cwd": str(config.CURRENT_TERMINAL_CWD)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/terminal/complete")
async def api_terminal_complete(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        query = body.get("query", "").strip()
        base_dir = config.CURRENT_TERMINAL_CWD if config.CURRENT_TERMINAL_CWD.is_dir() else config.WORKSPACE
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


@router.websocket("/pty")
async def pty_endpoint(
    ws: WebSocket,
    token: str = Query(""),
    cols: int = Query(80),
    rows: int = Query(24),
    proyecto: str = Query(""),
):
    # Solo el dueño: un PTY interactivo es una shell completa en su máquina.
    if not es_host(ws, token):
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
    cwd_dir = (config.PROYECTOS / proyecto).resolve() if proyecto and (config.PROYECTOS / proyecto).is_dir() else config.WORKSPACE

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
