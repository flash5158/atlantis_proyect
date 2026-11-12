"""Aislamiento entre tests de la suite de Atlantis Studio.

Los módulos del servidor son singletons compartidos por toda la
suite: el contador de fallos de join (_JOIN_FALLOS) es por IP y
TestClient siempre viene de la misma IP, así que 5 joins fallidos
en un archivo envenenarían los joins de los demás archivos con
429. Se resetea entre tests (el test de rate-limit acumula sus 5
fallos DENTRO de un solo test, así que sigue siendo válido).
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Defaults de entorno ANTES de que cualquier módulo de test se importe:
# la config del servidor se congela en el primer import, así que estos
# valores deben existir ya. Los módulos de test los reescriben con sus
# propios TMP, pero PUBLIC_MODE y el relay por defecto deben ser los
# mismos para toda la suite (si un módulo sin NEXO_PUBLIC importara
# primero, PUBLIC_MODE quedaría False y los endpoints sin token darían 403).
# HOME es crítico: un módulo que NO fija HOME temporal (test_landing)
# importado primero dejaría la config apuntando al HOME REAL, y la
# base hub.db + el registro de equipos persistirían entre corridas
# (versiones de archivo que crecen de una corrida a otra).
_SUITE_TMP = Path(tempfile.mkdtemp(prefix="atlantis-suite-"))
# Asignación directa (no setdefault): el shell ya trae HOME y
# NEXO_WORKSPACE puestos, y setdefault no los tocaría.
os.environ["HOME"] = str(_SUITE_TMP / "home")
os.environ["NEXO_WORKSPACE"] = str(_SUITE_TMP / "ws")
os.environ["NEXO_PUBLIC"] = "1"
os.environ["ATLANTIS_RELAY_DEFAULT"] = "local"

sys.path.insert(0, str(Path(__file__).resolve().parent / "nexo"))


@pytest.fixture(autouse=True)
def _aislar_estado_compartido():
    """Resetea el estado compartido en memoria entre tests."""
    try:
        from backend.core.config import config
        from backend.routers import sync_teams as sync_teams_mod

        config.GUEST_TOKENS.clear()
        sync_teams_mod._JOIN_FALLOS.clear()
    except Exception:
        pass
    yield
    try:
        from backend.core.config import config
        from backend.routers import sync_teams as sync_teams_mod

        config.GUEST_TOKENS.clear()
        sync_teams_mod._JOIN_FALLOS.clear()
    except Exception:
        pass
