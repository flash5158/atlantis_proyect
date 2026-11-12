"""Atlantis Studio — Adaptadores universales de asistentes IA.

Cualquier asistente de IA de cualquier marca puede conectarse al estudio,
no solo Hermes. Cada adaptador envuelve una herramienta LOCAL (CLI) del
usuario: cero API keys, cero nube.

Un adaptador implementa:
  - id: identificador único ("hermes", "claude-code", "ollama", "custom:<nombre>")
  - label: nombre visible ("Hermes Agent")
  - detect() -> dict | None: None si la herramienta no está presente en esta
    máquina; si está, {"detail": str, "default_model": str}.
  - arun(prompt, cwd, timeout) -> (retcode, salida): ejecuta de verdad.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator


async def _stream_subprocess(
    proc: asyncio.subprocess.Process,
    timeout: int,
) -> AsyncIterator[str]:
    """Lee stdout incrementalmente (línea a línea) mientras el proceso vive.

    Yields reales: cada línea se entrega en cuanto llega, sin esperar al final.
    Si se cancela (el cliente cerró la conexión), mata el proceso.
    """
    try:
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            except TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                yield "\n⏱ Se superó el límite de tiempo.\n"
                return
            if not line:
                break
            yield line.decode("utf-8", errors="replace")
        await proc.wait()
    except asyncio.CancelledError:
        try:
            proc.kill()
        except Exception:
            pass
        raise


class AgentAdapter:
    """Clase base. Ningún adaptador puede simular: detect() honesto, arun() real."""

    id: str = "base"
    label: str = "Asistente"

    def detect(self) -> dict[str, Any] | None:
        """None si no está disponible en esta máquina."""
        raise NotImplementedError

    async def arun(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> tuple[int, str]:
        """Ejecuta el asistente de verdad. Retorna (código, salida)."""
        raise NotImplementedError

    async def arun_stream(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Streaming real: yield ("chunk", texto) a medida que llega la salida,
        y al final ("done", (código, texto_completo)).

        Fallback honesto: si el CLI no soporta streaming incremental, entrega
        todo en un solo chunk (el cliente lo verá de una vez, sin fingir).
        """
        ret, out = await self.arun(prompt, cwd=cwd, timeout=timeout)
        yield ("chunk", out)
        yield ("done", (ret, out))

    def describe(self) -> str:
        return self.label

    def to_info(self) -> dict[str, Any]:
        det = self.detect()
        return {
            "id": self.id,
            "label": self.label,
            "detected": det is not None,
            "detail": (det or {}).get("detail", ""),
            "default_model": (det or {}).get("default_model", ""),
        }
