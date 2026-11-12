#!/usr/bin/env python3
"""Atlantis Studio como servidor MCP.

Expone el hub de Atlantis Studio como herramientas MCP para que clientes
MCP externos (Claude Code, editores, otros agentes) puedan conectarse:

  - leer_archivo / escribir_archivo — archivos del workspace
  - enviar_mensaje_bus — mensaje al bus de agentes
  - listar_tareas / crear_tarea — tareas del estudio
  - estado_hub — estado general

Uso:
  python3 nexo/atlantis_mcp_server.py --hub http://127.0.0.1:8787 [--token ...]

Como cliente MCP (stdio), p. ej. en mcp_config.json:
  { "atlantis": { "command": "python3",
                  "args": ["/ruta/a/nexo/atlantis_mcp_server.py", "--hub", "http://127.0.0.1:8787"] } }

Sin claves de IA: solo habla con TU hub local.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Falta el paquete 'mcp' (v1). Instálalo con: pip install 'mcp<2'", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("atlantis-studio")

_HUB = "http://127.0.0.1:8787"
_TOKEN = ""


def _api(method: str, path: str, body: dict | None = None) -> dict:
    url = _HUB.rstrip("/") + path
    if _TOKEN:
        sep = "&" if "?" in url else "?"
        url += f"{sep}token={urllib.parse.quote(_TOKEN)}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return {"ok": False, "error": json.loads(e.read().decode("utf-8")).get("error", str(e))}
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def leer_archivo(ruta: str) -> str:
    """Lee un archivo del workspace del hub. ruta: p. ej. 'main.py' o 'proyectos/x/app.js'."""
    res = _api("GET", f"/api/archivo?ruta={urllib.parse.quote(ruta)}")
    if not res.get("ok"):
        return f"Error: {res.get('error')}"
    return res.get("contenido", "")


@mcp.tool()
def escribir_archivo(ruta: str, contenido: str) -> str:
    """Escribe (crea o sobrescribe) un archivo en el workspace del hub."""
    res = _api("POST", f"/api/archivo?ruta={urllib.parse.quote(ruta)}",
               {"contenido": contenido, "autor": "mcp-externo"})
    if res.get("ok"):
        return f"✓ Guardado: {ruta}"
    return f"Error: {res.get('error')} — {res.get('detalle', '')}"


@mcp.tool()
def enviar_mensaje_bus(para: str, texto: str, de: str = "mcp-externo") -> str:
    """Envía un mensaje al bus de agentes del hub (lo ven los agentes y humanos)."""
    res = _api("POST", "/api/agents/message",
               {"from": de, "to": para, "text": texto})
    if res.get("ok") or res.get("id"):
        return "✓ Mensaje enviado al bus."
    return f"Error: {res.get('error')}"


@mcp.tool()
def listar_tareas() -> str:
    """Lista las tareas del estudio."""
    res = _api("GET", "/api/ai/tasks")
    tareas = res.get("tareas", res.get("tasks", [])) if isinstance(res, dict) else []
    if not tareas:
        return "No hay tareas."
    return "\n".join(
        f"- [{t.get('estado', '?')}] {t.get('titulo', '?')} (asignado: {t.get('asignado', '?')})"
        for t in tareas
    )


@mcp.tool()
def crear_tarea(titulo: str, descripcion: str = "", asignado: str = "todos") -> str:
    """Crea una tarea en el estudio."""
    res = _api("POST", "/api/ai/task",
               {"titulo": titulo, "descripcion": descripcion,
                "asignado": asignado, "creador": "mcp-externo"})
    if res.get("ok", True) and not res.get("error"):
        return f"✓ Tarea creada: {titulo}"
    return f"Error: {res.get('error')}"


@mcp.tool()
def estado_hub() -> str:
    """Estado general del hub de Atlantis Studio."""
    res = _api("GET", "/api/status")
    if not isinstance(res, dict):
        return "Sin respuesta del hub."
    return json.dumps(res, ensure_ascii=False)[:2000]


def main() -> None:
    global _HUB, _TOKEN
    ap = argparse.ArgumentParser(description="Atlantis Studio como servidor MCP")
    ap.add_argument("--hub", default="http://127.0.0.1:8787", help="URL del hub")
    ap.add_argument("--token", default="", help="Token del hub (si no es local)")
    args = ap.parse_args()
    _HUB = args.hub
    _TOKEN = args.token
    mcp.run()


if __name__ == "__main__":
    main()
