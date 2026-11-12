"""Atlantis Studio — Creador y gestor de servidores MCP.

Crea servidores MCP (Model Context Protocol) reales con FastMCP dentro del
workspace: el usuario define herramientas (nombre, descripción, esquema JSON
de parámetros y código Python) y la app genera un servidor funcional en
mcps/<nombre>/server.py + mcp.json.

Gestión: listar, arrancar/detener el proceso real, y panel de prueba con
list_tools + call_tool contra un cliente MCP real (stdio). Nada simulado:
si el paquete `mcp` no está instalado, se dice honestamente.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import mcp as _mcp_pkg

    _MCP_MAJOR = int(str(getattr(_mcp_pkg, "__version__", "1")).split(".")[0])
    if _MCP_MAJOR >= 2:
        raise ImportError(
            "Se detectó mcp 2.x; Atlantis Studio genera servidores v1 (FastMCP). "
            "Instala con: pip install 'mcp<2'"
        )

    MCP_AVAILABLE = True
    MCP_IMPORT_ERROR = ""
except Exception as e:  # pragma: no cover - depende del entorno
    MCP_AVAILABLE = False
    MCP_IMPORT_ERROR = str(e)
    ClientSession = None  # type: ignore
    StdioServerParameters = None  # type: ignore
    stdio_client = None  # type: ignore

_BASE_DIR: Path | None = None
_PROCS: dict[str, subprocess.Popen] = {}
_LOGS: dict[str, deque] = {}
_LOCK = threading.Lock()

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TYPE_MAP = {
    "string": ("str", '""'),
    "integer": ("int", "0"),
    "number": ("float", "0.0"),
    "boolean": ("bool", "False"),
    "array": ("list", "[]"),
    "object": ("dict", "{}"),
}


def set_base_dir(path: Path | str) -> None:
    """Raíz donde viven los MCPs (la llama server.py al arrancar/cambiar workspace)."""
    global _BASE_DIR
    _BASE_DIR = Path(path)


def get_mcp_dir() -> Path:
    base = _BASE_DIR or Path.cwd()
    d = base / "mcps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_tool(tool: dict[str, Any], idx: int) -> tuple[str, str, dict, str]:
    nombre = str(tool.get("name", "") or "").strip()
    if not _NAME_RE.match(nombre):
        raise ValueError(f"Herramienta #{idx + 1}: nombre inválido '{nombre}' (usa letras, números y _).")
    descripcion = str(tool.get("description", "") or "").strip() or f"Herramienta {nombre}"
    schema = tool.get("params_schema") or {}
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except Exception:
            raise ValueError(f"Herramienta '{nombre}': params_schema no es JSON válido.")
    if not isinstance(schema, dict):
        raise ValueError(f"Herramienta '{nombre}': params_schema debe ser un objeto JSON.")
    for pname, pspec in schema.items():
        if not _NAME_RE.match(str(pname)):
            raise ValueError(f"Herramienta '{nombre}': parámetro inválido '{pname}'.")
        if not isinstance(pspec, dict) or pspec.get("type") not in _TYPE_MAP:
            raise ValueError(
                f"Herramienta '{nombre}': el parámetro '{pname}' necesita type válido ({', '.join(sorted(_TYPE_MAP))})."
            )
    handler = str(tool.get("handler", "") or "")
    if not handler.strip():
        raise ValueError(f"Herramienta '{nombre}': el código no puede estar vacío.")
    # El cuerpo debe compilar dentro de una función.
    try:
        compile("def _t():\n" + _indent(handler, 4), "<handler>", "exec")
    except SyntaxError as e:
        raise ValueError(f"Herramienta '{nombre}': error de sintaxis en el código (línea {e.lineno}): {e.msg}")
    return nombre, descripcion, schema, handler


def _indent(code: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line if line.strip() else line for line in code.splitlines())


def _py_signature(schema: dict[str, Any]) -> str:
    parts = []
    for pname, pspec in schema.items():
        py_type, default = _TYPE_MAP[pspec["type"]]
        parts.append(f"{pname}: {py_type} = {default}")
    return ", ".join(parts)


def _tool_doc(descripcion: str, schema: dict[str, Any]) -> str:
    lines = [descripcion, ""]
    for pname, pspec in schema.items():
        lines.append(f"    {pname} ({pspec['type']}): {pspec.get('description', '')}".rstrip())
    lines.append("")
    lines.append("    Generado por Atlantis Studio — MCP Builder.")
    return "\n".join(lines)


def scaffold_server(name: str, description: str, tools: list[tuple[str, str, dict, str]]) -> str:
    """Genera el código fuente del servidor FastMCP. Retorna el .py completo."""
    chunks = [
        '"""Servidor MCP generado por Atlantis Studio — MCP Builder.',
        "",
        f"    {description}",
        '"""',
        "from mcp.server.fastmcp import FastMCP",
        "",
        f'mcp = FastMCP("{name}")',
        "",
        "",
    ]
    for tname, tdesc, schema, handler in tools:
        sig = _py_signature(schema)
        doc = _tool_doc(tdesc, schema)
        chunks.append("@mcp.tool()")
        chunks.append(f"def {tname}({sig}) -> str:")
        chunks.append(f'    """{doc}"""')
        chunks.append(_indent(handler, 4))
        chunks.append("")
        chunks.append("")
    chunks.append('if __name__ == "__main__":')
    chunks.append("    mcp.run()")
    chunks.append("")
    return "\n".join(chunks)


def create_mcp(name: str, description: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Valida, genera y guarda un MCP real en mcps/<name>/. Retorna el manifiesto."""
    if not MCP_AVAILABLE:
        raise RuntimeError("El paquete `mcp` no está instalado en el servidor. Instálalo con: pip install mcp")
    clean = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _NAME_RE.match(clean):
        raise ValueError(f"Nombre de MCP inválido: '{name}'.")
    if not tools:
        raise ValueError("Define al menos una herramienta.")
    validated = [_validate_tool(t, i) for i, t in enumerate(tools)]

    mcp_dir = get_mcp_dir() / clean
    if mcp_dir.exists():
        raise ValueError(f"Ya existe un MCP llamado '{clean}'. Elige otro nombre.")
    mcp_dir.mkdir(parents=True)

    server_code = scaffold_server(clean, (description or "").strip() or clean, validated)
    (mcp_dir / "server.py").write_text(server_code, encoding="utf-8")
    manifest = {
        "name": clean,
        "description": (description or "").strip(),
        "version": "1.0.0",
        "server": "server.py",
        "runtime": "python",
        "tools": [{"name": t[0], "description": t[1]} for t in validated],
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": "atlantis-studio",
    }
    (mcp_dir / "mcp.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def list_mcps() -> list[dict[str, Any]]:
    out = []
    base = get_mcp_dir()
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        mf = child / "mcp.json"
        if not mf.exists():
            continue
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append(
            {
                **manifest,
                "status": status_of(child.name),
                "path": str(child),
            }
        )
    return out


def status_of(name: str) -> str:
    with _LOCK:
        proc = _PROCS.get(name)
    if proc is None:
        return "detenido"
    return "corriendo" if proc.poll() is None else "detenido"


def start_mcp(name: str) -> dict[str, Any]:
    if not MCP_AVAILABLE:
        raise RuntimeError("El paquete `mcp` no está instalado. Instálalo con: pip install mcp")
    mcp_dir = get_mcp_dir() / name
    if not (mcp_dir / "server.py").exists():
        raise ValueError(f"No existe el MCP '{name}'.")
    with _LOCK:
        proc = _PROCS.get(name)
        if proc and proc.poll() is None:
            return {"ok": True, "status": "corriendo", "detalle": "Ya estaba corriendo."}
        logq: deque = deque(maxlen=200)
        _LOGS[name] = logq
        proc = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=str(mcp_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        _PROCS[name] = proc

    def _drain():
        try:
            for line in proc.stderr:  # type: ignore
                logq.append(line.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True).start()
    return {"ok": True, "status": "corriendo", "pid": proc.pid}


def stop_mcp(name: str) -> dict[str, Any]:
    with _LOCK:
        proc = _PROCS.pop(name, None)
    if not proc:
        return {"ok": True, "status": "detenido", "detalle": "No estaba corriendo."}
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass
    return {"ok": True, "status": "detenido"}


def get_logs(name: str, limit: int = 50) -> list[str]:
    q = _LOGS.get(name)
    if not q:
        return []
    return list(q)[-limit:]


def delete_mcp(name: str) -> dict[str, Any]:
    import shutil

    stop_mcp(name)
    mcp_dir = get_mcp_dir() / name
    if not mcp_dir.exists():
        raise ValueError(f"No existe el MCP '{name}'.")
    # Seguridad: solo borra dentro del directorio de MCPs.
    if mcp_dir.resolve().parent != get_mcp_dir().resolve():
        raise ValueError("Ruta fuera del directorio de MCPs.")
    shutil.rmtree(mcp_dir)
    _LOGS.pop(name, None)
    return {"ok": True}


async def _ephemeral_session(name: str):
    """Cliente MCP real (stdio): genera su propio proceso servidor efímero."""
    if not MCP_AVAILABLE:
        raise RuntimeError("El paquete `mcp` no está instalado. Instálalo con: pip install mcp")
    mcp_dir = get_mcp_dir() / name
    if not (mcp_dir / "server.py").exists():
        raise ValueError(f"No existe el MCP '{name}'.")
    params = StdioServerParameters(command=sys.executable, args=["server.py"], cwd=str(mcp_dir))
    return stdio_client(params)


async def list_tools_live(name: str) -> list[dict[str, Any]]:
    """list_tools contra un servidor MCP real recién generado (stdio)."""
    async with await _ephemeral_session(name) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.list_tools()
            return [
                {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema or {}}
                for t in res.tools
            ]


async def call_tool_live(name: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """call_tool contra un servidor MCP real. Retorna el resultado tal cual."""
    async with await _ephemeral_session(name) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args or {})
            texts = []
            structured = None
            for c in res.content or []:
                t = getattr(c, "type", "")
                if t == "text":
                    texts.append(getattr(c, "text", ""))
                else:
                    texts.append(str(c))
            if getattr(res, "structuredContent", None):
                structured = res.structuredContent
            return {
                "tool": tool,
                "isError": bool(getattr(res, "isError", False)),
                "text": "\n".join(texts),
                "structured": structured,
            }
