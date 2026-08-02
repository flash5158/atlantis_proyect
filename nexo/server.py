#!/usr/bin/env python3
"""NEXO — espacio de trabajo colaborativo para dos agentes Hermes.

Servidor local (FastAPI + WebSocket):
  - Árbol de proyectos con sus archivos
  - Chat compartido por proyecto, persistido en .nexo-chat.md
  - Ejecución de comandos con salida en streaming a la sala
  - Lectura/escritura de archivos por los agentes
  - Git/GitHub opcional (status/commit/push/pull)

Arranque:  .venv/bin/python server.py   ->  http://127.0.0.1:8787
"""

import asyncio
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from shlex import quote as shlex_quote

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------- config
HOME = Path.home()
WORKSPACE = Path(__file__).parent / "colab-hub" if (Path(__file__).parent / "colab-hub").exists() else HOME / "colab-hub"
if not WORKSPACE.exists():
    WORKSPACE = HOME / "nexo-workspace"
PROYECTOS = WORKSPACE / "proyectos"
CONFIG = Path(__file__).parent / "config.json"
PORT = 8787

def cargar_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    cfg = {"token": secrets.token_hex(16), "nombre": "hermes-1", "companero": "hermes-2"}
    CONFIG.write_text(json.dumps(cfg, indent=2))
    return cfg

CFG = cargar_config()
TOKEN = CFG["token"]
PROYECTOS.mkdir(parents=True, exist_ok=True)
if not (PROYECTOS / "README.md").exists():
    (PROYECTOS / "README.md").write_text("# Proyectos de NEXO\n\nCada carpeta es un proyecto con su chat.\n")

# ---------------------------------------------------------------- app
app = FastAPI(title="NEXO")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

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

SALAS: dict[str, Sala] = {}


def sala_de(nombre: str) -> Sala:
    nombre = Path(nombre).name  # sin rutas raras
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
    if base != p and base not in p.parents:
        raise ValueError(f"ruta fuera del proyecto: {ruta}")
    return p


async def enviar(ws: WebSocket, msg: dict):
    await ws.send_text(json.dumps(msg, ensure_ascii=False))


async def enviar_sala(sala: Sala, msg: dict, excepto: WebSocket | None = None):
    for ws in list(sala.conectados):
        if ws is excepto:
            continue
        try:
            await enviar(ws, msg)
        except OSError:
            sala.conectados.discard(ws)


async def lanzar(sala: Sala, rid: str, cmd: str, tipo: str = "run"):
    """Ejecuta un comando en el directorio del proyecto y emite la salida en streaming."""
    proc = await asyncio.create_subprocess_shell(
        cmd, cwd=sala.directorio,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
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


# ---------------------------------------------------------------- REST
@app.get("/")
async def raiz():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


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


@app.get("/api/arbol")
async def api_arbol(token: str = Query(...)):
    if token != TOKEN:
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {"arbol": _arbol(), "nombre": CFG["nombre"], "companero": CFG["companero"]}


@app.get("/api/historial")
async def api_historial(proyecto: str, token: str = Query(...)):
    if token != TOKEN:
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {"historial": sala_de(proyecto).historial()}


# ---------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(""), nombre: str = Query("")):
    if token != TOKEN:
        await ws.close(code=4401)
        return
    await ws.accept()
    nombre = nombre or CFG["nombre"]
    mis_salas: set[Sala] = set()
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            tipo = msg.get("tipo")
            fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

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
                    await enviar(ws, {"tipo": "arbol", "arbol": _arbol()})

            elif tipo in ("chat", "run", "archivo_leer", "archivo_escribir", "git", "arbol", "stop"):
                sala = sala_de(msg.get("proyecto", "general"))
                sala.conectados.add(ws)
                mis_salas.add(sala)

                if tipo == "chat":
                    texto = msg.get("texto", "").strip()
                    if texto:
                        sala.guardar_chat(nombre, texto, fecha)
                        await enviar_sala(sala, {"tipo": "chat", "proyecto": sala.nombre,
                                                 "de": nombre, "texto": texto, "fecha": fecha})

                elif tipo == "run":
                    rid = msg.get("id") or secrets.token_hex(4)
                    cmd = msg.get("cmd", "")
                    if cmd.strip():
                        sala.guardar_sistema(f"{nombre} ejecutó: {cmd}", fecha)
                        asyncio.create_task(lanzar(sala, rid, cmd))

                elif tipo == "stop":
                    proc = sala.procesos.get(msg.get("id", ""))
                    if proc:
                        proc.kill()

                elif tipo == "archivo_leer":
                    try:
                        p = ruta_segura(sala, msg.get("ruta", ""))
                        contenido = p.read_text(errors="replace")
                        await enviar(ws, {"tipo": "archivo", "proyecto": sala.nombre,
                                          "ruta": msg["ruta"], "contenido": contenido})
                    except OSError as e:
                        await enviar(ws, {"tipo": "error", "proyecto": sala.nombre, "texto": str(e)})

                elif tipo == "archivo_escribir":
                    try:
                        p = ruta_segura(sala, msg.get("ruta", ""))
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(msg.get("contenido", ""), encoding="utf-8")
                        sala.guardar_sistema(f"{nombre} guardó {msg['ruta']}", fecha)
                        await enviar_sala(sala, {"tipo": "sistema", "proyecto": sala.nombre,
                                                 "texto": f"{nombre} guardó {msg['ruta']}", "fecha": fecha})
                    except OSError as e:
                        await enviar(ws, {"tipo": "error", "proyecto": sala.nombre, "texto": str(e)})

                elif tipo == "git":
                    accion = msg.get("accion", "status")
                    rid = secrets.token_hex(4)
                    ident = f"-c user.name={shlex_quote(nombre)} -c user.email={shlex_quote(nombre + '@nexo.local')}"
                    cmd = f"git -C {shlex_quote(str(WORKSPACE))} {ident} {accion}"
                    if accion == "commit" and msg.get("msg"):
                        cmd = (f"git -C {shlex_quote(str(WORKSPACE))} {ident} add -A && "
                               f"git -C {shlex_quote(str(WORKSPACE))} {ident} commit -m {shlex_quote(msg['msg'])}")
                    asyncio.create_task(lanzar(sala, rid, cmd, tipo="git"))

                elif tipo == "proyecto_nuevo":
                    nombre_proy = msg.get("nombre", "").strip()
                    if nombre_proy:
                        n = sala_de(nombre_proy)
                        n.guardar_sistema(f"proyecto creado por {nombre}", fecha)
                        await enviar_sala(n, {"tipo": "sistema", "proyecto": n.nombre,
                                              "texto": f"proyecto creado por {nombre}", "fecha": fecha})
                        for ws_ in list(n.conectados) + [ws]:
                            await enviar(ws_, {"tipo": "arbol", "arbol": _arbol()})

                elif tipo == "arbol":
                    await enviar(ws, {"tipo": "arbol", "arbol": _arbol()})
    except WebSocketDisconnect:
        pass
    finally:
        for sala in mis_salas:
            sala.conectados.discard(ws)


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    import uvicorn
    print(f"NEXO arrancando en http://127.0.0.1:{PORT}  (0.0.0.0 para red local)")
    print(f"Token de acceso: {TOKEN}")
    print(f"Workspace: {WORKSPACE}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
