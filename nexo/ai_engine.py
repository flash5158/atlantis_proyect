#!/usr/bin/env python3
from __future__ import annotations

"""NEXO AI Engine 2.0 — Motor Real de IA para Hermes y Gemini.

Integración auténtica (CERO SIMULACIONES):
  1. Google Gemini 3.6 Flash / 2.5 Pro:
     - Conexión directa a la API oficial de Google AI Studio (generativelanguage.googleapis.com).
     - Detección automática de GOOGLE_API_KEY desde ~/.hermes/.env o variables de entorno.
     - Fallback inteligente entre gemini-3.6-flash, gemini-3.5-flash y gemini-2.5-pro.
  2. Nous Research Hermes Agent CLI:
     - Detección del binario real en ~/.local/bin/hermes.
     - Configurado nativamente con Gemini 3.6 Flash en ~/.hermes/config.yaml.
     - Ejecución real en el workspace del proyecto con herramientas locales (--in <DIR> --yolo).
     - Streaming asíncrono de stdout/stderr hacia WebSockets.
  3. Colaboración Dual Autónoma (Hermes ↔ Gemini):
     - Gemini analiza el objetivo y genera la arquitectura y plan de código.
     - Hermes ejecuta el plan en los archivos locales usando sus capacidades de agente.
     - Gemini audita y valida el resultado.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

import httpx

BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parent
CONFIG_FILE = BASE / "config.json"
HERMES_DIR = Path.home() / ".hermes"
HERMES_ENV_FILE = HERMES_DIR / ".env"
HERMES_BIN_DEFAULT = Path.home() / ".local" / "bin" / "hermes"


def detectar_hermes_bin() -> Optional[Path]:
    """Localiza el binario ejecutable real de Hermes Agent."""
    if HERMES_BIN_DEFAULT.is_file() and os.access(HERMES_BIN_DEFAULT, os.X_OK):
        return HERMES_BIN_DEFAULT
    which_path = shutil.which("hermes")
    if which_path:
        p = Path(which_path)
        if p.is_file():
            return p
    hermes_agent_venv = HERMES_DIR / "hermes-agent" / "venv" / "bin" / "hermes"
    if hermes_agent_venv.is_file():
        return hermes_agent_venv
    return None


def leer_hermes_env() -> dict[str, str]:
    """Carga claves secretas desde ~/.hermes/.env de forma segura."""
    env_vars: dict[str, str] = {}
    if HERMES_ENV_FILE.is_file():
        try:
            for line in HERMES_ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k and v:
                    env_vars[k] = v
        except Exception:
            pass
    return env_vars


def obtener_config_ia() -> dict[str, Any]:
    """Retorna la configuración activa de IA priorizando credenciales auténticas."""
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    hermes_env = leer_hermes_env()

    # Resolver GOOGLE_API_KEY
    gemini_key = (
        cfg.get("ai_api_key")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or hermes_env.get("GOOGLE_API_KEY")
        or hermes_env.get("GEMINI_API_KEY")
        or ""
    )

    hermes_bin = detectar_hermes_bin()

    return {
        "proveedor": cfg.get("ai_proveedor", "gemini"),
        "api_key": gemini_key,
        "modelo": cfg.get("ai_modelo", "gemini-3.6-flash"),
        "hermes_bin": str(hermes_bin) if hermes_bin else "",
        "hermes_disponible": hermes_bin is not None,
        "gemini_disponible": bool(gemini_key),
    }


def guardar_config_ia(proveedor: str, api_key: str, modelo: str = "gemini-3.6-flash") -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    cfg["ai_proveedor"] = proveedor
    if api_key:
        cfg["ai_api_key"] = api_key.strip()
    if modelo:
        cfg["ai_modelo"] = modelo.strip()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


# ---------------------------------------------------------------- Gemini Real API
async def consultar_gemini(
    prompt: str,
    contexto_archivo: str = "",
    nombre_archivo: str = "",
    modelo: str = "gemini-3.6-flash",
    sistema: str = "",
    historial: Optional[list] = None,
) -> tuple[str, Optional[str]]:
    """Ejecuta una consulta real a Google Gemini usando la API oficial.

    Retorna (texto_respuesta, codigo_extraido).
    """
    cfg = obtener_config_ia()
    key = cfg["api_key"]
    if not key:
        return (
            "Error: No se encontró una clave de Gemini (GOOGLE_API_KEY). "
            "Asegúrate de que esté configurada en ~/.hermes/.env o en el panel de configuración.",
            None,
        )

    system_text = sistema or (
        "Eres Gemini 3.6 Flash, el Co-Piloto de IA y Arquitecto de Software en la plataforma NEXO Atlantis. "
        "Colaboras en tiempo real con dos desarrolladores y con Hermes Agent (Nous Research). "
        "Sé ultra técnico, conciso, limpio y preciso. Proporciona soluciones completas listas para producción. "
        "Si generas código, incluye el bloque de código markdown con el lenguaje correspondiente."
    )

    full_text = prompt
    if nombre_archivo and contexto_archivo:
        full_text += f"\n\n--- Archivo: {nombre_archivo} ---\n{contexto_archivo[:12000]}\n--- Fin del archivo ---"

    contents = []
    # Añadir historial si existe
    if historial:
        for turn in historial[-6:]:
            role = "user" if turn.get("de") not in ("gemini", "Gemini", "Antigravity", "antigravity") else "model"
            contents.append({"role": role, "parts": [{"text": turn.get("texto") or turn.get("contenido", "")}]})

    contents.append({
        "role": "user",
        "parts": [{"text": f"{system_text}\n\n{full_text}"}],
    })

    # Modelos de fallback en orden de preferencia
    modelos_a_probar = [modelo, "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-pro", "gemini-flash-latest"]
    modelos_vistos = set()
    modelos_ordenados = []
    for m in modelos_a_probar:
        if m and m not in modelos_vistos:
            modelos_ordenados.append(m)
            modelos_vistos.add(m)

    ultimo_error = ""
    for mod in modelos_ordenados:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{mod}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                res = await client.post(
                    url,
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={"contents": contents},
                )
                if res.status_code == 200:
                    data = res.json()
                    cands = data.get("candidates", [])
                    if cands and "content" in cands[0]:
                        parts = cands[0]["content"].get("parts", [])
                        texto_resp = "".join(p.get("text", "") for p in parts)
                        codigo_ext = extraer_codigo(texto_resp)
                        return (texto_resp, codigo_ext)
                elif res.status_code == 404:
                    ultimo_error = f"Modelo {mod} no disponible (404)."
                    continue
                elif res.status_code == 429:
                    ultimo_error = f"Cuota excedida para {mod} (429). Probando fallback..."
                    await asyncio.sleep(1)
                    continue
                else:
                    ultimo_error = f"Error HTTP {res.status_code}: {res.text[:200]}"
        except Exception as e:
            ultimo_error = f"Excepción de conexión ({mod}): {e}"

    return (f"Error al conectar con Gemini: {ultimo_error}", None)


# ---------------------------------------------------------------- Hermes Real CLI Runner
async def ejecutar_hermes(
    prompt: str,
    cwd: Optional[Path] = None,
    yolo: bool = True,
    on_chunk: Optional[Callable[[str], None]] = None,
    timeout: int = 180,
) -> tuple[int, str]:
    """Ejecuta el binario real de Hermes Agent en el workspace.

    Utiliza el modo oneshot (-z) con bypass de aprobaciones (--yolo)
    para ejecución autónoma real en el proyecto.
    """
    hermes_bin = detectar_hermes_bin()
    if not hermes_bin:
        msg = (
            "Error: No se encontró el binario de Hermes Agent en ~/.local/bin/hermes. "
            "Asegúrate de tener Hermes instalado."
        )
        if on_chunk:
            on_chunk(msg + "\n")
        return (1, msg)

    work_dir = cwd if (cwd and cwd.is_dir()) else REPO_ROOT
    args = [str(hermes_bin), "-z", prompt, "--in", str(work_dir)]
    if yolo:
        args.append("--yolo")

    env = dict(os.environ)
    hermes_env = leer_hermes_env()
    env.update(hermes_env)
    env.setdefault("TERM", "xterm-256color")

    if on_chunk:
        on_chunk(f"⚡ [HERMES INICIADO] Directorio: {work_dir.name}\n")
        on_chunk(f"🤖 Ejecutando: hermes -z \"{prompt[:60]}...\"\n\n")

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(work_dir),
            env=env,
        )

        salida_completa = []
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                salida_completa.append(decoded)
                if on_chunk:
                    on_chunk(decoded)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            timeout_msg = "\n⚠️ [HERMES TIMEOUT] La operación superó el límite de tiempo.\n"
            salida_completa.append(timeout_msg)
            if on_chunk:
                on_chunk(timeout_msg)
            return (-1, "".join(salida_completa))

        await proc.wait()
        ret_code = proc.returncode or 0
        total_text = "".join(salida_completa)
        if on_chunk:
            on_chunk(f"\n✅ [HERMES FINALIZADO] Código de salida: {ret_code}\n")
        return (ret_code, total_text)

    except Exception as e:
        err_msg = f"Error ejecutando Hermes: {e}\n"
        if on_chunk:
            on_chunk(err_msg)
        return (1, err_msg)


# ---------------------------------------------------------------- Colaboración Dual (Hermes ↔ Gemini)
async def colaboracion_dual(
    objetivo: str,
    cwd: Optional[Path] = None,
    on_event: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Ejecuta una colaboración autónoma completa entre Gemini y Hermes.

    1. Gemini diseña la arquitectura, plan y cambios.
    2. Hermes ejecuta las herramientas locales, crea o edita archivos y prueba el código.
    3. Gemini revisa la salida final y firma el informe.
    """
    work_dir = cwd if (cwd and cwd.is_dir()) else REPO_ROOT

    def emitir(fase: str, de: str, mensaje: str, datos: Any = None):
        if on_event:
            on_event({
                "tipo": "dual_colab_event",
                "fase": fase,
                "de": de,
                "mensaje": mensaje,
                "datos": datos,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            })

    # Fase 1: Arquitectura con Gemini
    emitir("planificacion", "Gemini", f"Analizando requerimiento: '{objetivo}' y generando plan de ejecución...")
    plan_prompt = (
        f"OBJETIVO DE DESARROLLO:\n{objetivo}\n\n"
        "Eres el Arquitecto de Software (Gemini 3.6). Tu colega Hermes Agent ejecutará las modificaciones de código "
        "en los archivos locales. Genera:\n"
        "1. Diagnóstico breve y arquitectura propuesta.\n"
        "2. Instrucciones precisas paso a paso para que Hermes ejecute con sus herramientas locales.\n"
        "3. Especificación exacta de archivos a crear o modificar con código completo."
    )
    plan_texto, plan_codigo = await consultar_gemini(plan_prompt)
    emitir("planificacion_completa", "Gemini", "Plan de arquitectura generado.", {
        "texto": plan_texto,
        "codigo": plan_codigo,
    })

    # Fase 2: Ejecución con Hermes
    emitir("ejecucion", "Hermes", "Recibiendo especificación de Gemini y ejecutando en el workspace...")
    hermes_prompt = (
        f"Instrucción de desarrollo acordada con Gemini:\n\n{objetivo}\n\n"
        f"Plan y detalles de Gemini:\n{plan_texto[:3000]}\n\n"
        "Por favor ejecuta los cambios necesarios en el proyecto, crea o modifica los archivos correspondientes "
        "y verifica que no haya errores de sintaxis. Resume los cambios realizados."
    )

    def hermes_stream(chunk: str):
        emitir("ejecucion_stream", "Hermes", chunk)

    ret, salida_hermes = await ejecutar_hermes(
        hermes_prompt,
        cwd=work_dir,
        yolo=True,
        on_chunk=hermes_stream,
    )
    emitir("ejecucion_completa", "Hermes", f"Ejecución finalizada (Código {ret}).", {
        "salida": salida_hermes,
        "retcode": ret,
    })

    # Fase 3: Auditoría y Revisión con Gemini
    emitir("revision", "Gemini", "Auditando los cambios realizados por Hermes...")
    review_prompt = (
        f"Revisión de cambios para el objetivo: '{objetivo}'\n\n"
        f"Salida y modificaciones realizadas por Hermes Agent:\n{salida_hermes[:3500]}\n\n"
        "Realiza una breve auditoría de calidad: verifica si el objetivo se cumplió, "
        "si hay advertencias o pruebas adicionales recomendadas, y confirma el estado final."
    )
    rev_texto, _ = await consultar_gemini(review_prompt)
    emitir("revision_completa", "Gemini", "Auditoría completada exitosamente.", {
        "informe": rev_texto,
    })

    return {
        "ok": ret == 0,
        "objetivo": objetivo,
        "plan_gemini": plan_texto,
        "ejecucion_hermes": salida_hermes,
        "auditoria_gemini": rev_texto,
    }


# ---------------------------------------------------------------- Utilidades y Estado
def extraer_codigo(texto: str) -> Optional[str]:
    """Extrae el primer bloque de código relevante de una respuesta markdown."""
    match = re.search(r"```(?:\w+)?\n([\s\S]+?)\n```", texto)
    if match:
        return match.group(1)
    return None


async def verificar_estado_ia() -> dict[str, Any]:
    """Inspecciona y valida en tiempo real la conexión de Hermes y Gemini."""
    hermes_bin = detectar_hermes_bin()
    cfg = obtener_config_ia()
    key = cfg.get("api_key", "")

    # Verificar Gemini
    gemini_ok = False
    gemini_modelo = "gemini-3.6-flash"
    gemini_msg = "Sin clave configurada"
    if key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash?key={key}"
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    gemini_ok = True
                    gemini_msg = "Operacional y Conectado (Google AI Studio)"
                else:
                    gemini_msg = f"HTTP {res.status_code}"
        except Exception as e:
            gemini_msg = str(e)

    # Verificar Hermes
    hermes_ok = hermes_bin is not None
    hermes_version = ""
    if hermes_ok:
        try:
            res = subprocess.run([str(hermes_bin), "--version"], capture_output=True, text=True, timeout=5)
            hermes_version = res.stdout.strip() or res.stderr.strip()
        except Exception:
            hermes_version = "Detectado"

    return {
        "gemini": {
            "conectado": gemini_ok,
            "modelo": gemini_modelo,
            "mensaje": gemini_msg,
            "tiene_clave": bool(key),
            "clave_parcial": (key[:6] + "..." + key[-4:]) if len(key) > 12 else ("Configurada" if key else "Sin clave"),
        },
        "hermes": {
            "instalado": hermes_ok,
            "ruta": str(hermes_bin) if hermes_bin else "",
            "version": hermes_version,
            "modelo_hermes": "gemini-3.6-flash",
            "modo": "Nous Research Agent (Local CLI)",
        },
        "workspace": str(REPO_ROOT),
    }


# Compatibilidad hacia atrás con server.py
async def generar_respuesta_ia(
    prompt: str,
    contexto_archivo: str = "",
    nombre_archivo: str = "",
    agente_nombre: str = "Gemini",
) -> tuple[str, Optional[str]]:
    """Función de compatibilidad para endpoints de chat existentes."""
    if agente_nombre.lower() in ("hermes", "hermes agent"):
        hermes_bin = detectar_hermes_bin()
        if hermes_bin:
            ret, out = await ejecutar_hermes(prompt, cwd=REPO_ROOT, yolo=True)
            return (out, extraer_codigo(out))
    return await consultar_gemini(prompt, contexto_archivo=contexto_archivo, nombre_archivo=nombre_archivo)
