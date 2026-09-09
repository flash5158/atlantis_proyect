from __future__ import annotations

import os
import sys
from pathlib import Path

# Configurar rutas para Vercel Serverless
ROOT_DIR = Path(__file__).resolve().parent.parent
NEXO_DIR = ROOT_DIR / "nexo"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(NEXO_DIR) not in sys.path:
    sys.path.insert(0, str(NEXO_DIR))

# Indicar explícitamente modo Vercel Serverless
os.environ.setdefault("VERCEL", "1")

# Importar aplicación FastAPI principal
from nexo.server import app

# Exportar app para el runtime @vercel/python
app = app
