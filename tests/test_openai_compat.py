"""Tests del adaptador genérico OpenAI-compatible (LM Studio, llama.cpp…).

Cubre con un servidor HTTP falso (http.server en thread):
- detect() honesto: ok con /models real, None sin base_url, con servidor
  caído o con endpoint no compatible.
- arun_stream(): parsea el SSE estándar de OpenAI (data: … [DONE]).
- arun(): respuesta completa no-stream.
- Errores honestos en español: conexión imposible, 401, 404.
- Plumbing de slots: save_slot/load_slots/slot_adapter con base_url y api_key.
- test_connection(): ok con lista de modelos, error honesto si falla.

Usa HOME temporal (no toca la instalación real).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP_HOME = Path(tempfile.mkdtemp(prefix="atlantis-test-home-"))
os.environ["HOME"] = str(TMP_HOME)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

import agents  # noqa: E402
from agents.openai_compat import OpenAICompatAdapter  # noqa: E402
from agents import openai_compat as _oai_mod  # noqa: E402


class _FakeOAI(BaseHTTPRequestHandler):
    """Servidor OpenAI-compatible mínimo para tests."""

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/models":
            self._json({"object": "list",
                        "data": [{"id": "tiny-model"}, {"id": "otro-modelo"}]})
        elif self.path == "/privado/models":
            self._json({"error": {"message": "clave inválida"}}, status=401)
        else:
            self._json({"error": "no encontrado"}, status=404)

    def do_POST(self):
        if self.path == "/privado/chat/completions":
            self._json({"error": {"message": "clave inválida"}}, status=401)
            return
        if self.path != "/chat/completions":
            self._json({"error": "no encontrado"}, status=404)
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if body.get("stream"):
            chunks = ["Hola ", "mundo"]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for c in chunks:
                payload = json.dumps(
                    {"choices": [{"delta": {"content": c}}]}).encode()
                self.wfile.write(b"data: " + payload + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self._json({"choices": [{"message": {"content": "Respuesta completa"}}]})


def _servidor():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOAI)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


SRV = _servidor()
BASE = f"http://127.0.0.1:{SRV.server_address[1]}"


def _puerto_libre():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ------------------------------------------------------------ detect()
def test_detect_ok():
    a = OpenAICompatAdapter(base_url=BASE, model="tiny-model")
    det = a.detect()
    assert det is not None
    assert det["default_model"] == "tiny-model"
    assert BASE in det["detail"]


def test_detect_modelo_por_defecto_del_servidor():
    a = OpenAICompatAdapter(base_url=BASE)
    det = a.detect()
    assert det is not None
    assert det["default_model"] == "tiny-model"


def test_detect_sin_base_url():
    assert OpenAICompatAdapter().detect() is None


def test_detect_servidor_caido():
    a = OpenAICompatAdapter(base_url=f"http://127.0.0.1:{_puerto_libre()}")
    assert a.detect() is None


def test_detect_endpoint_no_compatible():
    a = OpenAICompatAdapter(base_url=BASE + "/nada")
    assert a.detect() is None


# ------------------------------------------------------------ streaming
def test_arun_stream_parsea_sse():
    async def collect():
        a = OpenAICompatAdapter(base_url=BASE, model="tiny-model")
        chunks, done = [], None
        async for kind, payload in a.arun_stream("hola"):
            if kind == "chunk":
                chunks.append(payload)
            elif kind == "done":
                done = payload
        return chunks, done

    chunks, done = asyncio.run(collect())
    assert "".join(chunks) == "Hola mundo"
    assert done == (0, "Hola mundo")


def test_arun_respuesta_completa():
    ret, out = asyncio.run(
        OpenAICompatAdapter(base_url=BASE, model="tiny-model").arun("hola"))
    assert ret == 0
    assert out == "Respuesta completa"


def test_arun_sin_api_key_funciona():
    # LM Studio / llama.cpp no piden clave: debe funcionar sin ella.
    ret, out = asyncio.run(
        OpenAICompatAdapter(base_url=BASE, model="tiny-model").arun("hola"))
    assert ret == 0


def test_error_conexion_honesto():
    url = f"http://127.0.0.1:{_puerto_libre()}"
    ret, out = asyncio.run(
        OpenAICompatAdapter(base_url=url, model="m").arun("hola"))
    assert ret != 0
    assert url in out  # el mensaje nombra el endpoint problemático


def test_stream_error_401_honesto():
    async def collect():
        a = OpenAICompatAdapter(base_url=BASE + "/privado", model="m")
        msgs, done = [], None
        async for kind, payload in a.arun_stream("hola"):
            if kind == "chunk":
                msgs.append(payload)
            elif kind == "done":
                done = payload
        return msgs, done

    msgs, done = asyncio.run(collect())
    assert done[0] != 0
    assert "401" in "".join(msgs)


# ------------------------------------------------------------ test_connection
def test_test_connection_ok():
    res = _oai_mod.test_connection(BASE)
    assert res["ok"] is True
    assert "tiny-model" in res["models"]


def test_test_connection_servidor_caido():
    res = _oai_mod.test_connection(f"http://127.0.0.1:{_puerto_libre()}")
    assert res["ok"] is False
    assert res["error"]


def test_test_connection_sin_url():
    res = _oai_mod.test_connection("")
    assert res["ok"] is False


# ------------------------------------------------------------ slots
def test_slot_plumbing_openai_compat():
    slot = agents.save_slot("Local LLM", "openai_compat",
                            model="tiny-model", base_url=BASE, api_key="")
    assert slot["base_url"] == BASE
    cargado = agents.get_slot("Local LLM")
    assert cargado["base_url"] == BASE
    assert cargado["api_key"] == ""
    a = agents.slot_adapter(cargado)
    assert isinstance(a, OpenAICompatAdapter)
    assert a.base_url == BASE
    assert a.model == "tiny-model"
    st = agents.slot_status(cargado)
    assert st["detected"] is True
    assert st["default_model"] == "tiny-model"


def test_slot_sin_base_url_rechazado():
    import pytest
    with pytest.raises(ValueError):
        agents.save_slot("Roto", "openai_compat", model="m")
