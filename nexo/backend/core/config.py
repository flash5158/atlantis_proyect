"""Configuración y rutas del hub.

Toda la configuración derivada del entorno vive aquí. El objeto mutable
``config`` (instancia de RuntimeConfig) reemplaza a los globales de módulo
que antes vivían en server.py; ``set_active_workspace()`` lo muta en
caliente igual que antes (endpoint /api/workspace/select).
"""

from __future__ import annotations

import hmac
import json
import os
import platform
import secrets
from pathlib import Path
from typing import Any


class RuntimeConfig:
    """Estado de configuración mutable del proceso (un solo hub por proceso)."""

    def __init__(self) -> None:
        self.HOME = Path.home()
        # backend/core/config.py -> backend/ -> nexo/ -> repo
        self.BACKEND_DIR = Path(__file__).resolve().parent.parent
        self.NEXO_DIR = self.BACKEND_DIR.parent
        self.REPO_ROOT = self.NEXO_DIR.parent

        self.IS_VERCEL = bool(os.environ.get("VERCEL"))
        # En despliegues públicos el hub funciona sin token de propietario.
        # En local se mantiene la barrera Bearer por defecto.
        self.PUBLIC_MODE = self.IS_VERCEL or bool(os.environ.get("NEXO_PUBLIC"))
        # NEXO_NO_WEB_UI=1: el hub no sirve la interfaz web del IDE
        # (GET / muestra la página de descarga; /static/* da 404).
        # /api/* y /ws siguen funcionando: el hub sigue siendo relay.
        self.NO_WEB_UI = os.environ.get("NEXO_NO_WEB_UI", "").strip().lower() in ("1", "true", "yes")

        self.WORKSPACE: Path = self.REPO_ROOT
        self.PROYECTOS: Path = self.REPO_ROOT / "proyectos"
        self.NEXO_DATA_DIR: Path = self.REPO_ROOT / ".nexo"
        self.ATLANTIS_USER_DIR = self.HOME / ".atlantis"
        self.ACCOUNTS_FILE = self.ATLANTIS_USER_DIR / "accounts.json"
        self.STATIC_DIR = self.NEXO_DIR / "static"
        self.CURRENT_TERMINAL_CWD: Path = self.REPO_ROOT

        self.PORT = int(os.environ.get("PORT", "8787"))
        self.TOKEN = ""
        self.CFG: dict[str, Any] = {}
        # Tokens de invitado de equipo: {guest_token: {pairing_code, user_name, agent_name}}
        # Solo en memoria: al reiniciar el hub, los equipos se reconstituyen con join.
        self.GUEST_TOKENS: dict[str, dict[str, str]] = {}

        self.MAX_LECTURA = 10 * 1024 * 1024     # 10 MB
        self.MAX_SUBIDA = 50 * 1024 * 1024      # 50 MB
        self.MAX_CHAT = 12000                   # caracteres por mensaje

        self.IS_DARWIN = platform.system() == "Darwin"
        self.IS_LINUX = platform.system() == "Linux"

        self._inicializado = False

    # -- bootstrap ------------------------------------------------------
    def init_runtime(self) -> None:
        """Lee el entorno y prepara rutas. Idempotente; se llama en create_app()."""
        # Releer por si el entorno cambió entre import y create_app (tests).
        self.HOME = Path.home()
        self.ATLANTIS_USER_DIR = self.HOME / ".atlantis"
        self.ACCOUNTS_FILE = self.ATLANTIS_USER_DIR / "accounts.json"
        self.IS_VERCEL = bool(os.environ.get("VERCEL"))
        self.PUBLIC_MODE = self.IS_VERCEL or bool(os.environ.get("NEXO_PUBLIC"))
        self.NO_WEB_UI = os.environ.get("NEXO_NO_WEB_UI", "").strip().lower() in ("1", "true", "yes")
        self.PORT = int(os.environ.get("PORT", "8787"))

        if self.IS_VERCEL:
            self.WORKSPACE = Path("/tmp/atlantis_proyect").resolve()
            self.PROYECTOS = self.WORKSPACE / "proyectos"
            self.NEXO_DATA_DIR = Path("/tmp/.nexo")
        else:
            env_ws = os.environ.get("NEXO_WORKSPACE")
            if env_ws and Path(env_ws).exists():
                self.WORKSPACE = Path(env_ws).resolve()
            elif (self.REPO_ROOT / "proyectos").exists():
                self.WORKSPACE = self.REPO_ROOT.resolve()
            elif (self.NEXO_DIR / "colab-hub").exists():
                self.WORKSPACE = (self.NEXO_DIR / "colab-hub").resolve()
            elif (self.HOME / "colab-hub").exists():
                self.WORKSPACE = (self.HOME / "colab-hub").resolve()
            else:
                self.WORKSPACE = self.REPO_ROOT.resolve()

            self.PROYECTOS = self.WORKSPACE / "proyectos"
            self.NEXO_DATA_DIR = self.WORKSPACE / ".nexo"

            self.ATLANTIS_USER_DIR.mkdir(parents=True, exist_ok=True)

        self.CURRENT_TERMINAL_CWD = self.WORKSPACE

        # La shell web vive en la raíz del repo; fallback a los assets
        # legados de nexo/ para instalaciones antiguas.
        web_index = self.REPO_ROOT / "web" / "index.html"
        if web_index.is_file():
            self.STATIC_DIR = self.REPO_ROOT / "web"
        else:
            self.STATIC_DIR = self.NEXO_DIR / "static"
        if not self.STATIC_DIR.is_dir() and (self.REPO_ROOT / "nexo" / "static").is_dir():
            self.STATIC_DIR = self.REPO_ROOT / "nexo" / "static"

        self.CFG = self.cargar_config()
        self.TOKEN = os.environ.get("TOKEN") or self.CFG["token"]

        try:
            self.PROYECTOS.mkdir(parents=True, exist_ok=True)
            self.NEXO_DATA_DIR.mkdir(parents=True, exist_ok=True)
            if not (self.PROYECTOS / "README.md").exists():
                (self.PROYECTOS / "README.md").write_text(
                    "# Proyectos de NEXO\n\nDirectorio de trabajo colaborativo para código compartido.\n",
                    encoding="utf-8",
                )
        except Exception:
            pass

        # MCP Builder: los MCPs creados viven en <workspace>/mcps.
        try:
            import mcp_manager
            mcp_manager.set_base_dir(self.WORKSPACE)
        except Exception:
            pass

        self._inicializado = True

    # -- config de usuario (~/.atlantis/config.json) ---------------------
    def cargar_config(self) -> dict:
        cfg: dict[str, Any] = {}
        config_path = self.ATLANTIS_USER_DIR / "config.json"
        base_config = self.NEXO_DIR / "config.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        elif base_config.exists():
            try:
                cfg = json.loads(base_config.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}

        cfg.setdefault("token", secrets.token_hex(16))
        cfg.setdefault("nombre", "jaime")
        cfg.setdefault("companero", "amigo")
        cfg.setdefault("ia_nombre", "antigravity")
        cfg.setdefault("ia_companero", "hermes")
        cfg.setdefault("tema", "jarvis")
        cfg.setdefault("ai_proveedor", "hermes")
        cfg.setdefault("ai_modelo", "auto")
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Server] Aviso guardando config: {e}")
        return cfg

    # -- workspace en caliente ------------------------------------------
    def set_active_workspace(self, new_path: Path) -> None:
        """Cambia el workspace en caliente (endpoint /api/workspace/select)."""
        new_path = Path(new_path).resolve()
        new_path.mkdir(parents=True, exist_ok=True)
        self.WORKSPACE = new_path
        self.PROYECTOS = new_path / "proyectos"
        self.NEXO_DATA_DIR = new_path / ".nexo"
        self.NEXO_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.CURRENT_TERMINAL_CWD = new_path
        os.environ["NEXO_WORKSPACE"] = str(new_path)
        try:
            import mcp_manager
            mcp_manager.set_base_dir(new_path)
        except Exception:
            pass

    # -- auth helpers ----------------------------------------------------
    # Cabeceras que delatan que la petición viene por un proxy/túnel
    # (cloudflared, ngrok, ...): el cliente real está en internet aunque el
    # socket TCP lo abra el demonio local en 127.0.0.1.
    PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "cf-ray", "x-real-ip")

    def token_ok(self, token: str) -> bool:
        """Cualquier credencial válida: secreto del dueño o token de invitado."""
        if self.PUBLIC_MODE:
            return True
        if hmac.compare_digest(token or "", self.TOKEN):
            return True
        return self.token_es_invitado(token or "")

    def es_local(self, req_or_ws) -> bool:
        headers = getattr(req_or_ws, "headers", None) or {}
        try:
            claves = {str(k).lower() for k in headers}
            if any(h in claves for h in self.PROXY_HEADERS):
                return False
        except Exception:
            pass
        client = getattr(req_or_ws, "client", None)
        if not client:
            return True
        host = client.host
        return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "testclient")

    def es_host(self, req_or_ws, token: str) -> bool:
        """True solo para el dueño de la máquina (su secreto).

        En modo público no hay dueño: todo el mundo pasa (comportamiento
        histórico del despliegue demo). El argumento req_or_ws se mantiene
        por simetría con es_local; la decisión es el secreto, no el origen.
        """
        if self.PUBLIC_MODE:
            return True
        return hmac.compare_digest(token or "", self.TOKEN)

    # -- tokens de invitado (equipo) -----------------------------------
    def registrar_invitado(self, guest_token: str, pairing_code: str,
                           user_name: str, agent_name: str) -> None:
        self.GUEST_TOKENS[guest_token] = {
            "pairing_code": pairing_code,
            "user_name": user_name,
            "agent_name": agent_name,
        }

    def token_es_invitado(self, token: str) -> bool:
        return bool(token) and token in self.GUEST_TOKENS

    def revocar_invitado(self, token: str) -> None:
        self.GUEST_TOKENS.pop(token or "", None)

    def revocar_invitados(self, pairing_code: str) -> None:
        for t in [t for t, info in self.GUEST_TOKENS.items()
                  if info.get("pairing_code") == pairing_code]:
            del self.GUEST_TOKENS[t]


config = RuntimeConfig()


# Atajos de módulo (misma firma que los helpers de server.py)
def token_ok(token: str) -> bool:
    return config.token_ok(token)


def es_local(req_or_ws) -> bool:
    return config.es_local(req_or_ws)


def es_host(req_or_ws, token: str) -> bool:
    return config.es_host(req_or_ws, token)


def token_es_invitado(token: str) -> bool:
    return config.token_es_invitado(token)


def registrar_invitado(guest_token: str, pairing_code: str,
                       user_name: str, agent_name: str) -> None:
    config.registrar_invitado(guest_token, pairing_code, user_name, agent_name)


def revocar_invitado(token: str) -> None:
    config.revocar_invitado(token)


def revocar_invitados(pairing_code: str) -> None:
    config.revocar_invitados(pairing_code)
