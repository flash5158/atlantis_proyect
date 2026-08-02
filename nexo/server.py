#!/usr/bin/env python3
"""NEXO — espacio de trabajo colaborativo para dos agentes Hermes.

Servidor local (FastAPI + WebSocket):
  - Árbol de proyectos con sus archivos
  - Chat compartido por proyecto, persistido en .nexo-chat.md
  - Ejecución de comandos con salida en streaming a la sala
  - Lectura/escritura/borrado/renombrado/subida/descarga de archivos
  - Indicador de "escribiendo" y presencia en vivo
  - Git/GitHub (status/add/commit/push/pull/log/branch/remote/diff/stash)

Arranque:  .venv/bin/python server.py  ->  http://127.0.0.1:8787
"""

import asyncio
import base64
import hmac
import json
import os
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------- config
HOME = Path.home()
BASE = Path(__file__).resolve().parent
WORKSPACE = BASE / "colab-hub" if (BASE / "colab-hub").exists() else HOME / "colab-hub"
if not WORKSPACE.exists():
    WORKSPACE = HOME / "nexo-workspace"
PROYECTOS = WORKSPACE / "proyectos"
CONFIG = BASE / "config.json"
PORT = 8787
MAX_LECTURA = 2 * 1024 * 1024      # 2 MB por archivo abierto en el editor
MAX_SUBIDA = 20 * 1024 * 1024      # 20 MB por subida

def cargar_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    cfg = {"token": secrets.token_hex(16), "nombre": "hermes-1", "companero": "hermes-2"}
    CONFIG.write_text(json.dumps(cfg, indent=2))
    return cfg

CFG = cargar_config()
TOKEN = os.environ.get("TOKEN") or CFG["token"]
PROYECTOS.mkdir(parents=True, exist_ok=True)
if not (PROYECTOS / "README.md").exists():
    (PROYECTOS / "README.md").write_text("# Proyectos de NEXO\n\nCada carpeta es un proyecto con su chat.\n")

# ---------------------------------------------------------------- app
app = FastAPI(title="NEXO")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

# Conectados globales (para presencia) y salas
CONECTADOS: set[WebSocket] = set()
SALAS: dict[str, "Sala"] = {}


def token_ok(token: str) -> bool:
    return hmac.compare_digest(token or "", TOKEN)


# ---------------------------------------------------------------- salas
class Sala:
    """Una sala = un proyecto. Conectados, historial y procesos en curso."""

    def __init__(self, nombre: str, directorio: Path):
        self.nombre = nombre
        self.directorio = directorio
        self.conectados: set[WebSocket] = set()
        self.procesos: dict[str, asyncio.subprocess.Process] = {}
        self.chat_file = directorio / ".nexo-chat.md"

    def historial(self) -> list[dict]:
        if not self.chat_file.exists():
            return []
        msgs = []
        pat = re.compile(r"^\*\*\[(.*?)\] (.*?):\*\* (.*)$")
        for linea in self.chat_file.read_text(errors="replace").splitlines():
            m = pat.match(linea)
            if m:
                msgs.append({"fecha": m.group(1), "de": m.group(2), "texto": m.group(3)})
            elif linea.startswith("> "):
                msgs.append({"fecha": "", "de": "sistema", "texto": linea[2:]})
        return msgs

    def guardar_chat(self, de: str, texto: str, fecha: str):
        with self.chat_file.open("a", encoding="utf-8") as f:
            f.write(f"**[{fecha}] {de}:** {texto}\n")

    def guardar_sistema(self, texto: str, fecha: str):
        with self.chat_file.open("a", encoding="utf-8") as f:
            f.write(f"> {texto} ({fecha})\n")


def sala_de(nombre: str) -> Sala:
    nombre = Path(nombre or "").name  # sin rutas raras
    if not nombre:
        raise ValueError("proyecto requerido")
    if nombre not in SALAS:
        directorio = PROYECTOS / nombre
        directorio.mkdir(parents=True, exist_ok=True)
        if not (directorio / "README.md").exists():
            (directorio / "README.md").write_text(f"# {nombre}\n\nProyecto creado en NEXO.\n")
        SALAS[nombre] = Sala(nombre, directorio)
    return SALAS[nombre]


def ruta_segura(sala: Sala, ruta: str) -> Path:
    base = sala.directorio.resolve()
    p = (base / ruta).resolve()
    if p == base or base not in p.parents:
        raise ValueError(f"ruta fuera del proyecto: {ruta}")
    return p


# ---------------------------------------------------------------- helpers
async def enviar(ws: WebSocket, msg: dict):
    await ws.send_text(json.dumps(msg, ensure_ascii=False))


async def enviar_sala(sala: Sala, msg: dict, excepto: WebSocket | None = None):
    for ws in list(sala.conectados):
        if ws is excepto:
            continue
        try:
            await enviar(ws, msg)
        except Exception:  # noqa: BLE001 - socket muerto: descartar
            sala.conectados.discard(ws)


async def enviar_todos(msg: dict):
    for ws in list(CONECTADOS):
        try:
            await enviar(ws, msg)
        except Exception:  # noqa: BLE001 - socket muerto: descartar
            CONECTADOS.discard(ws)


async def difundir_arbol():
    await enviar_todos({"tipo": "arbol", "arbol": _arbol()})


def _arbol():
    arbol = []
    for p in sorted(PROYECTOS.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        archivos = []
        for f in sorted(p.rglob("*")):
            if f.is_file() and ".git" not in f.parts and f.name != ".nexo-chat.md":
                archivos.append(str(f.relative_to(p)))
        arbol.append({"nombre": p.name, "archivos": archivos})
    return arbol


async def lanzar(sala: Sala, rid: str, cmd: str, tipo: str = "run"):
    """Ejecuta un comando en el directorio del proyecto y emite la salida en streaming."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=sala.directorio,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:  # noqa: BLE001 - reportar fallo de lanzamiento
        await enviar_sala(sala, {"tipo": "output", "proyecto": sala.nombre, "id": rid,
                                 "stream": "stderr", "texto": f"no se pudo lanzar: {e}\n"})
        return
    sala.procesos[rid] = proc

    async def leer(stream, stream_name):
        while True:
            linea = await stream.readline()
            if not linea:
                break
            await enviar_sala(sala, {"tipo": "output", "proyecto": sala.nombre, "id": rid,
                                     "stream": stream_name, "texto": linea.decode(errors="replace")})
    t1 = asyncio.create_task(leer(proc.stdout, "stdout"))
    t2 = asyncio.create_task(leer(proc.stderr, "stderr"))
    code = await proc.wait()
    await t1
    await t2
    sala.procesos.pop(rid, None)
    await enviar_sala(sala, {"tipo": "run_fin", "origen": tipo, "proyecto": sala.nombre,
                             "id": rid, "code": code, "cmd": cmd})


GIT_ACCIONES = {"status", "add", "commit", "push", "pull", "log", "branch", "remote", "diff", "stash"}


def cmd_git(accion: str, msg: str | None, nombre: str) -> str:
    ident = f"-c user.name={shlex_quote(nombre)} -c user.email={shlex_quote(nombre + '@nexo.local')}"
    base = f"git -C {shlex_quote(str(WORKSPACE))} {ident}"
    if accion == "status":
        return f"{base} status --short --branch"
    if accion == "add":
        return f"{base} add -A"
    if accion == "commit":
        return f"{base} add -A && {base} commit -m {shlex_quote(msg or 'nexo: cambios')}"
    if accion == "push":
        return f"{base} push origin HEAD"
    if accion == "pull":
        return f"{base} pull --rebase"
    if accion == "log":
        return f"{base} log --oneline -15"
    if accion == "branch":
        return f"{base} branch -a"
    if accion == "remote":
        return f"{base} remote -v"
    if accion == "diff":
        return f"{base} diff --stat"
    if accion == "stash":
        return f"{base} stash"
    return f"{base} status --short --branch"


# ---------------------------------------------------------------- REST
@app.get("/")
async def raiz():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/arbol")
async def api_arbol(token: str = Query("")):
    if not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {"arbol": _arbol(), "nombre": CFG["nombre"], "companero": CFG["companero"]}


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
            texto = texto[:MAX_LECTURA] + "\n… [archivo truncado: es muy grande para el editor]"
        return {"ruta": ruta, "contenido": texto, "truncado": truncado}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001 - fallo inesperado al leer
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
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(""), nombre: str = Query("")):
    if not token_ok(token):
        await ws.close(code=4401)
        return
    await ws.accept()
    nombre = (nombre or CFG["nombre"])[:40]
    CONECTADOS.add(ws)
    await enviar_todos({"tipo": "presencia", "conectados": len(CONECTADOS)})
    mis_salas: set[Sala] = set()
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            tipo = msg.get("tipo")
            fecha = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

            if tipo == "join":
                sala = sala_de(msg.get("proyecto", "general"))
                sala.conectados.add(ws)
                mis_salas.add(sala)
                await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre,
                                         "texto": f"{nombre} se unió al proyecto", "fecha": ""}, excepto=ws)

            elif tipo == "proyecto_nuevo":
                nombre_proy = msg.get("nombre", "").strip()
                if nombre_proy:
                    n = sala_de(nombre_proy)
                    n.guardar_sistema(f"proyecto creado por {nombre}", fecha)
                    await enviar_sala(n, {"tipo": "sistema", "proyecto": n.nombre,
                                          "texto": f"proyecto creado por {nombre}", "fecha": fecha})
                    await difundir_arbol()

            elif tipo == "proyecto_borrar":
                nombre_proy = Path(msg.get("nombre", "")).name
                confirmar = msg.get("confirmar") is True
                if confirmar and nombre_proy:
                    target = (PROYECTOS / nombre_proy).resolve()
                    if PROYECTOS.resolve() in target.parents and target.is_dir():
                        shutil.rmtree(target)
                        SALAS.pop(nombre_proy, None)
                        await enviar_todos({"tipo": "sistema", "proyecto": nombre_proy,
                                            "texto": f"proyecto {nombre_proy} eliminado por {nombre}",
                                            "fecha": fecha})
                        await difundir_arbol()
                else:
                    await enviar(ws, {"tipo": "error",
                                      "texto": "proyecto_borrar requiere confirmar: true"})

            elif tipo in ("chat", "run", "stop", "git", "archivo_leer", "archivo_escribir",
                          "archivo_borrar", "archivo_renombrar", "archivo_subir", "escribiendo", "arbol"):
                sala = sala_de(msg.get("proyecto", "general"))
                sala.conectados.add(ws)
                mis_salas.add(sala)

                if tipo == "chat":
                    texto = msg.get("texto", "").strip()
                    if texto:
                        sala.guardar_chat(nombre, texto, fecha)
                        await enviar_sala(sala, {"tipo": "chat", "proyecto": sala.nombre,
                                                 "de": nombre, "texto": texto, "fecha": fecha})

                elif tipo == "escribiendo":
                    await enviar_sala(sala, {"tipo": "escribiendo", "proyecto": sala.nombre,
                                             "de": nombre}, excepto=ws)

                elif tipo == "run":
                    rid = f"{msg.get('id') or 'run'}-{secrets.token_hex(3)}"
                    cmd = msg.get("cmd", "")
                    if cmd.strip():
                        sala.guardar_sistema(f"{nombre} ejecutó: {cmd}", fecha)
                        asyncio.create_task(lanzar(sala, rid, cmd))
                        await enviar(ws, {"tipo": "run_id", "proyecto": sala.nombre, "id": rid})

                elif tipo == "stop":
                    rid = msg.get("id", "")
                    proc = sala.procesos.get(rid)
                    if not proc and rid:
                        # el id puede vivir en otra sala: buscarlo
                        for s in SALAS.values():
                            if rid in s.procesos:
                                sala = s
                                proc = s.procesos[rid]
                                break
                    if not proc and rid:
                        # puede estar registrándose: esperar hasta 1s
                        for _ in range(10):
                            await asyncio.sleep(0.1)
                            for s in SALAS.values():
                                if rid in s.procesos:
                                    sala = s
                                    proc = s.procesos[rid]
                                    break
                            if proc:
                                break
                    if not proc and rid:
                        # aún así: parar el más reciente de cualquier sala
                        for s in SALAS.values():
                            if s.procesos:
                                sala = s
                                proc = next(reversed(list(s.procesos.values())))
                                break
                    if proc:
                        try:
                            proc.kill()
                        except Exception:  # noqa: BLE001, S110 - best effort
                            pass
                        await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre,
                                                 "texto": f"{nombre} detuvo el proceso {rid}",
                                                 "fecha": fecha})

                elif tipo == "archivo_leer":
                    try:
                        p = ruta_segura(sala, msg.get("ruta", ""))
                        datos = p.read_bytes()
                        if b"\x00" in datos[:8192]:
                            raise ValueError("archivo binario: usa descargar")
                        texto = datos.decode("utf-8", errors="replace")
                        truncado = len(datos) > MAX_LECTURA
                        if truncado:
                            texto = texto[:MAX_LECTURA] + "\n… [truncado]"
                        await enviar(ws, {"tipo": "archivo", "proyecto": sala.nombre,
                                          "ruta": msg["ruta"], "contenido": texto, "truncado": truncado})
                    except (OSError, ValueError) as e:
                        await enviar(ws, {"tipo": "error", "proyecto": sala.nombre, "texto": str(e)})

                elif tipo == "archivo_escribir":
                    try:
                        ruta = msg.get("ruta", "")
                        if not ruta:
                            raise ValueError("ruta vacía")
                        p = ruta_segura(sala, ruta)
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(msg.get("contenido", ""), encoding="utf-8")
                        sala.guardar_sistema(f"{nombre} guardó {ruta}", fecha)
                        await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre,
                                                 "texto": f"{nombre} guardó {ruta}", "fecha": fecha})
                        await difundir_arbol()
                    except (OSError, ValueError) as e:
                        await enviar(ws, {"tipo": "error", "proyecto": sala.nombre, "texto": str(e)})

                elif tipo == "archivo_borrar":
                    try:
                        ruta = msg.get("ruta", "")
                        if msg.get("confirmar") is not True:
                            raise ValueError("confirmación requerida")
                        p = ruta_segura(sala, ruta)
                        if p.is_dir():
                            shutil.rmtree(p)
                        else:
                            p.unlink()
                        sala.guardar_sistema(f"{nombre} borró {ruta}", fecha)
                        await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre,
                                                 "texto": f"{nombre} borró {ruta}", "fecha": fecha})
                        await difundir_arbol()
                    except (OSError, ValueError) as e:
                        await enviar(ws, {"tipo": "error", "proyecto": sala.nombre, "texto": str(e)})

                elif tipo == "archivo_renombrar":
                    try:
                        viejo = msg.get("ruta", "")
                        nuevo = msg.get("nuevo", "")
                        p_viejo = ruta_segura(sala, viejo)
                        p_nuevo = ruta_segura(sala, nuevo)
                        if p_nuevo.exists():
                            raise ValueError("ya existe un archivo con ese nombre")
                        p_nuevo.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(p_viejo), str(p_nuevo))
                        sala.guardar_sistema(f"{nombre} renombró {viejo} → {nuevo}", fecha)
                        await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre,
                                                 "texto": f"{nombre} renombró {viejo} → {nuevo}", "fecha": fecha})
                        await difundir_arbol()
                    except (OSError, ValueError) as e:
                        await enviar(ws, {"tipo": "error", "proyecto": sala.nombre, "texto": str(e)})

                elif tipo == "archivo_subir":
                    try:
                        ruta = msg.get("ruta", "")
                        datos = base64.b64decode(msg.get("datos", ""))
                        if len(datos) > MAX_SUBIDA:
                            raise ValueError("archivo demasiado grande (>20 MB)")
                        p = ruta_segura(sala, ruta)
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(datos)
                        sala.guardar_sistema(f"{nombre} subió {ruta} ({len(datos)} bytes)", fecha)
                        await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre,
                                                 "texto": f"{nombre} subió {ruta} ({len(datos)} bytes)",
                                                 "fecha": fecha})
                        await difundir_arbol()
                    except (OSError, ValueError) as e:
                        await enviar(ws, {"tipo": "error", "proyecto": sala.nombre, "texto": str(e)})

                elif tipo == "git":
                    accion = msg.get("accion", "status")
                    if accion not in GIT_ACCIONES:
                        await enviar(ws, {"tipo": "error", "proyecto": sala.nombre,
                                          "texto": f"acción git no permitida: {accion}"})
                    else:
                        rid = f"git-{secrets.token_hex(3)}"
                        asyncio.create_task(lanzar(sala, rid, cmd_git(accion, msg.get("msg"), nombre), tipo="git"))

                elif tipo == "arbol":
                    await enviar(ws, {"tipo": "arbol", "arbol": _arbol()})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001, S110 - cerrar conexión, sin ruido
        pass
    finally:
        CONECTADOS.discard(ws)
        for sala in mis_salas:
            sala.conectados.discard(ws)
        await enviar_todos({"tipo": "presencia", "conectados": len(CONECTADOS)})


def shlex_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    import uvicorn
    print(f"NEXO arrancando en http://127.0.0.1:{PORT}  (0.0.0.0 para red local)")
    print(f"Token de acceso: {TOKEN}")
    print(f"Workspace: {WORKSPACE}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
