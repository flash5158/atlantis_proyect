"""Factoría de la aplicación FastAPI (NEXO 2.0).

``create_app()`` levanta el hub completo:
  1. Configuración del runtime (rutas, entorno, token).
  2. Base de datos SQLite (WAL) + migración idempotente del JSON legado.
  3. HubStore (fachada dict-compatible sobre la BD).
  4. Registro de routers por dominio.

Se ejecuta en la importación (no en lifespan) porque los tests usan
``TestClient(app)`` sin gestor de contexto.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import db
from backend.core.config import config
from backend.store import init_store


def _semilla_agentes() -> dict:
    """Semilla del overlay 'agentes' (igual que la de ai_colab.json)."""
    ahora = time.time()
    return {
        config.CFG.get("ia_nombre", "antigravity"): {
            "tipo": "antigravity",
            "estado": "listo",
            "actividad": "Co-pilot en línea",
            "ultimo_ping": ahora,
        },
        config.CFG.get("ia_companero", "hermes"): {
            "tipo": "hermes",
            "estado": "listo",
            "actividad": "Enlace activo",
            "ultimo_ping": ahora,
        },
    }


def create_app() -> FastAPI:
    # 1. Runtime: lee el entorno AHORA (los tests fijan HOME/PORT antes del import).
    config.init_runtime()

    # 2. BD + migración del JSON legado (idempotente).
    db.init_db()

    # 3. Fachada de estado del hub.
    init_store(agentes_seed=_semilla_agentes())

    # 4. App y routers.
    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # Sync entre servidores: reanuda las sesiones de proyecto
        # guardadas (una por carpeta compartida). No-op si no hay
        # ninguna: los proyectos en solitario nunca sincronizan.
        try:
            from backend import sync as sync_mod
            sync_mod.iniciar_sesiones_persistidas()
        except Exception:
            pass
        try:
            from backend.routers.agents import iniciar_hermes_worker
            iniciar_hermes_worker()
        except Exception:
            pass
        yield

    app = FastAPI(
        title="NEXO 2.0 Real AI",
        description="Atlantis Real Developer & AI Hub",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if not config.NO_WEB_UI:
        app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

    from backend.routers import agents, auth, files, git, landing, mcp, preview, social, status, sync_teams, terminal, tunnel

    for router_mod in (status, files, terminal, git, agents, tunnel, auth, mcp, social, landing, sync_teams, preview):
        app.include_router(router_mod.router)

    if config.NO_WEB_UI:
        # Sin interfaz web del IDE: los assets del IDE no existen en este modo.
        @app.get("/static/{_path:path}")
        async def _static_bloqueado():
            return JSONResponse({"ok": False, "error": "Interfaz web desactivada."}, status_code=404)

    # Errores con forma consistente; nunca se filtran tracebacks al cliente.
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        return JSONResponse(
            {"ok": False, "error": "Error interno del servidor."},
            status_code=500,
        )

    return app
