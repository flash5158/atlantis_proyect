"""Tests de adaptadores universales de IA y del MCP Builder (sin API keys).

Cubre: registro/detección de adaptadores (con binarios falsos en PATH),
GenericCLIAdapter real (echo/stdin), honestidad sin binarios,
scaffolding MCP válido y round-trip list_tools/call_tool contra un
servidor MCP real generado. No tocan la instalación real del usuario.
"""
from __future__ import annotations

import asyncio
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

TMP_HOME = Path(tempfile.mkdtemp(prefix="atlantis-test-home-"))
TMP_WS = Path(tempfile.mkdtemp(prefix="atlantis-test-ws-"))
(TMP_WS / "proyectos").mkdir(exist_ok=True)

os.environ["HOME"] = str(TMP_HOME)
os.environ["NEXO_WORKSPACE"] = str(TMP_WS)
os.environ["NEXO_PUBLIC"] = "1"
os.environ["PORT"] = "18798"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

import agents  # noqa: E402
from agents.custom import GenericCLIAdapter  # noqa: E402
from agents.hermes import HermesAdapter  # noqa: E402

mcp_manager = pytest.importorskip("mcp_manager", reason="mcp_manager importable siempre")


# ---------------------------------------------------------------- helpers
def _fake_bin(tmp_path: Path, name: str, script: str) -> Path:
    p = tmp_path / name
    p.write_text(script, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


# ---------------------------------------------------------------- adaptadores
def test_registry_lista_tres_builtin():
    infos = agents.detect_all()
    ids = [i["id"] for i in infos]
    assert "hermes" in ids and "claude-code" in ids and "ollama" in ids
    # Hermes primero: es el camino principal.
    assert ids[0] == "hermes"


def test_hermes_honesto_sin_binario(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # PATH vacío de binarios reales
    monkeypatch.setenv("HOME", str(tmp_path))
    assert HermesAdapter().detect() is None


def test_claude_detect_con_binario_falso(tmp_path, monkeypatch):
    _fake_bin(tmp_path, "claude", "#!/bin/sh\necho hola\n")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    from agents.claude_code import ClaudeCodeAdapter

    det = ClaudeCodeAdapter().detect()
    assert det is not None and "claude" in det["detail"]


def test_ollama_sin_modelos_no_detecta(tmp_path, monkeypatch):
    _fake_bin(tmp_path, "ollama", "#!/bin/sh\nif [ \"$1\" = \"list\" ]; then echo 'NAME'; fi\n")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    from agents.ollama import OllamaAdapter

    assert OllamaAdapter().detect() is None  # honesto: sin modelos no vale


def test_generic_cli_modo_stdin():
    adapter = GenericCLIAdapter(name="Eco", command="cat")
    ret, out = asyncio.run(adapter.arun("hola-mcp-test", timeout=15))
    assert ret == 0 and "hola-mcp-test" in out


def test_generic_cli_modo_plantilla():
    adapter = GenericCLIAdapter(name="Eco2", command="echo 'RESP:{prompt}'")
    ret, out = asyncio.run(adapter.arun("hola", timeout=15))
    assert ret == 0 and "RESP:hola" in out


def test_generic_cli_sin_comando_honesto():
    adapter = GenericCLIAdapter(name="Vacio", command="")
    assert adapter.detect() is None
    ret, out = asyncio.run(adapter.arun("x", timeout=10))
    assert ret != 0 and "configurado" in out


def test_custom_save_y_detect(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    agents.save_custom_agent("MiEco", "cat")
    customs = agents.load_custom_adapters()
    assert any(c.custom_name == "MiEco" for c in customs)
    infos = agents.detect_all()
    assert any(i["id"] == "custom" and i["detected"] for i in infos)
    assert agents.remove_custom_agent("MiEco") is True
    assert agents.remove_custom_agent("MiEco") is False


# ---------------------------------------------------------------- MCP Builder
def test_mcp_scaffold_valido(tmp_path):
    mcp_manager.set_base_dir(tmp_path)
    mf = mcp_manager.create_mcp("prueba1", "desc", [{
        "name": "saludar",
        "description": "Saluda",
        "params_schema": {"nombre": {"type": "string", "description": "quién"}},
        "handler": 'return f"Hola, {nombre}!"',
    }])
    srv = tmp_path / "mcps" / "prueba1" / "server.py"
    assert srv.exists()
    compile(srv.read_text(encoding="utf-8"), "server.py", "exec")
    assert mf["name"] == "prueba1" and mf["tools"][0]["name"] == "saludar"


def test_mcp_rechaza_nombre_malo(tmp_path):
    mcp_manager.set_base_dir(tmp_path)
    with pytest.raises(ValueError):
        mcp_manager.create_mcp("mal nombre!", "x", [{
            "name": "t", "description": "d",
            "params_schema": {}, "handler": "return 'ok'",
        }])


def test_mcp_rechaza_codigo_roto(tmp_path):
    mcp_manager.set_base_dir(tmp_path)
    with pytest.raises(ValueError, match="sintaxis"):
        mcp_manager.create_mcp("roto", "x", [{
            "name": "t", "description": "d",
            "params_schema": {}, "handler": "def ((",
        }])


def test_mcp_roundtrip_real(tmp_path):
    pytest.importorskip("mcp", reason="paquete mcp requerido")
    if not mcp_manager.MCP_AVAILABLE:
        pytest.skip("mcp no disponible")
    mcp_manager.set_base_dir(tmp_path)
    mcp_manager.create_mcp("vivo", "servidor vivo", [{
        "name": "sumar",
        "description": "Suma dos números",
        "params_schema": {
            "a": {"type": "integer", "description": "primero"},
            "b": {"type": "integer", "description": "segundo"},
        },
        "handler": "return str(a + b)",
    }])

    async def go():
        tools = await mcp_manager.list_tools_live("vivo")
        assert any(t["name"] == "sumar" for t in tools)
        res = await mcp_manager.call_tool_live("vivo", "sumar", {"a": 20, "b": 22})
        assert not res["isError"] and res["text"].strip() == "42"

    asyncio.run(go())


def test_mcp_start_stop(tmp_path):
    pytest.importorskip("mcp", reason="paquete mcp requerido")
    if not mcp_manager.MCP_AVAILABLE:
        pytest.skip("mcp no disponible")
    mcp_manager.set_base_dir(tmp_path)
    mcp_manager.create_mcp("proc", "x", [{
        "name": "t", "description": "d", "params_schema": {},
        "handler": "return 'ok'",
    }])
    r = mcp_manager.start_mcp("proc")
    assert r["status"] == "corriendo"
    assert mcp_manager.status_of("proc") == "corriendo"
    r = mcp_manager.stop_mcp("proc")
    assert r["status"] == "detenido"


# ---------------------------------------------------------------- endpoints API
def _client():
    from fastapi.testclient import TestClient
    import server

    return TestClient(server.app)


def test_api_agents_available():
    c = _client()
    r = c.get("/api/agents/available")
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["adapters"]]
    assert "hermes" in ids and "claude-code" in ids and "ollama" in ids


def test_api_custom_test_echo():
    c = _client()
    r = c.post("/api/agents/custom/test", json={"command": "cat"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and "ENLACE_OK" in j["salida"]


def test_api_mcps_crud(tmp_path):
    pytest.importorskip("mcp", reason="paquete mcp requerido")
    c = _client()
    r = c.get("/api/mcps")
    assert r.status_code == 200 and r.json()["mcp_available"] is True
    r = c.post("/api/mcps", json={
        "name": "apitest", "description": "vía API",
        "tools": [{"name": "eco", "description": "eco",
                   "params_schema": {"t": {"type": "string"}},
                   "handler": "return t"}],
    })
    assert r.status_code == 200, r.text
    r = c.post("/api/mcps/apitest/tools")
    assert r.status_code == 200 and any(t["name"] == "eco" for t in r.json()["tools"])
    r = c.post("/api/mcps/apitest/call", json={"tool": "eco", "args": {"t": "hola-api"}})
    assert r.status_code == 200 and "hola-api" in r.json()["text"]
    r = c.delete("/api/mcps/apitest")
    assert r.status_code == 200 and r.json()["ok"]


# ---------------------------------------------------------------- multi-assistant slots
def _reset_slots(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".atlantis" / "config.json"
    if cfg.exists():
        cfg.unlink()
    return cfg


def test_slots_crud_y_nombres_unicos(tmp_path, monkeypatch):
    _reset_slots(monkeypatch, tmp_path)
    agents.save_slot("Koko", "hermes")
    agents.save_slot("Mi Antigravity", "custom", command="cat")
    slots = agents.load_slots()
    assert [s["name"] for s in slots] == ["Koko", "Mi Antigravity"]
    # El primero creado queda activo por defecto.
    assert agents.get_active_slot_name() == "Koko"
    # Nombres únicos: guardar de nuevo actualiza, no duplica.
    agents.save_slot("Koko", "custom", command="cat")
    assert len(agents.load_slots()) == 2
    assert agents.get_slot("Koko")["adapter"] == "custom"


def test_slots_cambio_de_activo_y_eliminar(tmp_path, monkeypatch):
    _reset_slots(monkeypatch, tmp_path)
    agents.save_slot("Koko", "hermes")
    agents.save_slot("Eco 2", "custom", command="cat")
    assert agents.set_active_slot_name("Eco 2") is True
    assert agents.get_active_slot_name() == "Eco 2"
    assert agents.set_active_slot_name("No Existe") is False
    # Eliminar el activo reasigna el activo a otro slot.
    assert agents.remove_slot("Eco 2") is True
    assert agents.get_active_slot_name() == "Koko"
    assert agents.remove_slot("Fantasma") is False


def test_slots_estado_honesto(tmp_path, monkeypatch):
    _reset_slots(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))  # sin binarios reales
    agents.save_slot("Koko", "hermes")
    agents.save_slot("Eco", "custom", command="cat")
    statuses = {s["name"]: s for s in agents.list_slots_status()}
    assert statuses["Koko"]["detected"] is False  # hermes no existe aquí
    assert statuses["Eco"]["detected"] is True   # `cat` sí existe
    assert statuses["Koko"]["active"] is True


def test_enrutamiento_sigue_al_slot_activo(tmp_path, monkeypatch):
    _reset_slots(monkeypatch, tmp_path)
    # Dos adaptadores CLI distintos con identidades distintas.
    agents.save_slot("Eco A", "custom", command="cat")
    tag = tmp_path / "tag.sh"
    tag.write_text('#!/bin/sh\nprintf "[B] "; cat\n', encoding="utf-8")
    tag.chmod(tag.stat().st_mode | stat.S_IEXEC)
    agents.save_slot("Eco B", "custom", command=str(tag))

    ret, out = asyncio.run(agents.run_slot("Eco A", "hola"))
    assert ret == 0 and out.strip() == "hola"

    ret, out = asyncio.run(agents.run_slot("Eco B", "hola"))
    assert ret == 0 and out.strip() == "[B] hola"

    # ejecutar_agente sin adapter_id usa el slot activo.
    import ai_engine

    ret, out = asyncio.run(ai_engine.ejecutar_agente("ping"))
    assert ret == 0 and out.strip() == "ping"  # activo = Eco A (primero)

    agents.set_active_slot_name("Eco B")
    ret, out = asyncio.run(ai_engine.ejecutar_agente("ping"))
    assert ret == 0 and out.strip() == "[B] ping"  # ahora sigue al activo

    # Por nombre de slot explícito, aunque no sea el activo.
    ret, out = asyncio.run(ai_engine.ejecutar_agente("ping", adapter_id="Eco A"))
    assert ret == 0 and out.strip() == "ping"


def test_bus_identidades_unicas_por_slot(tmp_path, monkeypatch):
    _reset_slots(monkeypatch, tmp_path)
    # Cada slot es una identidad propia: no puede haber dos con el mismo nombre.
    agents.save_slot("Koko", "hermes")
    agents.save_slot("Koko", "claude-code")  # upsert, no duplicado
    names = [s["name"] for s in agents.load_slots()]
    assert names == ["Koko"]


def test_api_hermes_health_and_ping():
    c = _client()
    r = c.get("/api/hermes/health")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert "installed" in j
    assert "agent_name" in j
    assert "worker" in j

    r_ping = c.post("/api/hermes/ping")
    assert r_ping.status_code in (200, 404)
    if r_ping.status_code == 200:
        pj = r_ping.json()
        assert pj["ok"] is True
        assert "latency_ms" in pj

