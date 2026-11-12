"""Adaptador Hermes — el camino principal de Daniel (sin cambios de lógica).

Delega intacto en ai_engine: detección del binario real y ejecución oneshot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

from .base import AgentAdapter

# Líneas de estado internas de ai_engine que no aportan al chat (ruido visual).
_BANNER_PREFIXES = (
    "⚡ [HERMES INICIADO]",
    "🤖 Ejecutando:",
    "✅ [HERMES FINALIZADO]",
)


class HermesAdapter(AgentAdapter):
    id = "hermes"
    label = "Hermes Agent"

    def detect(self) -> dict[str, Any] | None:
        import ai_engine

        hermes_bin = ai_engine.detectar_hermes_bin()
        if not hermes_bin:
            return None
        return {
            "detail": str(hermes_bin),
            "default_model": self._model_from_config(),
        }

    def _model_from_config(self) -> str:
        try:
            import ai_engine

            cfg = ai_engine.obtener_config_ia()
            return str(cfg.get("modelo", "") or "")
        except Exception:
            return ""

    async def arun(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> tuple[int, str]:
        import ai_engine

        return await ai_engine.ejecutar_hermes(prompt, cwd=cwd, yolo=True, timeout=timeout)

    def describe(self) -> str:
        return "Hermes Agent (CLI local — ~/.local/bin/hermes)"

    async def arun_stream(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Streaming REAL vía ai_engine.ejecutar_hermes (ya emite por líneas)."""
        import ai_engine

        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def on_chunk(chunk: str) -> None:
            try:
                queue.put_nowait(chunk)
            except Exception:
                pass

        async def runner() -> tuple[int, str]:
            return await ai_engine.ejecutar_hermes(
                prompt, cwd=cwd, yolo=True, timeout=timeout, on_chunk=on_chunk
            )

        task = asyncio.ensure_future(runner())
        full_parts: list[str] = []
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    piece = await asyncio.wait_for(queue.get(), timeout=0.25)
                except TimeoutError:
                    continue
                if piece is None:
                    continue
                if piece.startswith(_BANNER_PREFIXES):
                    continue  # ruido de estado interno, no contenido
                full_parts.append(piece)
                yield ("chunk", piece)
            ret, _raw = await task
            full = "".join(full_parts)
            if not full.strip() and ret != 0:
                # Sin contenido útil y con error: mostrar el mensaje honesto.
                msg = "El asistente no devolvió contenido (revisa que Hermes esté instalado y configurado)."
                yield ("chunk", msg)
                full = msg
            yield ("done", (ret, full))
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            raise
