#!/usr/bin/env python3
from __future__ import annotations

"""NEXO AI Engine — Motor de IA de Atlantis Studio (100% sin API keys).

Toda la inteligencia del estudio proviene de los Hermes Agents conectados por
los propios usuarios, NUNCA de APIs en la nube:

  1. Nous Research Hermes Agent CLI:
     - Detección del binario real en ~/.local/bin/hermes (o PATH).
     - El modelo lo define la configuración del propio Hermes (~/.hermes/config.yaml).
     - Ejecución real en el workspace del proyecto (--in <DIR> --yolo).
     - Streaming asíncrono de stdout/stderr hacia WebSockets.
  2. Colaboración Dual entre agentes (Mi Hermes <-> Hermes de mi amigo):
     - Mi agente propone, el agente del amigo audita, vía el bus de agentes del hub.
     - Sin amigo vinculado: modo individual honesto (mi agente propone y audita).
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable

BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parent
ATLANTIS_USER_DIR = Path.home() / ".atlantis"
try:
    ATLANTIS_USER_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
CONFIG_FILE = ATLANTIS_USER_DIR / "config.json"
BASE_CONFIG_FILE = BASE / "config.json"
HERMES_DIR = Path.home() / ".hermes"
HERMES_ENV_FILE = HERMES_DIR / ".env"
HERMES_BIN_DEFAULT = Path.home() / ".local" / "bin" / "hermes"


def detectar_hermes_bin() -> Path | None:
    """Localiza el binario ejecutable real de Hermes Agent en cualquier ruta estándar o personalizada."""
    home = Path.home()
    hermes_dir = home / ".hermes"
    posibles = [
        home / ".local" / "bin" / "hermes",
        home / "bin" / "hermes",
        home / ".cargo" / "bin" / "hermes",
        hermes_dir / "venv" / "bin" / "hermes",
        hermes_dir / "hermes-agent" / "venv" / "bin" / "hermes",
    ]
    which_path = shutil.which("hermes")
    if which_path:
        posibles.insert(0, Path(which_path))
    else:
        current_path_dirs = {p for p in os.environ.get("PATH", "").split(os.pathsep) if p}
        for sys_dir in ["/usr/local/bin", "/usr/bin", "/opt/hermes/bin"]:
            if sys_dir in current_path_dirs:
                posibles.append(Path(sys_dir) / "hermes")

    for p in posibles:
        try:
            if p.is_file() and os.access(p, os.X_OK):
                return p
        except Exception:
            pass
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
    """Retorna la configuración activa de IA: el agente Hermes local del usuario.

    Atlantis Studio no usa ni pide API keys. El modelo lo define la
    configuración del propio Hermes (~/.hermes/config.yaml).
    """
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    elif BASE_CONFIG_FILE.exists():
        try:
            cfg = json.loads(BASE_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    hermes_bin = detectar_hermes_bin()

    return {
        "proveedor": "hermes",
        "modelo": cfg.get("ai_modelo", "auto"),
        "hermes_bin": str(hermes_bin) if hermes_bin else "",
        "hermes_disponible": hermes_bin is not None,
    }


def guardar_config_ia(proveedor: str = "hermes", modelo: str = "auto") -> dict:
    """Guarda la configuración del agente (sin claves: no se usan ni se piden)."""
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    elif BASE_CONFIG_FILE.exists():
        try:
            cfg = json.loads(BASE_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    cfg["ai_proveedor"] = proveedor or "hermes"
    if modelo:
        cfg["ai_modelo"] = modelo.strip()

    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[AIEngine] Aviso: No se pudo escribir en {CONFIG_FILE}: {e}")

    return cfg


# ---------------------------------------------------------------- Hermes Real CLI Runner
async def ejecutar_hermes(
    prompt: str,
    cwd: Path | None = None,
    yolo: bool = True,
    on_chunk: Callable[[str], None] | None = None,
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
        except TimeoutError:
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
    cwd: Path | None = None,
    on_event: Callable[[dict], None] | None = None,
    bus: Any | None = None,
) -> dict:
    """Debate dual entre agentes Hermes (100% sin API keys).

    1. Mi Hermes (CLI local) genera la propuesta técnica.
    2. Si hay un agente amigo vinculado: la propuesta viaja por el bus de
       agentes del hub y se espera su auditoría (timeout honesto).
    3. Sin amigo vinculado: mi propio Hermes audita la propuesta
       (modo individual, etiquetado como tal).

    `bus` es un adaptador del hub con: my_agent_name(), paired_agent_name(),
    post(de, para, texto, tipo, task_ref) y wait_reply(respuesta_a, timeout).
    """
    work_dir = cwd if (cwd and cwd.is_dir()) else REPO_ROOT
    hermes_bin = detectar_hermes_bin()
    if not hermes_bin:
        return {
            "ok": False,
            "error": "conecta tu Hermes",
            "detalle": "No se detectó el binario de Hermes en esta máquina. Conéctalo en el asistente de inicio.",
        }

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

    mi_agente = bus.my_agent_name() if bus else "Hermes"
    amigo = bus.paired_agent_name() if bus else None

    agy_extra = ""
    try:
        from antigravity_engine import get_antigravity_engine
        agy_extra = get_antigravity_engine(work_dir).consolidated_context_for_hermes()
    except Exception:
        pass

    # Fase 1: mi agente propone
    emitir("planificacion", mi_agente, f"Analizando objetivo: '{objetivo}' y generando propuesta...")
    propuesta_prompt = (
        f"OBJETIVO DE DESARROLLO:\n{objetivo}\n\n"
        f"{agy_extra}\n\n"
        "Eres el Arquitecto de Software del equipo. Genera:\n"
        "1. Diagnóstico breve y arquitectura propuesta.\n"
        "2. Pasos precisos de implementación.\n"
        "3. Archivos a crear o modificar (sin aplicar cambios todavía, solo la propuesta)."
    )

    def stream_propuesta(chunk: str):
        emitir("planificacion_stream", mi_agente, chunk)

    ret, propuesta = await ejecutar_agente(propuesta_prompt, cwd=work_dir, timeout=300, on_chunk=stream_propuesta)
    emitir("planificacion_completa", mi_agente, "Propuesta generada.", {"texto": propuesta})

    # Fase 2: auditoría — agente amigo vía bus, o agente secundario local / modo individual
    auditoria = ""
    auditoria_de_amigo = False
    modo = "individual"
    agente_auditor = mi_agente

    if amigo and bus:
        modo = "dual"
        agente_auditor = amigo
        emitir("revision", amigo, f"Solicitando auditoría a {amigo} por el bus de agentes...")
        solicitud = bus.post(
            de=mi_agente,
            para=amigo,
            texto=(
                f"Auditoría solicitada para el objetivo: '{objetivo}'\n\n"
                f"Propuesta de {mi_agente}:\n{propuesta[:4000]}\n\n"
                "Audita: ¿el plan cumple el objetivo? Riesgos, bugs potenciales y mejoras concretas."
            ),
            tipo="auditoria",
        )
        if asyncio.iscoroutine(solicitud):
            solicitud = await solicitud
        emitir("revision_espera", amigo, "Esperando auditoría del agente amigo (hasta 3 min)...")
        respuesta = await bus.wait_reply(solicitud["id"], timeout=180)
        if respuesta:
            auditoria = respuesta.get("texto", "")
            auditoria_de_amigo = True
            emitir("revision_completa", amigo, "Auditoría recibida del agente amigo.", {"informe": auditoria})
        else:
            emitir("revision_timeout", "Sistema",
                   f"{amigo} no respondió a tiempo. Intentando auditoría local de respaldo.",
                   {"informe": ""})

    # Si no hubo amigo remoto, buscar si hay un segundo agente configurado localmente (ej: Antigravity, Claude, Ollama)
    if not auditoria:
        audit_prompt = (
            f"Objetivo: '{objetivo}'\n\nPropuesta generada:\n{propuesta[:4000]}\n\n"
            "Actúa ahora como auditor senior: ¿el plan cumple el objetivo? "
            "Señala riesgos, bugs potenciales y mejoras concretas. Sé directo."
        )

        try:
            import agents as agent_registry
            slots = agent_registry.load_slots()
            segundo_nombre = None
            segundo_adapter = None
            for s in slots:
                if s["name"].lower() != mi_agente.lower():
                    ad = agent_registry.slot_adapter(s)
                    if ad and ad.detect():
                        segundo_nombre = s["name"]
                        segundo_adapter = ad
                        break
            if not segundo_adapter:
                for candidate in [agent_registry.AntigravityAdapter(), agent_registry.ClaudeCodeAdapter()]:
                    if candidate.detect() and candidate.label.lower() != mi_agente.lower():
                        segundo_nombre = candidate.label
                        segundo_adapter = candidate
                        break

            if segundo_adapter and segundo_nombre:
                agente_auditor = segundo_nombre
                modo = "dual"
                auditoria_de_amigo = True
                emitir("revision", segundo_nombre, f"Iniciando auditoría cruzada con {segundo_nombre}...")

                _ret_sec, sec_out = await segundo_adapter.arun(audit_prompt, cwd=work_dir, timeout=300)
                auditoria = sec_out
                emitir("revision_completa", segundo_nombre, f"Auditoría cruzada completada por {segundo_nombre}.", {"informe": auditoria})
        except Exception:
            pass

    if not auditoria:
        modo = "individual"
        agente_auditor = mi_agente
        emitir("revision", mi_agente, "Auditando la propuesta (modo individual reflexivo)...")
        _ret_a, auditoria = await ejecutar_agente(audit_prompt, cwd=work_dir, timeout=300)
        emitir("revision_completa", mi_agente, "Auditoría completada.", {"informe": auditoria})

    # Fase 3: Síntesis ejecutiva y consenso de acción entre ambos agentes
    emitir("sintesis", mi_agente, f"Sintetizando consenso final entre {mi_agente} y {agente_auditor}...")
    sintesis_prompt = (
        f"OBJETIVO: '{objetivo}'\n\n"
        f"PROPUESTA ({mi_agente}):\n{propuesta[:3000]}\n\n"
        f"AUDITORÍA ({agente_auditor}):\n{auditoria[:3000]}\n\n"
        "Eres el Coordinador del equipo de agentes. Consolida el consenso final de forma concisa:\n"
        "1. Puntos clave acordados y ajustes aceptados de la auditoría.\n"
        "2. Plan de acción definitivo paso a paso listo para ejecutar.\n"
        "3. Lista de archivos exactos a modificar o crear."
    )

    def stream_sintesis(chunk: str):
        emitir("sintesis_stream", mi_agente, chunk)

    _ret_s, sintesis = await ejecutar_agente(sintesis_prompt, cwd=work_dir, timeout=300, on_chunk=stream_sintesis)
    emitir("sintesis_completa", mi_agente, "Consenso y plan de acción final generado.", {"sintesis": sintesis})

    return {
        "ok": ret == 0,
        "modo": modo if auditoria_de_amigo else "individual",
        "objetivo": objetivo,
        "propuesta": propuesta,
        "auditoria": auditoria,
        "sintesis": sintesis,
        "consenso_alcanzado": True,
        "agente_propone": mi_agente,
        "agente_audita": agente_auditor,
        "agente_coordina": mi_agente,
    }


# ---------------------------------------------------------------- Utilidades y Estado
def extraer_codigo(texto: str) -> str | None:
    """Extrae el primer bloque de código relevante de una respuesta markdown."""
    match = re.search(r"```(?:\w+)?\n([\s\S]+?)\n```", texto)
    if match:
        return match.group(1)
    return None


async def verificar_estado_ia() -> dict[str, Any]:
    """Inspecciona el asistente IA local del usuario (sin claves, sin nube).

    Cualquier marca: Hermes, Claude Code, Ollama o CLI personalizado.
    """
    import agents as agent_registry

    hermes_bin = detectar_hermes_bin()
    cfg = obtener_config_ia()

    hermes_ok = hermes_bin is not None
    hermes_version = ""
    if hermes_ok:
        try:
            res = subprocess.run([str(hermes_bin), "--version"], capture_output=True, text=True, timeout=5)
            hermes_version = res.stdout.strip() or res.stderr.strip()
        except Exception:
            hermes_version = "Detectado"

    sel_id = agent_registry.get_selected_id()
    sel_adapter = agent_registry.get_adapter(sel_id) if sel_id else None
    sel_info = sel_adapter.to_info() if sel_adapter else None
    active_slot = agent_registry.get_active_slot_name()
    slot_info = next((s for s in agent_registry.list_slots_status()
                      if s["name"] == active_slot), None)

    return {
        # Bloque legacy (compatibilidad con el frontend actual).
        "hermes": {
            "instalado": hermes_ok,
            "ruta": str(hermes_bin) if hermes_bin else "",
            "version": hermes_version,
            "modelo": cfg.get("modelo", "auto"),
            "modo": "Hermes Agent (CLI local del usuario)",
        },
        # Bloque nuevo: el asistente ACTIVO (cualquier marca, multi-slot).
        "agente": {
            "id": sel_id,
            "slot": active_slot,
            "label": (slot_info or {}).get("name", "") or (sel_adapter.label if sel_adapter else ""),
            "adapter_label": (slot_info or {}).get("adapter_label", "") or (sel_adapter.label if sel_adapter else ""),
            "disponible": bool(slot_info and slot_info["detected"]),
            "detail": (slot_info or {}).get("detail", "") or (sel_info or {}).get("detail", ""),
            "modelo": cfg.get("modelo", "auto"),
            "slots": agent_registry.list_slots_status(),
        },
        "workspace": str(REPO_ROOT),
    }


async def ejecutar_agente(
    prompt: str,
    adapter_id: str = "",
    cwd: Path | None = None,
    timeout: int = 180,
    on_chunk: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Ejecuta el prompt con el asistente indicado (cualquier marca).

    adapter_id puede ser:
      - el NOMBRE de un slot ("Koko", "Mi Antigravity") → corre ese slot;
      - un id de adaptador ("hermes", "claude-code", "ollama", "custom");
      - "" → usa el slot ACTIVO.
    Sin asistente disponible: mensaje honesto, nunca inventa.
    """
    import agents as agent_registry

    sel = (adapter_id or "").strip()
    slot = agent_registry.get_slot(sel) if sel else None
    if slot:
        if on_chunk:
            on_chunk(f"⚡ [{slot['name'].upper()} INICIADO]\n")
        ret, out = await agent_registry.run_slot(sel, prompt, cwd=cwd, timeout=timeout)
        if on_chunk:
            on_chunk(f"\n✅ [{slot['name'].upper()} FINALIZADO] Código: {ret}\n")
        return (ret, out)

    adapter = agent_registry.get_adapter(sel) if sel else None
    if not adapter:
        active = agent_registry.get_active_slot_name()
        if active:
            return await ejecutar_agente(prompt, adapter_id=active, cwd=cwd,
                                         timeout=timeout, on_chunk=on_chunk)
        msg = (
            "No hay ningún asistente conectado en esta máquina. "
            "Conéctalo en el asistente de inicio (paso 1: Conectar mi Asistente)."
        )
        if on_chunk:
            on_chunk(msg + "\n")
        return (1, msg)
    info = adapter.to_info()
    if not info["detected"]:
        msg = f"El asistente '{adapter.label}' no está disponible en esta máquina ahora mismo."
        if on_chunk:
            on_chunk(msg + "\n")
        return (1, msg)
    if on_chunk:
        on_chunk(f"⚡ [{adapter.label.upper()} INICIADO]\n")
    ret, out = await adapter.arun(prompt, cwd=cwd, timeout=timeout)
    if on_chunk:
        on_chunk(f"\n✅ [{adapter.label.upper()} FINALIZADO] Código: {ret}\n")
    return (ret, out)


async def ejecutar_agente_stream(
    prompt: str,
    adapter_id: str = "",
    cwd: Path | None = None,
    timeout: int = 180,
):
    """Streaming REAL del asistente indicado. Yields ("chunk", texto) y
    ("done", (código, texto_completo)).

    adapter_id: nombre de slot, id de adaptador, o "" (slot activo).
    Sin asistente disponible: un solo chunk con el mensaje honesto.
    """
    import agents as agent_registry

    sel = (adapter_id or "").strip()
    slot = agent_registry.get_slot(sel) if sel else None
    if slot:
        async for kind, payload in agent_registry.run_slot_stream(
            sel, prompt, cwd=cwd, timeout=timeout
        ):
            yield kind, payload
        return

    adapter = agent_registry.get_adapter(sel) if sel else None
    if not adapter:
        active = agent_registry.get_active_slot_name()
        if active:
            async for kind, payload in ejecutar_agente_stream(
                prompt, adapter_id=active, cwd=cwd, timeout=timeout
            ):
                yield kind, payload
            return
        if sel:
            # Se pidió un slot/adaptador concreto que no existe: decirlo claro.
            msg = f"No existe el asistente '{sel}'."
        else:
            msg = (
                "No hay ningún asistente conectado en esta máquina. "
                "Conéctalo en el asistente de inicio (paso 1: Conectar mi Asistente)."
            )
        yield ("chunk", msg)
        yield ("done", (1, msg))
        return

    info = adapter.to_info()
    if not info["detected"]:
        msg = f"El asistente '{adapter.label}' no está disponible en esta máquina ahora mismo."
        yield ("chunk", msg)
        yield ("done", (1, msg))
        return
    async for kind, payload in adapter.arun_stream(prompt, cwd=cwd, timeout=timeout):
        yield kind, payload


# Usado por server.py para el chat con menciones (@hermes / @ia)
async def generar_respuesta_ia(
    prompt: str,
    contexto_archivo: str = "",
    nombre_archivo: str = "",
    agente_nombre: str = "Hermes",
) -> tuple[str, str | None]:
    """Responde usando el asistente local del usuario (cualquier marca, sin API keys).

    Si no hay asistente disponible, devuelve un mensaje honesto en vez de
    inventar una respuesta.
    """
    full = prompt
    if nombre_archivo and contexto_archivo:
        full += f"\n\n--- Archivo: {nombre_archivo} ---\n{contexto_archivo[:12000]}\n--- Fin del archivo ---"
    ret, out = await ejecutar_agente(full, cwd=REPO_ROOT, timeout=180)
    if ret != 0 or not out.strip():
        # ejecutar_agente ya trae el mensaje honesto cuando no hay asistente.
        return (out.strip() or "Mi asistente no pudo responder esta vez. Inténtalo de nuevo.", None)
    return (out.strip(), extraer_codigo(out))
