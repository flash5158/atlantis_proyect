"""Backend real de Atlantis Studio (NEXO 2.0).

Paquete profesional del hub colaborativo. La app se construye con
``backend.main.create_app()``; ``nexo/server.py`` es solo un shim fino
que lo expone (así el launcher de Electron y los tests siguen igual).

Estructura:
    backend/main.py      -> factoría de la app FastAPI
    backend/db.py        -> SQLAlchemy 2.0 + SQLite (WAL)
    backend/models.py    -> tablas
    backend/store.py     -> HubStore: fachada dict-compatible sobre la BD
                             (preserva la interfaz AI_STORE que usan el
                             código y los tests, persistiendo en SQLite)
    backend/core/        -> config, seguridad, estado en memoria, deps
    backend/routers/     -> endpoints por dominio
"""

from __future__ import annotations

import sys
from pathlib import Path

# Los motores legados (ai_engine, antigravity_engine, social_matrix,
# mcp_manager, agents) viven en nexo/ como módulos de primer nivel.
# Garantizar que nexo/ esté en sys.path para que los routers puedan
# importarlos igual que hacía server.py.
_NEXO_DIR = Path(__file__).resolve().parent.parent
if str(_NEXO_DIR) not in sys.path:
    sys.path.insert(0, str(_NEXO_DIR))

__all__: list[str] = []
