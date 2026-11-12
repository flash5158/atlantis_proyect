#!/usr/bin/env python3
from __future__ import annotations

"""Diagnóstico y Salud del Sistema NEXO Atlantis (Gemini & Hermes).

Verifica en tiempo real la conectividad, latencia y respuesta
de los motores Gemini 3.6 Flash y Hermes Agent CLI.
"""

import asyncio
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Optional, TypedDict
import httpx


class ServiceStatus(TypedDict):
    status: str
    modelo: str
    latency_ms: float
    error: Optional[str]


class SystemHealth(TypedDict):
    timestamp: float
    workspace: str
    gemini: ServiceStatus
    hermes: ServiceStatus


def leer_gemini_key() -> str:
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    if not key:
        env_file = os.path.expanduser("~/.hermes/.env")
        if os.path.isfile(env_file):
            try:
                with open(env_file, "r") as f:
                    for line in f:
                        if line.startswith("GOOGLE_API_KEY="):
                            key = line.strip().split("=", 1)[1].strip("\"'")
                            break
            except Exception:
                pass
    return key


async def verificar_gemini() -> ServiceStatus:
    """Verifica conectividad y latencia con Gemini 3.6 Flash."""
    key = leer_gemini_key()
    if not key:
        return {
            "status": "sin_clave",
            "modelo": "gemini-3.6-flash",
            "latency_ms": 0.0,
            "error": "GOOGLE_API_KEY no encontrada",
        }

    start = time.perf_counter()
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash?key=" + key
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            latency = round((time.perf_counter() - start) * 1000, 2)
            if resp.status_code == 200:
                return {
                    "status": "healthy",
                    "modelo": "gemini-3.6-flash",
                    "latency_ms": latency,
                    "error": None,
                }
            return {
                "status": "unhealthy",
                "modelo": "gemini-3.6-flash",
                "latency_ms": latency,
                "error": f"HTTP {resp.status_code}: {resp.text[:120]}",
            }
    except Exception as e:
        latency = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "error",
            "modelo": "gemini-3.6-flash",
            "latency_ms": latency,
            "error": str(e),
        }


async def verificar_hermes() -> ServiceStatus:
    """Verifica presencia y ejecución del binario local de Hermes."""
    hermes_bin = os.path.expanduser("~/.local/bin/hermes")
    if not os.path.isfile(hermes_bin):
        hermes_bin = shutil.which("hermes") or ""

    if not hermes_bin:
        return {
            "status": "no_instalado",
            "modelo": "desconocido",
            "latency_ms": 0.0,
            "error": "Binario de Hermes no encontrado en ~/.local/bin/hermes",
        }

    start = time.perf_counter()
    try:
        loop = asyncio.get_running_loop()
        p = await loop.run_in_executor(
            None,
            lambda: subprocess.run([hermes_bin, "--version"], capture_output=True, text=True, timeout=5)
        )
        latency = round((time.perf_counter() - start) * 1000, 2)
        ver = p.stdout.strip() or p.stderr.strip() or "v0.21.0"
        return {
            "status": "healthy",
            "modelo": "gemini-3.6-flash (Nous Hermes CLI)",
            "latency_ms": latency,
            "error": None,
        }
    except Exception as e:
        latency = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "error",
            "modelo": "hermes",
            "latency_ms": latency,
            "error": str(e),
        }


async def diagnosticar() -> SystemHealth:
    gemini_task = verificar_gemini()
    hermes_task = verificar_hermes()
    res_gemini, res_hermes = await asyncio.gather(gemini_task, hermes_task)
    return {
        "timestamp": time.time(),
        "workspace": os.path.dirname(os.path.abspath(__file__)),
        "gemini": res_gemini,
        "hermes": res_hermes,
    }


if __name__ == "__main__":
    import json
    print("Iniciando auditoría de salud del sistema NEXO Atlantis...")
    reporte = asyncio.run(diagnosticar())
    print(json.dumps(reporte, indent=2, ensure_ascii=False))
