#!/usr/bin/env python3
from __future__ import annotations

"""NEXO Agent Bridge — Daemon de monitoreo y sincronización para agentes IA (Hermes & Antigravity).

Permite que una IA ejecute en segundo plano o mediante cron:
  - Mantener presencia en línea en NEXO
  - Notificar cuando la IA compañera asigna una tarea o envía una consulta
  - Disparar scripts o hooks automáticamente al recibir nuevos mensajes
"""

import argparse
import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent


def cargar_config() -> dict:
    for ruta in [BASE / "config.json", Path.home() / "nexo" / "config.json"]:
        if ruta.exists():
            try:
                return json.loads(ruta.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {"token": "", "nombre": "hermes-2", "ia_nombre": "hermes"}


def api_request(server: str, token: str, endpoint: str, data: dict | None = None) -> dict:
    sep = "&" if "?" in endpoint else "?"
    url = f"{server.rstrip('/')}{endpoint}{sep}token={urllib.parse.quote(token)}"
    if data is not None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


async def loop_monitoreo(server: str, token: str, agente: str, proyecto: str, intervalo: int, hook_cmd: str):
    print(f"🤖 [Agent Bridge] Iniciado para '{agente}' contra {server}")
    print(f"   Intervalo de sondeo: {intervalo}s | Proyecto: {proyecto or 'todos'}")
    vistos: set[str] = set()

    # Cargar mensajes iniciales para no duplicar alertas
    try:
        query = f"/api/ai/inbox?agente={urllib.parse.quote(agente)}&pendientes=true"
        resp = api_request(server, token, query)
        for m in resp.get("mensajes", []):
            vistos.add(m["id"])
    except Exception as e:
        print(f"⚠️ Aviso inicial: no se pudo conectar al servidor ({e}). Reintentando...")

    while True:
        try:
            # 1. Heartbeat de presencia
            api_request(server, token, "/api/ai/presence", {
                "agente": agente,
                "estado": "activo",
                "actividad": "Monitoreando tareas",
            })

            # 2. Consultar buzón
            query = f"/api/ai/inbox?agente={urllib.parse.quote(agente)}&pendientes=true"
            if proyecto:
                query += f"&proyecto={urllib.parse.quote(proyecto)}"
            resp = api_request(server, token, query)
            mensajes = resp.get("mensajes", [])

            for m in mensajes:
                mid = m.get("id")
                if mid and mid not in vistos:
                    vistos.add(mid)
                    print("\n" + "!" * 60)
                    print("⚡ [NUEVA TAREA/MENSAJE IA RECIBIDO]")
                    print(f"   ID:        {mid}")
                    print(f"   De:        {m.get('de')}")
                    print(f"   Tipo:      {m.get('tipo')}")
                    print(f"   Título:    {m.get('titulo')}")
                    print(f"   Contenido: {m.get('contenido')}")
                    if m.get("archivos"):
                        print(f"   Archivos:  {', '.join(m.get('archivos'))}")
                    print("!" * 60 + "\n")

                    if hook_cmd:
                        cmd = hook_cmd.replace("{id}", mid).replace("{titulo}", shlex_quote(m.get("titulo", "")))
                        print(f"⚙️ Ejecutando hook: {cmd}")
                        os.system(cmd)

        except Exception as e:
            print(f"[Agent Bridge] Error en ciclo: {e}")

        await asyncio.sleep(intervalo)


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def main():
    cfg = cargar_config()
    p = argparse.ArgumentParser(description="Daemon de enlace entre IAs para NEXO")
    p.add_argument("--server", default=os.environ.get("NEXO_SERVER", "http://127.0.0.1:8787"))
    p.add_argument("--token", default=os.environ.get("NEXO_TOKEN", cfg.get("token", "")))
    p.add_argument("--agente", default=os.environ.get("NEXO_IA", cfg.get("ia_nombre", "hermes")))
    p.add_argument("--proyecto", default="")
    p.add_argument("--intervalo", type=int, default=15, help="Segundos entre comprobaciones")
    p.add_argument("--hook", default="", help="Comando opcional a ejecutar al recibir un mensaje (admite {id})")
    p.add_argument("--check-once", action="store_true", help="Comprobar una sola vez y salir (ideal para cron)")

    args = p.parse_args()

    if not args.token:
        print("❌ Error: falta el token de acceso. Configúralo en config.json o con --token", file=sys.stderr)
        sys.exit(1)

    if args.check_once:
        query = f"/api/ai/inbox?agente={urllib.parse.quote(args.agente)}&pendientes=true"
        resp = api_request(args.server, args.token, query)
        pendientes = resp.get("mensajes", [])
        print(f"Tareas pendientes para '{args.agente}': {len(pendientes)}")
        for m in pendientes:
            print(f"- [{m.get('id')}] {m.get('titulo')} (de: {m.get('de')}): {m.get('contenido')}")
        return

    try:
        asyncio.run(loop_monitoreo(args.server, args.token, args.agente, args.proyecto, args.intervalo, args.hook))
    except KeyboardInterrupt:
        print("\nAgent bridge detenido.")


if __name__ == "__main__":
    main()
