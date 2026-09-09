#!/usr/bin/env python3
from __future__ import annotations

"""NEXO CLI 2.0 — Interfaz de línea de comandos para desarrolladores y agentes IA (Hermes & Antigravity).

Uso:
  nexo-cli chat <texto> --proyecto P                  Enviar mensaje al chat del proyecto
  nexo-cli run "comando" --proyecto P                 Ejecutar comando en el proyecto y ver streaming
  nexo-cli leer <ruta> --proyecto P                   Leer contenido de un archivo
  nexo-cli escribir <ruta> --contenido "..."          Escribir/crear archivo (- para leer de stdin)
  nexo-cli borrar <ruta> --confirmar                  Borrar archivo o carpeta
  nexo-cli renombrar <ruta> <nuevo>                   Renombrar o mover archivo
  nexo-cli subir <local> --destino <ruta>             Subir archivo local al workspace
  nexo-cli descargar <ruta> --salida <archivo>        Descargar archivo del workspace
  nexo-cli arbol                                      Listar todos los proyectos y archivos
  nexo-cli historial --proyecto P                     Ver mensajes recientes del chat
  nexo-cli nuevo <nombre>                             Crear un nuevo proyecto
  nexo-cli proyecto-borrar <nombre> --confirmar       Eliminar un proyecto completo
  nexo-cli git <status|add|commit|push|pull|log>      Comandos git en el repositorio
  nexo-cli escuchar [--proyecto P]                    Modo escucha en vivo continuo (daemon de agente)

Comandos para Agentes IA (Buzón, Tareas y Colaboración):
  nexo-cli ai-inbox [--agente X] [--pendientes]       Ver mensajes y tareas pendientes para tu IA
  nexo-cli ai-send --para X --titulo T --contenido C  Enviar tarea, pregunta o código a la otra IA
  nexo-cli ai-reply --id ID --contenido C             Responder a una tarea previa
  nexo-cli ai-tasks [--proyecto P]                    Ver el tablero colaborativo de tareas
  nexo-cli ai-task-add --titulo T --asignado X        Crear una tarea compartida
  nexo-cli ai-status                                  Ver estado de conectividad e IAs en línea
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

BASE = Path(__file__).resolve().parent


def config_por_defecto() -> dict:
    rutas_cfg = [
        BASE / "config.json",
        Path.home() / "nexo" / "config.json",
        Path.home() / "colab-hub" / "nexo" / "config.json",
    ]
    for r in rutas_cfg:
        if r.exists():
            try:
                return json.loads(r.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {"token": "", "nombre": "hermes-1", "ia_nombre": "hermes"}


def parse_args():
    cfg = config_por_defecto()
    env_server = os.environ.get("NEXO_SERVER", "http://127.0.0.1:8787")
    env_token = os.environ.get("NEXO_TOKEN", cfg.get("token", ""))
    env_nombre = os.environ.get("NEXO_NOMBRE", cfg.get("nombre", "agente"))

    p = argparse.ArgumentParser(prog="nexo-cli", description="NEXO 2.0: Workspace colaborativo para desarrolladores e IAs")
    p.add_argument("--server", default=env_server, help="URL del servidor NEXO (ej: http://127.0.0.1:8787 o URL Cloudflare)")
    p.add_argument("--token", default=env_token, help="Token de autenticación")
    p.add_argument("--nombre", default=env_nombre, help="Tu identificador o nombre de agente")
    p.add_argument("--json", action="store_true", help="Salida en formato JSON para consumo de scripts/LLMs")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add_proyecto(sp, required=False):
        sp.add_argument("--proyecto", default="general", help="Nombre del proyecto o sala (default: general)")

    # Comandos generales
    sp = sub.add_parser("chat", help="Enviar mensaje al chat del proyecto")
    sp.add_argument("texto")
    add_proyecto(sp)

    sp = sub.add_parser("run", help="Ejecutar comando en el proyecto")
    sp.add_argument("comando")
    add_proyecto(sp)

    sp = sub.add_parser("leer", help="Leer un archivo")
    sp.add_argument("ruta")
    add_proyecto(sp)

    sp = sub.add_parser("escribir", help="Escribir o crear un archivo")
    sp.add_argument("ruta")
    sp.add_argument("--contenido", default="", help="Contenido del archivo (o - para leer de stdin)")
    add_proyecto(sp)

    sp = sub.add_parser("borrar", help="Borrar archivo o carpeta")
    sp.add_argument("ruta")
    sp.add_argument("--confirmar", action="store_true", help="Confirmación obligatoria")
    add_proyecto(sp)

    sp = sub.add_parser("renombrar", help="Renombrar o mover archivo")
    sp.add_argument("ruta")
    sp.add_argument("nuevo")
    add_proyecto(sp)

    sp = sub.add_parser("subir", help="Subir archivo local al proyecto")
    sp.add_argument("local", help="Ruta local del archivo")
    sp.add_argument("--destino", default=None, help="Ruta de destino dentro del proyecto")
    add_proyecto(sp)

    sp = sub.add_parser("descargar", help="Descargar archivo del proyecto")
    sp.add_argument("ruta")
    sp.add_argument("--salida", default=None, help="Ruta de guardado local")
    add_proyecto(sp)

    sub.add_parser("arbol", help="Listar proyectos y archivos en el workspace")

    sp = sub.add_parser("historial", help="Ver historial del chat del proyecto")
    add_proyecto(sp)

    sp = sub.add_parser("nuevo", help="Crear un nuevo proyecto")
    sp.add_argument("nombre")

    sp = sub.add_parser("proyecto-borrar", help="Eliminar un proyecto completo")
    sp.add_argument("nombre")
    sp.add_argument("--confirmar", action="store_true")

    sp = sub.add_parser("git", help="Operaciones Git en el repositorio del workspace")
    sp.add_argument("accion", choices=["status", "add", "commit", "push", "pull", "log", "branch", "remote", "diff", "stash"])
    sp.add_argument("--msg", default="", help="Mensaje para el commit")

    sp = sub.add_parser("escuchar", help="Modo agente en vivo: escucha chat y eventos")
    add_proyecto(sp)

    # Comandos IA-a-IA
    sp = sub.add_parser("ai-inbox", help="Consultar mensajes y tareas recibidas por la IA")
    sp.add_argument("--agente", default="", help="Filtrar por nombre de IA destino")
    sp.add_argument("--pendientes", action="store_true", help="Mostrar solo tareas pendientes")
    add_proyecto(sp)

    sp = sub.add_parser("ai-send", help="Enviar mensaje estructurado o tarea a otra IA")
    sp.add_argument("--para", default="todos", help="Nombre de la IA destino (ej: hermes-2, antigravity)")
    sp.add_argument("--titulo", required=True, help="Título de la tarea o mensaje")
    sp.add_argument("--contenido", required=True, help="Descripción detallada de la tarea o consulta")
    sp.add_argument("--tipo", choices=["tarea", "pregunta", "respuesta", "revision", "sync", "alerta"], default="tarea")
    sp.add_argument("--archivos", default="", help="Archivos relacionados separados por comas")
    sp.add_argument("--codigo", default="", help="Fragmento de código relevante")
    add_proyecto(sp)

    sp = sub.add_parser("ai-reply", help="Responder a una tarea o mensaje de otra IA")
    sp.add_argument("--id", required=True, help="ID del mensaje al que se responde (mid)")
    sp.add_argument("--contenido", required=True, help="Respuesta o reporte de resolución")
    sp.add_argument("--estado", choices=["completado", "en_progreso", "pendiente"], default="completado")
    sp.add_argument("--codigo", default="", help="Código o diff generado")

    sp = sub.add_parser("ai-tasks", help="Ver tablero colaborativo de tareas")
    add_proyecto(sp)

    sp = sub.add_parser("ai-task-add", help="Crear una tarea en el tablero colaborativo")
    sp.add_argument("--titulo", required=True, help="Título de la tarea")
    sp.add_argument("--descripcion", default="", help="Detalles de la tarea")
    sp.add_argument("--asignado", default="todos", help="A quién se asigna (nombre de usuario o IA)")
    sp.add_argument("--prioridad", choices=["baja", "media", "alta", "urgente"], default="media")
    sp.add_argument("--archivos", default="", help="Archivos involucrados separados por coma")
    add_proyecto(sp)

    sub.add_parser("ai-status", help="Ver estado de conexión y presencia de las IAs")

    return p.parse_args()


def url_ws(server: str) -> str:
    s = server.rstrip("/")
    if s.startswith("https://"):
        return s.replace("https://", "wss://") + "/ws"
    return s.replace("http://", "ws://") + "/ws"


def rest_get(args, endpoint: str) -> dict:
    sep = "&" if "?" in endpoint else "?"
    url = f"{args.server.rstrip('/')}{endpoint}{sep}token={urllib.parse.quote(args.token)}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(err_msg)
        except Exception:
            return {"error": f"HTTP {e.code}: {err_msg}"}
    except Exception as e:
        return {"error": str(e)}


def rest_post(args, endpoint: str, data: dict) -> dict:
    sep = "&" if "?" in endpoint else "?"
    url = f"{args.server.rstrip('/')}{endpoint}{sep}token={urllib.parse.quote(args.token)}"
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(err_msg)
        except Exception:
            return {"error": f"HTTP {e.code}: {err_msg}"}
    except Exception as e:
        return {"error": str(e)}


async def enviar_y_esperar(args, msg: dict, hasta="run_fin", timeout=300) -> list[dict]:
    out = []
    uri = f"{url_ws(args.server)}?token={urllib.parse.quote(args.token)}&nombre={urllib.parse.quote(args.nombre)}"
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(msg, ensure_ascii=False))
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except TimeoutError:
                    out.append({"tipo": "error", "texto": "Tiempo de espera agotado"})
                    break
                m = json.loads(raw)
                out.append(m)
                if m.get("tipo") == hasta:
                    break
    except Exception as e:
        out.append({"tipo": "error", "texto": f"Error WebSocket: {e}"})
    return out


def imprimir_mensaje_consola(m: dict):
    t = m.get("tipo")
    if t == "chat":
        print(f"💬 [{m.get('proyecto', '')}] {m.get('de')}: {m.get('texto')}")
    elif t == "sistema":
        print(f"⚙️ {m.get('texto')}")
    elif t == "output":
        sys.stdout.write(m.get("texto", ""))
        sys.stdout.flush()
    elif t == "run_fin":
        code = m.get("code", 0)
        simbolo = "✓" if code == 0 else "✗"
        print(f"\n[{simbolo} Proceso finalizado con código: {code}]")
    elif t == "ai_msg":
        msg = m.get("mensaje", {})
        print(f"\n🤖 [MENSAJE IA] De: {msg.get('de')} → Para: {msg.get('para')} ({msg.get('tipo')})")
        print(f"   Título: {msg.get('titulo')}")
        print(f"   Contenido: {msg.get('contenido')}")
        if msg.get("archivos"):
            print(f"   Archivos: {', '.join(msg.get('archivos'))}")
        if msg.get("codigo"):
            print(f"   Código:\n{msg.get('codigo')}")
    elif t == "error":
        print(f"❌ Error: {m.get('texto')}", file=sys.stderr)
    elif t == "escribiendo":
        print(f"✍️  {m.get('de')} está escribiendo…")
    elif t == "presencia":
        print(f"👥 Conectados: {m.get('conectados')} agente(s)")


async def cmd_escuchar(args):
    print(f"\n🎧 Conectado a NEXO [{args.server}] como '{args.nombre}'")
    print(f"   Proyecto activo: {args.proyecto}")
    print("   Presiona Ctrl+C para salir.\n" + "-" * 50)
    uri = f"{url_ws(args.server)}?token={urllib.parse.quote(args.token)}&nombre={urllib.parse.quote(args.nombre)}"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"tipo": "join", "proyecto": args.proyecto}))
                async for raw in ws:
                    msg = json.loads(raw)
                    if args.json:
                        print(json.dumps(msg, ensure_ascii=False))
                    else:
                        imprimir_mensaje_consola(msg)
        except websockets.ConnectionClosed:
            print("\n[Reconectando en 3 segundos…]")
            await asyncio.sleep(3)
        except KeyboardInterrupt:
            print("\nDesconectado.")
            return


async def main():
    args = parse_args()

    if not args.token:
        print("❌ Error: No se especificó el token de acceso.", file=sys.stderr)
        print("   Configúralo en nexo/config.json, pasa --token o usa variable NEXO_TOKEN.", file=sys.stderr)
        sys.exit(1)

    # ---------------- Escucha interactiva
    if args.cmd == "escuchar":
        await cmd_escuchar(args)
        return

    # ---------------- Arbol de archivos
    if args.cmd == "arbol":
        d = rest_get(args, "/api/arbol")
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        if "arbol" in d:
            print(f"\n📂 Workspace NEXO ({len(d['arbol'])} proyectos):")
            for p in d["arbol"]:
                print(f"\n  📁 {p['nombre']}/")
                for f in p["archivos"]:
                    print(f"     📄 {f}")
            print()
        else:
            print("Error: " + d.get("error", "desconocido"), file=sys.stderr)
        return

    # ---------------- Historial de chat
    if args.cmd == "historial":
        d = rest_get(args, f"/api/historial?proyecto={urllib.parse.quote(args.proyecto)}")
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        for m in d.get("historial", []):
            if m["de"] == "sistema":
                print(f"  ⚙️  {m['texto']}")
            else:
                print(f"  [{m['fecha']}] {m['de']}: {m['texto']}")
        return

    # ---------------- Leer archivo
    if args.cmd == "leer":
        d = rest_get(args, f"/api/archivo?proyecto={urllib.parse.quote(args.proyecto)}&ruta={urllib.parse.quote(args.ruta)}")
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        if "contenido" in d:
            print(d["contenido"])
        else:
            print("Error: " + d.get("error", "no se pudo leer"), file=sys.stderr)
            sys.exit(1)
        return

    # ---------------- Escribir archivo
    if args.cmd == "escribir":
        contenido = args.contenido
        if contenido == "-":
            contenido = sys.stdin.read()
        resp = await enviar_y_esperar(
            args,
            {"tipo": "archivo_escribir", "proyecto": args.proyecto, "ruta": args.ruta, "contenido": contenido},
            hasta="operacion_ok",
            timeout=15,
        )
        if any(m.get("tipo") == "error" for m in resp):
            print("Error: " + next(m["texto"] for m in resp if m.get("tipo") == "error"), file=sys.stderr)
            sys.exit(1)
        print(f"✓ Archivo '{args.ruta}' guardado en proyecto '{args.proyecto}'.")
        return

    # ---------------- Borrar archivo
    if args.cmd == "borrar":
        if not args.confirmar:
            print("Usa --confirmar para borrar el archivo.", file=sys.stderr)
            sys.exit(1)
        resp = await enviar_y_esperar(
            args,
            {"tipo": "archivo_borrar", "proyecto": args.proyecto, "ruta": args.ruta, "confirmar": True},
            hasta="operacion_ok",
            timeout=15,
        )
        print(f"✓ '{args.ruta}' eliminado.")
        return

    # ---------------- Renombrar
    if args.cmd == "renombrar":
        resp = await enviar_y_esperar(
            args,
            {"tipo": "archivo_renombrar", "proyecto": args.proyecto, "ruta": args.ruta, "nuevo": args.nuevo},
            hasta="operacion_ok",
            timeout=15,
        )
        print(f"✓ Renombrado: {args.ruta} → {args.nuevo}")
        return

    # ---------------- Subir archivo
    if args.cmd == "subir":
        loc = Path(args.local)
        if not loc.exists():
            print(f"No existe el archivo local: {loc}", file=sys.stderr)
            sys.exit(1)
        datos = base64.b64encode(loc.read_bytes()).decode()
        dest = args.destino or loc.name
        print(f"Subiendo {loc.name} ({loc.stat().st_size} bytes) → {args.proyecto}/{dest}…")
        resp = await enviar_y_esperar(
            args,
            {"tipo": "archivo_subir", "proyecto": args.proyecto, "ruta": dest, "datos": datos},
            hasta="operacion_ok",
            timeout=60,
        )
        print("✓ Archivo subido con éxito.")
        return

    # ---------------- Descargar archivo
    if args.cmd == "descargar":
        url = f"{args.server.rstrip('/')}/api/descargar?proyecto={urllib.parse.quote(args.proyecto)}&ruta={urllib.parse.quote(args.ruta)}&token={urllib.parse.quote(args.token)}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                datos = r.read()
            salida = args.salida or Path(args.ruta).name
            Path(salida).write_bytes(datos)
            print(f"✓ Descargado {len(datos)} bytes → {salida}")
        except Exception as e:
            print(f"Error al descargar: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # ---------------- Nuevo proyecto
    if args.cmd == "nuevo":
        resp = await enviar_y_esperar(args, {"tipo": "proyecto_nuevo", "nombre": args.nombre}, hasta="arbol", timeout=15)
        print(f"✓ Proyecto '{args.nombre}' creado.")
        return

    # ---------------- Proyecto borrar
    if args.cmd == "proyecto-borrar":
        if not args.confirmar:
            print("Usa --confirmar para eliminar el proyecto.", file=sys.stderr)
            sys.exit(1)
        resp = await enviar_y_esperar(args, {"tipo": "proyecto_borrar", "nombre": args.nombre, "confirmar": True}, hasta="arbol", timeout=20)
        print(f"✓ Proyecto '{args.nombre}' eliminado.")
        return

    # ---------------- Chat
    if args.cmd == "chat":
        resp = await enviar_y_esperar(args, {"tipo": "chat", "proyecto": args.proyecto, "texto": args.texto}, hasta="chat", timeout=15)
        for m in resp:
            if m.get("tipo") == "chat":
                imprimir_mensaje_consola(m)
        return

    # ---------------- Run comando
    if args.cmd == "run":
        resp = await enviar_y_esperar(args, {"tipo": "run", "proyecto": args.proyecto, "cmd": args.comando, "id": "cli"}, hasta="run_fin", timeout=360)
        for m in resp:
            if m.get("tipo") in ("output", "run_fin"):
                imprimir_mensaje_consola(m)
        return

    # ---------------- Git
    if args.cmd == "git":
        resp = await enviar_y_esperar(args, {"tipo": "git", "accion": args.accion, "msg": args.msg}, hasta="run_fin", timeout=120)
        for m in resp:
            if m.get("tipo") in ("output", "run_fin"):
                imprimir_mensaje_consola(m)
        return

    # ---------------- AI-INBOX
    if args.cmd == "ai-inbox":
        query = f"/api/ai/inbox?agente={urllib.parse.quote(args.agente)}&proyecto={urllib.parse.quote(args.proyecto)}&pendientes={'true' if args.pendientes else 'false'}"
        d = rest_get(args, query)
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        mensajes = d.get("mensajes", [])
        print(f"\n📬 Buzón IA ({len(mensajes)} mensajes):")
        for m in mensajes:
            st = "⏳ Pendiente" if m.get("estado") == "pendiente" else "✅ Completado"
            print(f"\n  • [{m.get('id')}] {m.get('titulo')} ({st})")
            print(f"    De: {m.get('de')} → Para: {m.get('para')} | Tipo: {m.get('tipo')}")
            print(f"    Contenido: {m.get('contenido')}")
            if m.get("archivos"):
                print(f"    Archivos: {', '.join(m.get('archivos'))}")
            if m.get("codigo"):
                print(f"    Código:\n{m.get('codigo')}")
        print()
        return

    # ---------------- AI-SEND
    if args.cmd == "ai-send":
        archivos = [a.strip() for a in args.archivos.split(",") if a.strip()] if args.archivos else []
        payload = {
            "de": args.nombre,
            "para": args.para,
            "proyecto": args.proyecto,
            "tipo": args.tipo,
            "titulo": args.titulo,
            "contenido": args.contenido,
            "archivos": archivos,
            "codigo": args.codigo,
        }
        d = rest_post(args, "/api/ai/msg", payload)
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        if d.get("ok"):
            m = d.get("mensaje", {})
            print(f"✓ Mensaje IA enviado con ID: {m.get('id')}")
        else:
            print(f"Error al enviar mensaje IA: {d.get('error')}", file=sys.stderr)
        return

    # ---------------- AI-REPLY
    if args.cmd == "ai-reply":
        payload = {
            "id": args.id,
            "de": args.nombre,
            "contenido": args.contenido,
            "estado": args.estado,
            "codigo": args.codigo,
        }
        d = rest_post(args, "/api/ai/reply", payload)
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        if d.get("ok"):
            print(f"✓ Respuesta enviada a tarea {args.id}.")
        else:
            print(f"Error al responder: {d.get('error')}", file=sys.stderr)
        return

    # ---------------- AI-TASKS
    if args.cmd == "ai-tasks":
        d = rest_get(args, f"/api/ai/tasks?proyecto={urllib.parse.quote(args.proyecto)}")
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        tareas = d.get("tareas", [])
        print(f"\n📋 Tablero de Tareas Colaborativo ({len(tareas)} tareas):")
        for t in tareas:
            st_map = {"todo": "📝 POR HACER", "in_progress": "⚡ EN PROGRESO", "done": "✅ COMPLETADA"}
            print(f"\n  • [{t.get('id')}] {t.get('titulo')} [{st_map.get(t.get('estado'), t.get('estado'))}]")
            print(f"    Asignado a: {t.get('asignado')} | Prioridad: {t.get('prioridad')}")
            if t.get("descripcion"):
                print(f"    Descripción: {t.get('descripcion')}")
            if t.get("archivos"):
                print(f"    Archivos: {', '.join(t.get('archivos'))}")
        print()
        return

    # ---------------- AI-TASK-ADD
    if args.cmd == "ai-task-add":
        archivos = [a.strip() for a in args.archivos.split(",") if a.strip()] if args.archivos else []
        payload = {
            "proyecto": args.proyecto,
            "titulo": args.titulo,
            "descripcion": args.descripcion,
            "asignado": args.asignado,
            "creador": args.nombre,
            "prioridad": args.prioridad,
            "archivos": archivos,
            "estado": "todo",
        }
        d = rest_post(args, "/api/ai/task", payload)
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        if d.get("ok"):
            print(f"✓ Tarea creada con ID: {d.get('tarea', {}).get('id')}")
        else:
            print(f"Error al crear tarea: {d.get('error')}", file=sys.stderr)
        return

    # ---------------- AI-STATUS
    if args.cmd == "ai-status":
        d = rest_get(args, "/api/status")
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        print("\n⚡ Estado del Sistema NEXO:")
        print(f"   Sistema:     {d.get('sistema')} ({d.get('os')} {d.get('arch')})")
        print(f"   Workspace:   {d.get('workspace')}")
        print(f"   Proyectos:   {d.get('proyectos')}")
        print(f"   Conectados:  {d.get('conectados')}")
        print(f"   Sandbox:     {d.get('bwrap_sandbox')}")
        cfg_d = d.get("config", {})
        print(f"   Identidades: {cfg_d.get('nombre')} & {cfg_d.get('companero')}")
        print(f"   IAs:         {cfg_d.get('ia_nombre')} & {cfg_d.get('ia_companero')}\n")
        return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
