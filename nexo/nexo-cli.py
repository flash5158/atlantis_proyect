#!/usr/bin/env python3
"""NEXO CLI — interfaz para que un agente Hermes use el espacio de trabajo.

Uso:
  nexo-cli chat <texto> --proyecto P           enviar mensaje al chat del proyecto
  nexo-cli run "comando" --proyecto P          ejecutar comando y mostrar salida
  nexo-cli leer <ruta> --proyecto P            leer un archivo
  nexo-cli escribir <ruta> --contenido "..." --proyecto P
  nexo-cli borrar <ruta> --proyecto P --confirmar
  nexo-cli renombrar <ruta> <nuevo> --proyecto P
  nexo-cli subir <archivo-local> --destino <ruta> --proyecto P
  nexo-cli descargar <ruta> --salida <archivo> --proyecto P
  nexo-cli arbol                                listar proyectos y archivos
  nexo-cli historial --proyecto P              últimas líneas del chat
  nexo-cli nuevo <nombre>                       crear proyecto
  nexo-cli proyecto-borrar <nombre> --confirmar
  nexo-cli git <status|add|commit|push|pull|log|branch|remote|diff|stash> [--msg "..."]
  nexo-cli escuchar [--proyecto P]             modo agente: se queda conectado,
                                               imprime chat/sistema/output en vivo

Opciones globales: --server http://127.0.0.1:8787 --token X --nombre hermes-1
El token y nombre se leen de ~/nexo/config.json si no se pasan.
"""

import argparse
import asyncio
import base64
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import websockets


def config_por_defecto():
    cfg = Path.home() / "nexo" / "config.json"
    if cfg.exists():
        return json.loads(cfg.read_text())
    return {"token": "", "nombre": "hermes-1", "companero": "hermes-2"}


def parse_args():
    cfg = config_por_defecto()
    p = argparse.ArgumentParser(prog="nexo-cli", description="NEXO: workspace colaborativo para 2 Hermes")
    p.add_argument("--server", default="http://127.0.0.1:8787", help="URL del servidor NEXO")
    p.add_argument("--token", default=cfg.get("token", ""), help="token de acceso")
    p.add_argument("--nombre", default=cfg.get("nombre", "hermes-1"), help="tu nombre de agente")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_proyecto(sp, required=True):
        sp.add_argument("--proyecto", default="general", help="proyecto/sala")

    sp = sub.add_parser("chat", help="enviar mensaje al chat")
    sp.add_argument("texto")
    add_proyecto(sp)

    sp = sub.add_parser("run", help="ejecutar comando en el proyecto")
    sp.add_argument("comando")
    add_proyecto(sp)

    sp = sub.add_parser("leer", help="leer un archivo")
    sp.add_argument("ruta")
    add_proyecto(sp)

    sp = sub.add_parser("escribir", help="escribir/crear un archivo")
    sp.add_argument("ruta")
    sp.add_argument("--contenido", default="", help="contenido (o - para stdin)")
    add_proyecto(sp)

    sp = sub.add_parser("borrar", help="borrar archivo o carpeta")
    sp.add_argument("ruta")
    sp.add_argument("--confirmar", action="store_true", help="confirmación obligatoria")
    add_proyecto(sp)

    sp = sub.add_parser("renombrar", help="renombrar/mover archivo")
    sp.add_argument("ruta")
    sp.add_argument("nuevo")
    add_proyecto(sp)

    sp = sub.add_parser("subir", help="subir archivo local al proyecto (base64)")
    sp.add_argument("local", help="ruta local del archivo")
    sp.add_argument("--destino", default=None, help="ruta destino en el proyecto (default: nombre)")
    add_proyecto(sp)

    sp = sub.add_parser("descargar", help="descargar archivo del proyecto")
    sp.add_argument("ruta")
    sp.add_argument("--salida", default=None, help="ruta local de salida (default: nombre)")
    add_proyecto(sp)

    sp = sub.add_parser("arbol", help="listar proyectos y archivos")
    sp = sub.add_parser("historial", help="leer el chat del proyecto")
    add_proyecto(sp)

    sp = sub.add_parser("nuevo", help="crear proyecto")
    sp.add_argument("nombre")

    sp = sub.add_parser("proyecto-borrar", help="eliminar proyecto completo")
    sp.add_argument("nombre")
    sp.add_argument("--confirmar", action="store_true")

    sp = sub.add_parser("git", help="git en el workspace")
    sp.add_argument("accion", choices=["status", "add", "commit", "push", "pull", "log", "branch", "remote", "diff", "stash"])
    sp.add_argument("--msg", default="", help="mensaje para commit")

    sp = sub.add_parser("escuchar", help="quedarse conectado a la sala (modo agente)")
    add_proyecto(sp, required=False)
    return p.parse_args()


def url_ws(args):
    return args.server.replace("http://", "ws://").replace("https://", "wss://") + "/ws"


def rest(args, path):
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(args.server + path + f"{sep}token={urllib.parse.quote(args.token)}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


async def enviar_y_esperar(args, msg, hasta="run_fin", timeout=600):
    """Envía un mensaje WS y devuelve las respuestas acumuladas hasta `hasta`."""
    out = []
    async with websockets.connect(url_ws(args) + f"?token={urllib.parse.quote(args.token)}&nombre={urllib.parse.quote(args.nombre)}") as ws:
        await ws.send(json.dumps(msg, ensure_ascii=False))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                out.append({"tipo": "error", "texto": "timeout esperando respuesta"})
                break
            m = json.loads(raw)
            out.append(m)
            if m.get("tipo") == hasta:
                break
    return out


def imprimir(m, args=None):
    t = m.get("tipo")
    if t == "chat":
        print(f"[chat:{m.get('proyecto')}] {m.get('de')}: {m.get('texto')}")
    elif t == "sistema":
        print(f"[sistema] {m.get('texto')}")
    elif t == "output":
        sys.stdout.write(m.get("texto", ""))
        sys.stdout.flush()
    elif t == "run_fin":
        print(f"\n[exit code {m.get('code')}]")
    elif t == "error":
        print(f"[error] {m.get('texto')}", file=sys.stderr)
    elif t == "escribiendo":
        print(f"[{m.get('de')} está escribiendo…]")
    elif t == "presencia":
        print(f"[presencia] {m.get('conectados')} agente(s) conectados")
    elif t == "run_id":
        pass
    else:
        print(json.dumps(m, ensure_ascii=False))


async def cmd_escuchar(args):
    print(f"Escuchando sala '{args.proyecto}' como {args.nombre} (Ctrl+C para salir)…")
    while True:
        try:
            async with websockets.connect(url_ws(args) + f"?token={urllib.parse.quote(args.token)}&nombre={urllib.parse.quote(args.nombre)}") as ws:
                await ws.send(json.dumps({"tipo": "join", "proyecto": args.proyecto}))
                async for raw in ws:
                    imprimir(json.loads(raw))
        except websockets.ConnectionClosed:
            print("\n[reconectando en 3s…]")
            await asyncio.sleep(3)
        except KeyboardInterrupt:
            return


async def main():
    args = parse_args()
    if not args.token:
        print("No hay token: crea ~/nexo/config.json o pasa --token", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "escuchar":
        await cmd_escuchar(args)
        return

    if args.cmd == "arbol":
        d = rest(args, "/api/arbol")
        if "arbol" in d:
            for p in d["arbol"]:
                print(f"📁 {p['nombre']}")
                for f in p["archivos"]:
                    print(f"   📄 {f}")
        return

    if args.cmd == "historial":
        d = rest(args, f"/api/historial?proyecto={urllib.parse.quote(args.proyecto)}")
        for m in d.get("historial", []):
            if m["de"] == "sistema":
                print(f"  ⚙ {m['texto']}")
            else:
                print(f"[{m['fecha']}] {m['de']}: {m['texto']}")
        return

    if args.cmd == "leer":
        d = rest(args, f"/api/archivo?proyecto={urllib.parse.quote(args.proyecto)}&ruta={urllib.parse.quote(args.ruta)}")
        if "contenido" in d:
            print(d["contenido"])
            if d.get("truncado"):
                print("\n… [archivo truncado]", file=sys.stderr)
        else:
            print("Error: " + d.get("error", "?"), file=sys.stderr)
            sys.exit(1)
        return

    if args.cmd == "descargar":
        url = args.server + f"/api/descargar?proyecto={urllib.parse.quote(args.proyecto)}&ruta={urllib.parse.quote(args.ruta)}&token={urllib.parse.quote(args.token)}"
        req = urllib.request.Request(url)
        def descargar():
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        datos = await asyncio.to_thread(descargar)
        salida = args.salida or Path(args.ruta).name
        Path(salida).write_bytes(datos)
        print(f"Descargado {len(datos)} bytes → {salida}")
        return

    if args.cmd == "nuevo":
        resp = await enviar_y_esperar(args, {"tipo": "proyecto_nuevo", "nombre": args.nombre}, hasta="arbol", timeout=15)
        print(f"Proyecto '{args.nombre}' creado" if any(m.get("tipo") == "arbol" for m in resp) else "Proyecto enviado")
        return

    if args.cmd == "proyecto-borrar":
        if not args.confirmar:
            print("Usa --confirmar para eliminar el proyecto (¡no se puede deshacer!)", file=sys.stderr)
            sys.exit(1)
        resp = await enviar_y_esperar(args, {"tipo": "proyecto_borrar", "nombre": args.nombre, "confirmar": True}, hasta="arbol", timeout=30)
        print(f"Proyecto '{args.nombre}' eliminado")
        return

    if args.cmd == "chat":
        resp = await enviar_y_esperar(args, {"tipo": "chat", "proyecto": args.proyecto, "texto": args.texto}, hasta="chat", timeout=15)
        imprimir(resp[-1])
        return

    if args.cmd == "run":
        resp = await enviar_y_esperar(args, {"tipo": "run", "proyecto": args.proyecto, "cmd": args.comando, "id": "cli"}, hasta="run_fin")
        for m in resp:
            imprimir(m)
        return

    if args.cmd == "escribir":
        contenido = args.contenido
        if args.contenido == "-":
            contenido = sys.stdin.read()
        resp = await enviar_y_esperar(args, {"tipo": "archivo_escribir", "proyecto": args.proyecto, "ruta": args.ruta, "contenido": contenido}, hasta="arbol", timeout=15)
        if any(m.get("tipo") == "error" for m in resp):
            for m in resp:
                if m.get("tipo") == "error":
                    print("Error: " + m["texto"], file=sys.stderr)
            sys.exit(1)
        print(f"Escrito {args.ruta} en '{args.proyecto}'")
        return

    if args.cmd == "borrar":
        if not args.confirmar:
            print("Usa --confirmar para borrar (¡no se puede deshacer!)", file=sys.stderr)
            sys.exit(1)
        resp = await enviar_y_esperar(args, {"tipo": "archivo_borrar", "proyecto": args.proyecto, "ruta": args.ruta, "confirmar": True}, hasta="arbol", timeout=15)
        if any(m.get("tipo") == "error" for m in resp):
            print("Error: " + next(m["texto"] for m in resp if m.get("tipo") == "error"), file=sys.stderr)
            sys.exit(1)
        print(f"Borrado {args.ruta}")
        return

    if args.cmd == "renombrar":
        resp = await enviar_y_esperar(args, {"tipo": "archivo_renombrar", "proyecto": args.proyecto, "ruta": args.ruta, "nuevo": args.nuevo}, hasta="arbol", timeout=15)
        if any(m.get("tipo") == "error" for m in resp):
            print("Error: " + next(m["texto"] for m in resp if m.get("tipo") == "error"), file=sys.stderr)
            sys.exit(1)
        print(f"Renombrado {args.ruta} → {args.nuevo}")
        return

    if args.cmd == "subir":
        local = Path(args.local)
        if not local.exists():
            print(f"No existe: {local}", file=sys.stderr)
            sys.exit(1)
        datos = base64.b64encode(local.read_bytes()).decode()
        destino = args.destino or local.name
        print(f"Subiendo {local.name} ({local.stat().st_size} bytes) → {destino}…")
        resp = await enviar_y_esperar(args, {"tipo": "archivo_subir", "proyecto": args.proyecto, "ruta": destino, "datos": datos}, hasta="arbol", timeout=60)
        if any(m.get("tipo") == "error" for m in resp):
            print("Error: " + next(m["texto"] for m in resp if m.get("tipo") == "error"), file=sys.stderr)
            sys.exit(1)
        print("Subido ✓")
        return

    if args.cmd == "git":
        resp = await enviar_y_esperar(args, {"tipo": "git", "accion": args.accion, "msg": args.msg}, hasta="run_fin", timeout=300)
        for m in resp:
            imprimir(m)
        return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
