"""Tests del streaming REAL de asistentes IA (sin API keys).

Cubre:
- arun_stream del GenericCLIAdapter: chunks progresivos de un CLI que
  emite con pausas (no un solo bloque al final).
- Fallback honesto del adaptador base: un solo chunk.
- Endpoint SSE POST /api/agents/stream: eventos data + done.
- Cancelación: abortar a mitad mata el subproceso.
- Renderizador markdown-lite (web/md.js) vía node: escapa HTML y
  renderiza bloques de código con botón de copiar.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


TMP_HOME = Path(tempfile.mkdtemp(prefix="atlantis-test-home-"))
TMP_WS = Path(tempfile.mkdtemp(prefix="atlantis-test-ws-"))
(TMP_WS / "proyectos").mkdir(exist_ok=True)

os.environ["HOME"] = str(TMP_HOME)
os.environ["NEXO_WORKSPACE"] = str(TMP_WS)
os.environ["NEXO_PUBLIC"] = "1"
os.environ["PORT"] = "18799"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

import agents  # noqa: E402
from agents.base import AgentAdapter  # noqa: E402
from agents.custom import GenericCLIAdapter  # noqa: E402


def _chunked_script(tmp_path: Path) -> Path:
    """CLI falso que emite 5 líneas con pausas de 0.3s (streaming real)."""
    p = tmp_path / "chunky.sh"
    p.write_text(
        "#!/bin/bash\n"
        "for i in 1 2 3 4 5; do echo \"linea-$i\"; sleep 0.3; done\n",
        encoding="utf-8",
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_arun_stream_chunks_progresivos(tmp_path):
    script = _chunked_script(tmp_path)
    adapter = GenericCLIAdapter(name="Chunky", command=str(script))

    async def collect():
        chunks: list[str] = []
        tiempos: list[float] = []
        done = None
        t0 = time.monotonic()
        async for kind, payload in adapter.arun_stream("hola", timeout=15):
            if kind == "chunk":
                chunks.append(payload)
                tiempos.append(time.monotonic() - t0)
            elif kind == "done":
                done = payload
        return chunks, tiempos, done

    chunks, tiempos, done = asyncio.run(collect())
    texto = "".join(chunks)
    assert "linea-1" in texto and "linea-5" in texto
    # Progresivo de verdad: el primer chunk llega mucho antes que el último.
    assert len(chunks) >= 3, f"se esperaban varios chunks, llegaron {len(chunks)}"
    assert tiempos[-1] - tiempos[0] >= 0.8, "los chunks no llegaron espaciados en el tiempo"
    assert tiempos[0] < 0.9, "el primer chunk tardó demasiado (¿esperó al final?)"
    assert done is not None and done[0] == 0
    assert "linea-5" in done[1]


def test_arun_stream_fallback_honesto():
    class UnoSolo(AgentAdapter):
        id = "unosolo"
        label = "UnoSolo"

        def detect(self):
            return {"detail": "test", "default_model": ""}

        async def arun(self, prompt, cwd=None, timeout=180):
            await asyncio.sleep(0.1)
            return (0, "todo junto")

    async def collect():
        eventos = []
        async for kind, payload in UnoSolo().arun_stream("x", timeout=10):
            eventos.append((kind, payload))
        return eventos

    eventos = asyncio.run(collect())
    chunks = [p for k, p in eventos if k == "chunk"]
    dones = [p for k, p in eventos if k == "done"]
    assert len(chunks) == 1 and chunks[0] == "todo junto"
    assert len(dones) == 1 and dones[0] == (0, "todo junto")


def test_cancelacion_mata_proceso(tmp_path):
    p = tmp_path / "lento.sh"
    p.write_text("#!/bin/bash\nfor i in $(seq 1 100); do echo \"x-$i\"; sleep 0.2; done\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    adapter = GenericCLIAdapter(name="Lento", command=str(p))

    async def cancelar():
        gen = adapter.arun_stream("hola", timeout=60)
        primero = await gen.__anext__()
        assert primero[0] == "chunk"
        t0 = time.monotonic()
        await gen.aclose()  # simula al cliente cerrando la conexión (Detener)
        return time.monotonic() - t0

    dur = asyncio.run(cancelar())
    assert dur < 5, f"la cancelación tardó {dur:.1f}s (el proceso no murió)"


def _client():
    from fastapi.testclient import TestClient
    import server

    return TestClient(server.app)


def test_sse_stream_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".atlantis" / "config.json"
    if cfg.exists():
        cfg.unlink()
    script = _chunked_script(tmp_path)
    agents.save_slot("Streamy", "custom", command=str(script))
    agents.set_active_slot_name("Streamy")

    c = _client()
    r = c.post("/api/agents/stream", json={"prompt": "hola", "slot": "Streamy"})
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers["content-type"]
    eventos = []
    for bloque in r.text.split("\n\n"):
        lineas = [ln for ln in bloque.strip().splitlines() if ln.strip()]
        if not lineas:
            continue
        ev = "data"
        data = ""
        for ln in lineas:
            if ln.startswith("event:"):
                ev = ln.split(":", 1)[1].strip()
            elif ln.startswith("data:"):
                data = ln.split(":", 1)[1].strip()
        eventos.append((ev, json.loads(data) if data else {}))
    tipos = [e for e, _ in eventos]
    assert "start" in tipos and "done" in tipos
    chunks = [d.get("chunk", "") for e, d in eventos if e == "data"]
    assert len(chunks) >= 3, f"pocos chunks SSE: {len(chunks)}"
    done = next(d for e, d in eventos if e == "done")
    assert done["retcode"] == 0 and "linea-5" in done["full"]


def test_sse_slot_inexistente_honesto(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _client()
    r = c.post("/api/agents/stream", json={"prompt": "hola", "slot": "NoExiste"})
    assert r.status_code == 200
    assert "No existe el asistente" in r.text


def test_sse_requiere_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _client()
    r = c.post("/api/agents/stream", json={"prompt": "   "})
    assert r.status_code == 400


def test_markdown_renderer_node():
    """web/md.js: escapa HTML y renderiza bloques de código con copiar."""
    repo = Path(__file__).resolve().parent.parent
    md_js = repo / "web" / "md.js"
    assert md_js.exists(), "falta web/md.js"
    node = subprocess.run(
        ["node", "-e", r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const factory = new Function('module', 'exports', src + '\nreturn module.exports || { renderMarkdown };');
const m = { exports: {} };
const lib = factory(m, m.exports);
const assert = require('assert');
// 1. Escapa HTML (XSS)
let h = lib.renderMarkdown('<script>alert(1)</scr' + 'ipt> **hola**');
assert(!h.includes('<script>'), 'XSS no escapado: ' + h);
assert(h.includes('&lt;script&gt;'), 'escape incorrecto: ' + h);
// 2. Bloque de código con lenguaje + botón copiar
h = lib.renderMarkdown('mira:\n```python\nprint("x")\n```\nfin');
assert(h.includes('md-code'), 'falta bloque de código');
assert(h.includes('python'), 'falta etiqueta de lenguaje');
assert(h.includes('md-copy'), 'falta botón copiar');
assert(h.includes('print(&quot;x&quot;)') || h.includes('print("x")'), 'código perdido: ' + h);
// 3. Inline code, bold, italic, listas
h = lib.renderMarkdown('usa `x=1` y **fuerte** y *suave*\n- uno\n- dos');
assert(h.includes('<code'), 'falta inline code');
assert(h.includes('<strong>fuerte</strong>'), 'falta bold');
assert(h.includes('<li>uno</li>'), 'falta lista: ' + h);
// 4. El código dentro de bloques no se transforma
h = lib.renderMarkdown('```\n**no bold**\n```');
assert(!h.includes('<strong>no bold</strong>'), 'transformó dentro de código');
console.log('MD OK');
""", str(md_js)],
        capture_output=True, text=True, timeout=30,
    )
    assert node.returncode == 0, f"node falló: {node.stderr}\n{node.stdout}"
    assert "MD OK" in node.stdout
