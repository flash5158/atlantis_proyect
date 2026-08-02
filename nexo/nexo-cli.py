#!/usr/bin/env python3
"""NEXO CLI — interfaz para que un agente Hermes use el espacio de trabajo.

Uso:
  nexo-cli chat <texto> --proyecto P           enviar mensaje al chat del proyecto
  nexo-cli run "comando" --proyecto P          ejecutar comando y mostrar salida
  nexo-cli leer <ruta> --proyecto P            leer un archivo
  nexo-cli escribir <ruta> --contenido "..." --proyecto P
  nexo-cli arbol                                listar proyectos y archivos
  nexo-cli historial --proyecto P              últimas líneas del chat
  nexo-cli git <status|commit|push|pull> [--msg "..."]
  nexo-cli escuchar [--proyecto P]             modo agente: se queda conectado,
                                               imprime chat/sistema/output en vivo
  nexo-cli nuevo <nombre>                       crear proyecto

Opciones globales: --server http://127.0.0.1:8787 --token X --nombre hermes-1
El token y nombre se leen de ~/nexo/config.json si no se pasan.
"""

import argparse
import asyncio
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path.home() / "nexo" / "config.json"


def config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}


def ws_url(base: str, token: str, nombre: str) -> str:
    base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{base}/ws?token={urllib.parse.quote(token)}&nombre={urllib.parse.quote(nombre)}"


def rest(base: str, path: str, token: str):
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen(f"{base}{path}{sep}token={urllib.parse.quote(token)}", timeout=10) as r:
        return json.loads(r.read())


async def hablar(ws, msg):
    await ws.send(json.dumps(msg, ensure_ascii=False))


async def escuchar(ws, nombre, proyecto=None, echo=True, hasta_fin=False):
    """Bucle del agente: imprime todo lo que llega a su sala."""
    if proyecto:
        await hablar(ws, {"tipo": "join", "proyecto": proyecto})
    try:
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            tipo = msg.get("tipo")
            if tipo == "chat":
                print(f"\n[{msg.get('fecha','')}] {msg.get('de','?')}: {msg.get('texto','')}", flush=True)
            elif tipo == "sistema":
                print(f"\n[nexo] {msg.get('texto','')}", flush=True)
            elif tipo == "output":
                print(f"\033[90m[out {msg.get('id','')}] {msg.get('texto','')}\033[0m", end="", flush=True)
            elif tipo == "run_fin":
                print(f"\n[nexo] fin comando {msg.get('cmd','')} -> código {msg.get('code')}", flush=True)
                if hasta_fin:
                    return
            elif tipo == "arbol":
                for p in msg.get("arbol", []):
                    print(f"\n[arbol] {p['nombre']}/ ({len(p['archivos'])} archivos)")
            elif tipo == "error":
                print(f"\n[nexo error] {msg.get('texto','')}", flush=True)
    except Exception:
        pass


async def cmd_chat(args):
    ws = await _ws(args)
    await hablar(ws, {"tipo": "chat", "proyecto": args.proyecto, "texto": args.texto})
    await ws.close()


async def cmd_run(args):
    ws = await _ws(args)
    rid = args.id or "cli"
    await hablar(ws, {"tipo": "run", "proyecto": args.proyecto, "cmd": args.comando, "id": rid})
    await escuchar(ws, args.nombre, hasta_fin=True)
    await ws.close()


async def cmd_leer(args):
    ws = await _ws(args)
    await hablar(ws, {"tipo": "archivo_leer", "proyecto": args.proyecto, "ruta": args.ruta})
    while True:
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("tipo") == "archivo":
            sys.stdout.write(msg.get("contenido", ""))
            await ws.close()
            return
        if msg.get("tipo") == "error":
            print(f"error: {msg.get('texto')}", file=sys.stderr)
            await ws.close()
            sys.exit(1)


async def cmd_escribir(args):
    ws = await _ws(args)
    await hablar(ws, {"tipo": "archivo_escribir", "proyecto": args.proyecto,
                      "ruta": args.ruta, "contenido": args.contenido})
    await asyncio.sleep(0.5)
    await ws.close()


async def cmd_git(args):
    ws = await _ws(args)
    await hablar(ws, {"tipo": "git", "proyecto": args.proyecto, "accion": args.accion, "msg": args.msg})
    await escuchar(ws, args.nombre, hasta_fin=True)
    await ws.close()


async def cmd_arbol(args):
    cfg = config()
    d = rest(args.server, "/api/arbol", args.token or cfg.get("token", ""))
    for p in d.get("arbol", []):
        print(f"{p['nombre']}/")
        for f in p.get("archivos", [])[:50]:
            print(f"  {f}")


async def cmd_historial(args):
    cfg = config()
    d = rest(args.server, f"/api/historial?proyecto={urllib.parse.quote(args.proyecto)}",
             args.token or cfg.get("token", ""))
    for m in d.get("historial", []):
        if m.get("de") == "sistema":
            print(f"> {m['texto']}")
        else:
            print(f"[{m.get('fecha','')}] {m.get('de','?')}: {m.get('texto','')}")


async def cmd_nuevo(args):
    ws = await _ws(args)
    await hablar(ws, {"tipo": "proyecto_nuevo", "nombre": args.nombre_proyecto})
    await asyncio.sleep(0.5)
    await ws.close()
    print(f"proyecto {args.nombre_proyecto} creado")


async def cmd_escuchar(args):
    ws = await _ws(args)
    await escuchar(ws, args.nombre, proyecto=args.proyecto)


async def _ws(args):
    import websockets
    cfg = config()
    token = args.token or cfg.get("token", "")
    nombre = args.nombre or cfg.get("nombre", "hermes-1")
    return await websockets.connect(ws_url(args.server, token, nombre))


def main():
    p = argparse.ArgumentParser(description="NEXO CLI de agente")
    g = argparse.ArgumentParser(add_help=False)
    g.add_argument("--server", default="http://127.0.0.1:8787")
    g.add_argument("--token", default=None)
    g.add_argument("--nombre", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("chat", parents=[g]); sp.add_argument("texto"); sp.add_argument("--proyecto", default="general"); sp.set_defaults(fn=cmd_chat)
    sp = sub.add_parser("run", parents=[g]); sp.add_argument("comando"); sp.add_argument("--proyecto", default="general"); sp.add_argument("--id", default=None); sp.set_defaults(fn=cmd_run)
    sp = sub.add_parser("leer", parents=[g]); sp.add_argument("ruta"); sp.add_argument("--proyecto", default="general"); sp.set_defaults(fn=cmd_leer)
    sp = sub.add_parser("escribir", parents=[g]); sp.add_argument("ruta"); sp.add_argument("--contenido", default=""); sp.add_argument("--proyecto", default="general"); sp.set_defaults(fn=cmd_escribir)
    sp = sub.add_parser("git", parents=[g]); sp.add_argument("accion", choices=["status", "commit", "push", "pull"]); sp.add_argument("--msg", default=None); sp.add_argument("--proyecto", default="general"); sp.set_defaults(fn=cmd_git)
    sp = sub.add_parser("arbol", parents=[g]); sp.set_defaults(fn=cmd_arbol)
    sp = sub.add_parser("historial", parents=[g]); sp.add_argument("--proyecto", default="general"); sp.set_defaults(fn=cmd_historial)
    sp = sub.add_parser("nuevo", parents=[g]); sp.add_argument("nombre_proyecto"); sp.set_defaults(fn=cmd_nuevo)
    sp = sub.add_parser("escuchar", parents=[g]); sp.add_argument("--proyecto", default=None); sp.set_defaults(fn=cmd_escuchar)

    args = p.parse_args()
    asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
