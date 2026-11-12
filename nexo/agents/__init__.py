"""Registro de adaptadores de asistentes IA.

Orden: Hermes primero (es el de Daniel), luego el resto detectado.
La selección del usuario se guarda por máquina en ~/.atlantis/config.json
(clave "ai_adapter"); los CLIs personalizados en "custom_agents".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import AgentAdapter
from .hermes import HermesAdapter
from .antigravity import AntigravityAdapter
from .claude_code import ClaudeCodeAdapter
from .ollama import OllamaAdapter
from .custom import GenericCLIAdapter
from .openai_compat import OpenAICompatAdapter, test_connection

__all__ = [
    "AgentAdapter",
    "HermesAdapter",
    "AntigravityAdapter",
    "ClaudeCodeAdapter",
    "OllamaAdapter",
    "GenericCLIAdapter",
    "OpenAICompatAdapter",
    "test_connection",
    "builtin_adapters",
    "load_custom_adapters",
    "detect_all",
    "get_adapter",
    "get_selected_id",
    "set_selected_id",
    "save_custom_agent",
    "remove_custom_agent",
    # Multi-assistant slots
    "load_slots",
    "save_slot",
    "remove_slot",
    "get_slot",
    "get_active_slot_name",
    "set_active_slot_name",
    "slot_adapter",
    "slot_status",
    "list_slots_status",
    "run_slot",
    "run_slot_stream",
]


def _config_path() -> Path:
    # Se resuelve en cada acceso (no en la importación) para que los tests
    # puedan aislar HOME por caso.
    return Path.home() / ".atlantis" / "config.json"


def _read_config() -> dict[str, Any]:
    try:
        p = _config_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_config(cfg: dict[str, Any]) -> None:
    try:
        p = _config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def builtin_adapters() -> list[AgentAdapter]:
    # Hermes primero: es el camino principal de Daniel.
    # Antigravity (AGY) nativo en la máquina.
    # openai_compat sin configurar: aparece como configurable, detect() honesto.
    return [HermesAdapter(), AntigravityAdapter(), ClaudeCodeAdapter(), OllamaAdapter(), OpenAICompatAdapter()]


def load_custom_adapters() -> list[GenericCLIAdapter]:
    cfg = _read_config()
    out: list[GenericCLIAdapter] = []
    for item in cfg.get("custom_agents", []) or []:
        if not isinstance(item, dict):
            continue
        cmd = str(item.get("command", "") or "").strip()
        if not cmd:
            continue
        out.append(
            GenericCLIAdapter(
                name=str(item.get("name", "") or "Mi CLI"),
                command=cmd,
                timeout=int(item.get("timeout", 120) or 120),
            )
        )
    return out


def all_adapters() -> list[AgentAdapter]:
    return builtin_adapters() + load_custom_adapters()


def detect_all() -> list[dict[str, Any]]:
    """Lista honesta: solo los realmente presentes marcan detected=true."""
    infos: list[dict[str, Any]] = []
    for a in builtin_adapters():
        infos.append(a.to_info())
    for a in load_custom_adapters():
        info = a.to_info()
        info["label"] = a.display_label
        infos.append(info)
    return infos


def get_adapter(adapter_id: str) -> AgentAdapter | None:
    aid = (adapter_id or "").strip()
    for a in builtin_adapters():
        if a.id == aid:
            return a
    if aid == "custom" or aid.startswith("custom:"):
        customs = load_custom_adapters()
        if customs:
            # Sin nombre específico: el primero configurado.
            return customs[0]
    return None


def get_selected_id() -> str:
    cfg = _read_config()
    sel = str(cfg.get("ai_adapter", "") or "").strip()
    if sel and get_adapter(sel):
        # Solo vale si sigue disponible/configurado.
        info_match = [i for i in detect_all() if i["id"] == sel and i["detected"]]
        if info_match:
            return sel
    # Por defecto: Hermes si está, si no el primer detectado, si no "".
    for i in detect_all():
        if i["id"] == "hermes" and i["detected"]:
            return "hermes"
    for i in detect_all():
        if i["detected"]:
            return i["id"]
    return ""


def set_selected_id(adapter_id: str) -> None:
    cfg = _read_config()
    cfg["ai_adapter"] = (adapter_id or "").strip()
    _write_config(cfg)


def save_custom_agent(name: str, command: str, timeout: int = 120) -> dict[str, Any]:
    cfg = _read_config()
    customs = [c for c in (cfg.get("custom_agents", []) or []) if isinstance(c, dict)]
    entry = {
        "name": (name or "").strip() or "Mi CLI",
        "command": (command or "").strip(),
        "timeout": int(timeout or 120),
    }
    # Reemplaza si ya existe uno con el mismo nombre.
    customs = [c for c in customs if str(c.get("name", "")) != entry["name"]]
    customs.append(entry)
    cfg["custom_agents"] = customs
    _write_config(cfg)
    return entry


def remove_custom_agent(name: str) -> bool:
    cfg = _read_config()
    customs = [c for c in (cfg.get("custom_agents", []) or []) if isinstance(c, dict)]
    kept = [c for c in customs if str(c.get("name", "")) != (name or "").strip()]
    if len(kept) == len(customs):
        return False
    cfg["custom_agents"] = kept
    if str(cfg.get("ai_adapter", "")) == "custom":
        cfg["ai_adapter"] = ""
    _write_config(cfg)
    return True


# ---------------------------------------------------------------- Multi-assistant slots
# Cada slot = un asistente conectado con identidad propia en el bus
# {"name": "Koko", "adapter": "hermes", "command": "", "model": "", "timeout": 120,
#  "base_url": "", "api_key": ""}  (base_url/api_key solo para openai_compat)
# Guardado por máquina en ~/.atlantis/config.json (clave "assistant_slots").


def _migrate_legacy_slots(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Migra la config anterior (ai_adapter + custom_agents) a slots."""
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str, adapter: str, command: str = "", model: str = ""):
        name = (name or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        slots.append({"name": name, "adapter": adapter, "command": command, "model": model, "timeout": 120})

    legacy_adapter = str(cfg.get("ai_adapter", "") or "").strip()
    if legacy_adapter and get_adapter(legacy_adapter):
        add("Mi Asistente", legacy_adapter)
    for item in cfg.get("custom_agents", []) or []:
        if isinstance(item, dict) and str(item.get("command", "") or "").strip():
            add(str(item.get("name", "") or "Mi CLI"), "custom", str(item.get("command", "")))
    return slots


def load_slots() -> list[dict[str, Any]]:
    cfg = _read_config()
    raw = cfg.get("assistant_slots")
    if not isinstance(raw, list):
        slots = _migrate_legacy_slots(cfg)
        if slots:
            cfg["assistant_slots"] = slots
            if not str(cfg.get("active_slot", "") or "").strip():
                cfg["active_slot"] = slots[0]["name"]
            _write_config(cfg)
        return slots
    # Sanea: nombres únicos, campos completos.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "name": name,
                "adapter": str(item.get("adapter", "") or "").strip() or "hermes",
                "command": str(item.get("command", "") or "").strip(),
                "model": str(item.get("model", "") or "").strip(),
                "timeout": int(item.get("timeout", 120) or 120),
                "base_url": str(item.get("base_url", "") or "").strip(),
                "api_key": str(item.get("api_key", "") or "").strip(),
            }
        )
    return out


def _persist_slots(slots: list[dict[str, Any]], active: str | None = None) -> None:
    cfg = _read_config()
    cfg["assistant_slots"] = slots
    if active is not None:
        cfg["active_slot"] = active
    elif str(cfg.get("active_slot", "") or "").strip() not in {s["name"] for s in slots}:
        cfg["active_slot"] = slots[0]["name"] if slots else ""
    _write_config(cfg)


def get_slot(name: str) -> dict[str, Any] | None:
    name = (name or "").strip()
    for s in load_slots():
        if s["name"] == name:
            return s
    return None


def save_slot(name: str, adapter: str, command: str = "", model: str = "",
             timeout: int = 120, base_url: str = "", api_key: str = "") -> dict[str, Any]:
    """Crea o actualiza un slot. Nombres únicos (identidad en el bus)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("El slot necesita un nombre.")
    adapter = (adapter or "").strip() or "hermes"
    if adapter == "openai_compat" and not (base_url or "").strip():
        raise ValueError("El endpoint OpenAI-compatible necesita una URL base.")
    slot = {
        "name": name,
        "adapter": adapter,
        "command": (command or "").strip(),
        "model": (model or "").strip(),
        "timeout": int(timeout or 120),
        "base_url": (base_url or "").strip(),
        "api_key": (api_key or "").strip(),
    }
    slots = [s for s in load_slots() if s["name"] != name]
    # Hermes primero en el orden solo si es el primero creado; si no, al final.
    slots.append(slot)
    _persist_slots(slots)
    return slot


def remove_slot(name: str) -> bool:
    name = (name or "").strip()
    slots = load_slots()
    kept = [s for s in slots if s["name"] != name]
    if len(kept) == len(slots):
        return False
    cfg = _read_config()
    active = str(cfg.get("active_slot", "") or "").strip()
    _persist_slots(kept, active if active != name else None)
    return True


def get_active_slot_name() -> str:
    cfg = _read_config()
    active = str(cfg.get("active_slot", "") or "").strip()
    slots = load_slots()
    if any(s["name"] == active for s in slots):
        return active
    return slots[0]["name"] if slots else ""


def set_active_slot_name(name: str) -> bool:
    name = (name or "").strip()
    if not get_slot(name):
        return False
    cfg = _read_config()
    cfg["active_slot"] = name
    _write_config(cfg)
    return True


def slot_adapter(slot: dict[str, Any]) -> AgentAdapter | None:
    """Construye el adaptador real para un slot (incluye CLI personalizado)."""
    adapter_id = slot.get("adapter", "hermes")
    if adapter_id == "custom":
        cmd = slot.get("command", "")
        if not cmd:
            return None
        return GenericCLIAdapter(name=slot["name"], command=cmd, timeout=slot.get("timeout", 120))
    if adapter_id == "openai_compat":
        return OpenAICompatAdapter(
            base_url=slot.get("base_url", ""),
            model=slot.get("model", ""),
            api_key=slot.get("api_key", ""),
            timeout=slot.get("timeout", 120),
        )
    a = get_adapter(adapter_id)
    if isinstance(a, OllamaAdapter) and slot.get("model"):
        return OllamaAdapter(model=slot["model"])
    return a


def slot_status(slot: dict[str, Any]) -> dict[str, Any]:
    a = slot_adapter(slot)
    info = a.to_info() if a else None
    if isinstance(a, GenericCLIAdapter):
        adapter_label = a.display_label
    elif a:
        adapter_label = a.label
    else:
        adapter_label = slot["adapter"]
    return {
        "name": slot["name"],
        "adapter": slot["adapter"],
        "adapter_label": adapter_label,
        "detected": bool(info and info["detected"]),
        "detail": (info or {}).get("detail", ""),
        "default_model": (info or {}).get("default_model", "") or slot.get("model", ""),
        "active": slot["name"] == get_active_slot_name(),
    }


def list_slots_status() -> list[dict[str, Any]]:
    return [slot_status(s) for s in load_slots()]


async def run_slot(name: str, prompt: str, cwd=None, timeout: int = 0) -> tuple[int, str]:
    """Ejecuta el prompt con el asistente del slot indicado (real, nunca simulado)."""
    slot = get_slot(name)
    if not slot:
        return (1, f"No existe el asistente '{name}'.")
    a = slot_adapter(slot)
    if not a:
        return (1, f"El asistente '{name}' no tiene comando configurado.")
    info = a.to_info()
    if not info["detected"]:
        return (1, f"El asistente '{name}' ({a.label}) no está disponible en esta máquina ahora mismo.")
    t = timeout or slot.get("timeout", 120) or 120
    return await a.arun(prompt, cwd=cwd, timeout=t)


async def run_slot_stream(name: str, prompt: str, cwd=None, timeout: int = 0):
    """Streaming REAL del slot: yield ("chunk", texto) y ("done", (código, texto)).

    Nunca simula: si el CLI no emite incrementalmente, llega un solo chunk.
    """
    slot = get_slot(name)
    if not slot:
        msg = f"No existe el asistente '{name}'."
        yield ("chunk", msg)
        yield ("done", (1, msg))
        return
    a = slot_adapter(slot)
    if not a:
        msg = f"El asistente '{name}' no tiene comando configurado."
        yield ("chunk", msg)
        yield ("done", (1, msg))
        return
    info = a.to_info()
    if not info["detected"]:
        msg = f"El asistente '{name}' ({a.label}) no está disponible en esta máquina ahora mismo."
        yield ("chunk", msg)
        yield ("done", (1, msg))
        return
    t = timeout or slot.get("timeout", 120) or 120
    async for kind, payload in a.arun_stream(prompt, cwd=cwd, timeout=t):
        yield kind, payload
