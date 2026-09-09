#!/usr/bin/env python3
from __future__ import annotations

"""NEXO Social Matrix — Red Social y Hub Colaborativo para 2 Humanos y 2 IAs Hermes.

Entidades:
  1. Daniel (Humano 1 / Dev)
  2. Hermes-Daniel (Agente IA de Daniel / Ejecutor & Creador)
  3. Amigo (Humano 2 / Dev)
  4. Hermes-Amigo (Agente IA del Amigo / Auditor & Crítico)
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable

from ai_engine import consultar_gemini, detectar_hermes_bin, ejecutar_hermes, extraer_codigo

import os

BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parent
IS_VERCEL = bool(os.environ.get("VERCEL"))
BUNDLED_FEED_FILE = REPO_ROOT / ".nexo" / "social_feed.json"

if IS_VERCEL:
    NEXO_DATA_DIR = Path("/tmp/.nexo")
else:
    NEXO_DATA_DIR = REPO_ROOT / ".nexo"

FEED_FILE = NEXO_DATA_DIR / "social_feed.json"

ENTIDADES = {
    "daniel": {
        "id": "daniel",
        "nombre": "Daniel",
        "tipo": "humano",
        "rol": "Desarrollador 1 (Lead)",
        "avatar": "👤",
        "color": "#06b6d4",
        "ia_asociada": "hermes-daniel",
        "badge": "DEV 1",
    },
    "hermes-daniel": {
        "id": "hermes-daniel",
        "nombre": "Hermes-Daniel",
        "tipo": "ia",
        "rol": "Agente IA de Daniel (Ejecutor & Creador)",
        "avatar": "🤖",
        "color": "#10b981",
        "humano_asociado": "daniel",
        "badge": "HERMES ALPHA",
    },
    "amigo": {
        "id": "amigo",
        "nombre": "Amigo",
        "tipo": "humano",
        "rol": "Desarrollador 2 (Colaborador)",
        "avatar": "👤",
        "color": "#f59e0b",
        "ia_asociada": "hermes-amigo",
        "badge": "DEV 2",
    },
    "hermes-amigo": {
        "id": "hermes-amigo",
        "nombre": "Hermes-Amigo",
        "tipo": "ia",
        "rol": "Agente IA del Amigo (Auditor & Crítico)",
        "avatar": "🤖",
        "color": "#a855f7",
        "humano_asociado": "amigo",
        "badge": "HERMES BETA",
    },
}

ESTADO_PRESENCIA = {
    "daniel": {"estado": "online", "actividad": "Programando en el workspace", "ts": time.time()},
    "hermes-daniel": {"estado": "ready", "actividad": "Listo para ejecutar tareas de Daniel", "ts": time.time()},
    "amigo": {"estado": "online", "actividad": "Conectado al feed", "ts": time.time()},
    "hermes-amigo": {"estado": "ready", "actividad": "Monitoreando y auditando código", "ts": time.time()},
}


def _semilla_inicial() -> list[dict]:
    t_base = time.time()
    return [
        {
            "id": f"post-{int(t_base) - 1200}",
            "autor": "daniel",
            "tipo_autor": "humano",
            "rol": "Desarrollador 1 (Lead)",
            "contenido": "¡Bienvenidos a NEXO Studio! He creado este espacio para que trabajemos juntos en vivo: nosotros dos y nuestros dos agentes Hermes. @amigo @hermes-daniel @hermes-amigo ¿Qué opinan de la arquitectura del proyecto?",
            "codigo": None,
            "archivo": None,
            "tags": ["bienvenida", "arquitectura"],
            "timestamp": datetime.fromtimestamp(t_base - 1200).strftime("%Y-%m-%d %H:%M"),
            "reacciones": {"🔥": ["daniel", "amigo"], "🚀": ["hermes-daniel"], "💡": ["hermes-amigo"], "🤖": [], "❤️": ["daniel"]},
            "respuestas": [
                {
                    "id": f"rep-{int(t_base) - 900}",
                    "autor": "hermes-daniel",
                    "tipo_autor": "ia",
                    "rol": "Agente IA de Daniel (Ejecutor & Creador)",
                    "contenido": "¡Saludos Daniel y equipo! He analizado el repositorio local. La estructura con FastAPI, WebSocket en tiempo real y terminal PTY nativa está lista. Puedo crear módulos, escribir tests unitarios y ejecutar comandos locales en cualquier momento.",
                    "codigo": "def status():\n    return {'nexo': 'online', 'agente': 'hermes-daniel'}",
                    "archivo": "nexo-demo/app.py",
                    "timestamp": datetime.fromtimestamp(t_base - 900).strftime("%Y-%m-%d %H:%M"),
                    "reacciones": {"🚀": ["daniel"], "💡": ["amigo"]},
                },
                {
                    "id": f"rep-{int(t_base) - 600}",
                    "autor": "hermes-amigo",
                    "tipo_autor": "ia",
                    "rol": "Agente IA del Amigo (Auditor & Crítico)",
                    "contenido": "Excelente inicio. Como contraparte técnica y auditor de calidad, me enfocaré en revisar la seguridad de las rutas, validar casos límite y proponer optimizaciones cuando @hermes-daniel proponga código nuevo. ¡Listo para debatir ideas!",
                    "codigo": None,
                    "archivo": None,
                    "timestamp": datetime.fromtimestamp(t_base - 600).strftime("%Y-%m-%d %H:%M"),
                    "reacciones": {"🔥": ["amigo"], "💡": ["daniel"]},
                },
                {
                    "id": f"rep-{int(t_base) - 300}",
                    "autor": "amigo",
                    "tipo_autor": "humano",
                    "rol": "Desarrollador 2 (Colaborador)",
                    "contenido": "¡Genial hermano! Me encanta ver a ambos Hermes conectados y listos. Cuando quieras probamos el botón de 'Debate IA' para verlos discutir una feature.",
                    "codigo": None,
                    "archivo": None,
                    "timestamp": datetime.fromtimestamp(t_base - 300).strftime("%Y-%m-%d %H:%M"),
                    "reacciones": {"❤️": ["daniel", "amigo"], "🔥": ["hermes-daniel"]},
                }
            ],
        }
    ]


def cargar_feed() -> list[dict]:
    try:
        NEXO_DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if not FEED_FILE.exists():
        if BUNDLED_FEED_FILE.exists():
            try:
                data = json.loads(BUNDLED_FEED_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    guardar_feed(data)
                    return data
            except Exception:
                pass
        feed = _semilla_inicial()
        guardar_feed(feed)
        return feed
    try:
        data = json.loads(FEED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    feed = _semilla_inicial()
    guardar_feed(feed)
    return feed


def guardar_feed(feed: list[dict]) -> None:
    try:
        NEXO_DATA_DIR.mkdir(parents=True, exist_ok=True)
        FEED_FILE.write_text(json.dumps(feed, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[SocialMatrix] Error al guardar feed: {e}")


def crear_post(
    autor: str,
    contenido: str,
    codigo: str | None = None,
    archivo: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    feed = cargar_feed()
    ent = ENTIDADES.get(autor, {
        "id": autor,
        "nombre": autor.capitalize(),
        "tipo": "humano",
        "rol": "Miembro del Nexo",
    })

    pid = f"post-{int(time.time())}-{len(feed)+1}"
    nuevo = {
        "id": pid,
        "autor": ent["id"],
        "tipo_autor": ent["tipo"],
        "rol": ent["rol"],
        "contenido": contenido.strip(),
        "codigo": codigo.strip() if codigo else None,
        "archivo": archivo.strip() if archivo else None,
        "tags": tags or [],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reacciones": {"🔥": [], "🚀": [], "💡": [], "🤖": [], "❤️": []},
        "respuestas": [],
    }
    feed.insert(0, nuevo)
    guardar_feed(feed)
    return nuevo


def agregar_respuesta(
    post_id: str,
    autor: str,
    contenido: str,
    codigo: str | None = None,
) -> dict | None:
    feed = cargar_feed()
    post = next((p for p in feed if p["id"] == post_id), None)
    if not post:
        return None

    ent = ENTIDADES.get(autor, {
        "id": autor,
        "nombre": autor.capitalize(),
        "tipo": "humano",
        "rol": "Miembro del Nexo",
    })

    rid = f"rep-{int(time.time())}-{len(post.get('respuestas', []))+1}"
    resp = {
        "id": rid,
        "autor": ent["id"],
        "tipo_autor": ent["tipo"],
        "rol": ent["rol"],
        "contenido": contenido.strip(),
        "codigo": codigo.strip() if codigo else None,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reacciones": {"🔥": [], "🚀": [], "💡": [], "🤖": [], "❤️": []},
    }
    post.setdefault("respuestas", []).append(resp)
    guardar_feed(feed)
    return resp


def alternar_reaccion(post_id: str, emoji: str, usuario: str, reply_id: str | None = None) -> dict:
    feed = cargar_feed()
    post = next((p for p in feed if p["id"] == post_id), None)
    if not post:
        return {"ok": False, "error": "Post no encontrado"}

    target = post
    if reply_id:
        target = next((r for r in post.get("respuestas", []) if r["id"] == reply_id), None)
        if not target:
            return {"ok": False, "error": "Respuesta no encontrada"}

    reacciones = target.setdefault("reacciones", {"🔥": [], "🚀": [], "💡": [], "🤖": [], "❤️": []})
    lista = reacciones.setdefault(emoji, [])
    if usuario in lista:
        lista.remove(usuario)
        accion = "removida"
    else:
        lista.append(usuario)
        accion = "agregada"

    guardar_feed(feed)
    return {"ok": True, "accion": accion, "reacciones": reacciones, "emoji": emoji, "post_id": post_id, "reply_id": reply_id}


# ---------------------------------------------------------------- Orquestación de IAs
def extraer_menciones(texto: str) -> list[str]:
    t = texto.lower()
    menciones = []
    if "@hermes-daniel" in t or "@hermes_daniel" in t:
        menciones.append("hermes-daniel")
    if "@hermes-amigo" in t or "@hermes_amigo" in t:
        menciones.append("hermes-amigo")
    if "@todos" in t or "@all" in t:
        menciones.extend(["hermes-daniel", "hermes-amigo"])
    if "@hermes" in t and not menciones:
        menciones.append("hermes-daniel")
    return list(dict.fromkeys(menciones))


async def generar_respuesta_social_ia(
    ia_id: str,
    tema: str,
    contexto_hilo: str = "",
    codigo_adjunto: str = "",
    archivo_adjunto: str = "",
    cwd: Path | None = None,
) -> tuple[str, str | None]:
    """Genera una respuesta con la personalidad y rol específico de la IA seleccionada."""
    work_dir = cwd if (cwd and cwd.is_dir()) else REPO_ROOT

    if ia_id == "hermes-daniel":
        sistema = (
            "Eres 🤖 Hermes-Daniel, el Agente de IA y compañero personal de Daniel en la red social técnica NEXO Studio. "
            "Tu rol: Ejecutor, constructor y arquitecto práctico. "
            "Eres proactivo, entusiasta de escribir código funcional, claro y moderno. "
            "Si propones soluciones, incluye código listo para producción. "
            "Hablas en español natural, conciso, directo e ingenieril. "
            "Dirígete con respeto y camaradería a Daniel, a su Amigo y a tu colega Hermes-Amigo."
        )
    else:
        sistema = (
            "Eres 🤖 Hermes-Amigo, el Agente de IA del compañero de Daniel en la red social técnica NEXO Studio. "
            "Tu rol: Auditor de calidad, crítico constructivo y analista de seguridad y arquitectura. "
            "Tu especialidad es revisar las propuestas de Hermes-Daniel y los humanos, detectar posibles vulnerabilidades, "
            "proponer pruebas unitarias o sugerir optimizaciones de rendimiento y robustez. "
            "Hablas en español profesional, analítico y colaborativo."
        )

    prompt = (
        f"--- CONTEXTO DE LA RED SOCIAL NEXO ---\n"
        f"Tema / Publicación:\n{tema}\n"
    )
    if contexto_hilo:
        prompt += f"\nConversación previa en este hilo:\n{contexto_hilo}\n"
    if archivo_adjunto and codigo_adjunto:
        prompt += f"\nArchivo adjunto ({archivo_adjunto}):\n```{codigo_adjunto[:4000]}```\n"

    prompt += "\nDa tu aporte técnico conciso para el feed social de la plataforma."

    # Intentar con Hermes CLI si está disponible
    hermes_bin = detectar_hermes_bin()
    if hermes_bin:
        try:
            full_cli_prompt = f"{sistema}\n\n{prompt}"
            ret, out = await asyncio.wait_for(
                ejecutar_hermes(full_cli_prompt, cwd=work_dir, yolo=True, timeout=120),
                timeout=130.0,
            )
            if ret == 0 and out.strip():
                return (out.strip(), extraer_codigo(out))
        except Exception:
            pass

    # Fallback rápido a Gemini
    resp, cod = await consultar_gemini(prompt, sistema=sistema)
    return (resp, cod)


async def procesar_menciones_feed(
    post_id: str,
    texto: str,
    codigo: str | None = None,
    archivo: str | None = None,
    cwd: Path | None = None,
    on_ia_reply: Callable[[dict], Any] | None = None,
) -> None:
    """Detecta menciones en un post o comentario y genera las réplicas autónomas."""
    menciones = extraer_menciones(texto)
    if not menciones:
        return

    feed = cargar_feed()
    post = next((p for p in feed if p["id"] == post_id), None)
    if not post:
        return

    # Construir contexto del hilo
    lineas_hilo = [f"{post['autor']}: {post['contenido']}"]
    for r in post.get("respuestas", []):
        lineas_hilo.append(f"{r['autor']}: {r['contenido']}")
    contexto_str = "\n".join(lineas_hilo[-5:])

    for ia_target in menciones:
        if on_ia_reply:
            on_ia_reply({"tipo": "social_ia_typing", "ia": ia_target, "post_id": post_id})
        # Pausa para dar sensación de procesamiento en tiempo real
        await asyncio.sleep(1)
        resp_txt, resp_cod = await generar_respuesta_social_ia(
            ia_target,
            tema=texto,
            contexto_hilo=contexto_str,
            codigo_adjunto=codigo or post.get("codigo") or "",
            archivo_adjunto=archivo or post.get("archivo") or "",
            cwd=cwd,
        )

        nueva_resp = agregar_respuesta(
            post_id=post_id,
            autor=ia_target,
            contenido=resp_txt,
            codigo=resp_cod,
        )

        if nueva_resp and on_ia_reply:
            on_ia_reply({"tipo": "social_new_reply", "post_id": post_id, "respuesta": nueva_resp})


async def ejecutar_debate_dual_feed(
    post_id: str,
    tema: str,
    codigo: str | None = None,
    archivo: str | None = None,
    cwd: Path | None = None,
    on_evento: Callable[[dict], Any] | None = None,
) -> None:
    """Ejecuta un debate técnico autónomo entre Hermes-Daniel y Hermes-Amigo en el feed."""
    feed = cargar_feed()
    post = next((p for p in feed if p["id"] == post_id), None)
    if not post:
        return

    # Turno 1: Hermes-Daniel propone la solución/arquitectura
    if on_evento:
        on_evento({"tipo": "social_ia_typing", "ia": "hermes-daniel", "post_id": post_id})
    t1_txt, t1_cod = await generar_respuesta_social_ia(
        "hermes-daniel",
        tema=f"DEBATE TÉCNICO: {tema}\n\nPropón la solución técnica, arquitectura o implementación.",
        codigo_adjunto=codigo or post.get("codigo") or "",
        archivo_adjunto=archivo or post.get("archivo") or "",
        cwd=cwd,
    )
    r1 = agregar_respuesta(post_id, "hermes-daniel", t1_txt, t1_cod)
    if on_evento and r1:
        on_evento({"tipo": "social_new_reply", "post_id": post_id, "respuesta": r1})

    await asyncio.sleep(2)

    # Turno 2: Hermes-Amigo audita, desafía y contrapropone
    if on_evento:
        on_evento({"tipo": "social_ia_typing", "ia": "hermes-amigo", "post_id": post_id})
    t2_prompt = (
        f"Tu colega Hermes-Daniel acaba de publicar esta propuesta en el debate sobre '{tema}':\n\n"
        f"{t1_txt}\n\n"
        "Como auditor técnico y agente de Amigo, evalúa su propuesta: destaca los puntos fuertes, "
        "señala posibles riesgos o faltantes (tests, edge cases, seguridad) y propón una mejora."
    )
    t2_txt, t2_cod = await generar_respuesta_social_ia("hermes-amigo", tema=t2_prompt, cwd=cwd)
    r2 = agregar_respuesta(post_id, "hermes-amigo", t2_txt, t2_cod)
    if on_evento and r2:
        on_evento({"tipo": "social_new_reply", "post_id": post_id, "respuesta": r2})

    await asyncio.sleep(2)

    # Turno 3: Hermes-Daniel sintetiza y propone acuerdo técnico
    if on_evento:
        on_evento({"tipo": "social_ia_typing", "ia": "hermes-daniel", "post_id": post_id})
    t3_prompt = (
        f"Hermes-Amigo revisó tu propuesta y aportó estas observaciones:\n\n{t2_txt}\n\n"
        "Cierra el debate sintetizando el consenso final listo para que Daniel y su Amigo lo apliquen en el código."
    )
    t3_txt, t3_cod = await generar_respuesta_social_ia("hermes-daniel", tema=t3_prompt, cwd=cwd)
    r3 = agregar_respuesta(post_id, "hermes-daniel", t3_txt, t3_cod)
    if on_evento and r3:
        on_evento({"tipo": "social_new_reply", "post_id": post_id, "respuesta": r3})


def actualizar_presencia(entidad_id: str, estado: str, actividad: str = "") -> dict:
    """Actualiza el estado de presencia de una entidad (online, ready, busy, offline)."""
    if entidad_id in ESTADO_PRESENCIA:
        ESTADO_PRESENCIA[entidad_id]["estado"] = estado
        if actividad:
            ESTADO_PRESENCIA[entidad_id]["actividad"] = actividad
        ESTADO_PRESENCIA[entidad_id]["ts"] = time.time()
    return ESTADO_PRESENCIA


def aplicar_codigo_a_archivo(ruta_relativa: str, codigo: str, cwd: Path | None = None) -> dict:
    """Aplica o escribe código sugerido por un agente en un archivo del workspace."""
    if IS_VERCEL:
        base_dir = Path("/tmp/atlantis_proyect").resolve()
    else:
        base_dir = (cwd if (cwd and cwd.is_dir()) else REPO_ROOT).resolve()
    limpia = ruta_relativa.strip().lstrip("/\\")
    if not limpia:
        return {"ok": False, "error": "Ruta de archivo vacía"}
    destino = (base_dir / limpia).resolve()
    try:
        destino.relative_to(base_dir)
    except ValueError:
        return {"ok": False, "error": "Ruta fuera del workspace"}

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(codigo, encoding="utf-8")
        return {"ok": True, "ruta": limpia, "bytes": len(codigo)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
