#!/usr/bin/env python3
"""NEXO 2.0 Real AI — Atlantis Real Developer & AI Hub.

SHIM FINO: la aplicación real vive en el paquete ``backend/``
(``backend.main.create_app()``). Este módulo solo la expone para que
el launcher de Electron (``desktop/native/main.js``) y los tests
sigan usando ``nexo/server.py`` sin cambios.

Estado del hub: ``AI_STORE`` es la fachada dict-compatible sobre SQLite
(ver ``backend/store.py``); la interfaz es idéntica a la de antes.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Garantizar que `backend` sea importable cuando server.py se ejecuta
# directamente (python nexo/server.py) o vía tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.core.config import config  # noqa: E402
from backend.store import get_store  # noqa: E402
from backend.main import create_app  # noqa: E402

app = create_app()

# Fachada dict-compatible sobre la BD (misma interfaz que el AI_STORE original).
AI_STORE = get_store()


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  NEXO 2.0 Real AI - Atlantis Real Developer & AI Hub")
    print("=" * 60)
    print(f"  URL:      http://localhost:{config.PORT}")
    print(f"  Token:    {config.TOKEN}")
    print(f"  Workspace:{config.WORKSPACE}")
    print(f"  BD:       {config.ATLANTIS_USER_DIR / 'hub.db'}")
    if config.PUBLIC_MODE:
        print("  Modo:     PÚBLICO (sin token)")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="warning")
