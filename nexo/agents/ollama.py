"""Adaptador Ollama — modelos locales vía CLI `ollama` (cero nube, cero keys)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

from .base import AgentAdapter, _stream_subprocess


class OllamaAdapter(AgentAdapter):
    id = "ollama"
    label = "Ollama (local)"

    def __init__(self, model: str = ""):
        self.model = (model or "").strip()

    def _bin(self) -> str | None:
        return shutil.which("ollama")

    def _installed_models(self) -> list[str]:
        b = self._bin()
        if not b:
            return []
        try:
            res = subprocess.run([b, "list"], capture_output=True, text=True, timeout=10)
            models: list[str] = []
            for line in (res.stdout or "").splitlines()[1:]:
                name = line.split()[0] if line.split() else ""
                if name and name != "NAME":
                    models.append(name)
            return models
        except Exception:
            return []

    def detect(self) -> dict[str, Any] | None:
        b = self._bin()
        if not b:
            return None
        models = self._installed_models()
        if not models:
            # Binario presente pero sin modelos: honesto, no se marca detectado.
            return None
        chosen = self.model or models[0]
        return {"detail": f"{b} · modelo: {chosen}", "default_model": chosen}

    async def arun(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> tuple[int, str]:
        b = self._bin()
        if not b:
            return (1, "CLI 'ollama' no encontrado en esta máquina.")
        models = self._installed_models()
        if not models:
            return (1, "Ollama está instalado pero no tiene modelos descargados (`ollama pull <modelo>`).")
        model = self.model or models[0]
        proc = await asyncio.create_subprocess_exec(
            b,
            "run",
            model,
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return (-1, "⏱ Ollama superó el límite de tiempo.")
        text = out.decode("utf-8", errors="replace")
        return (proc.returncode or 0, text)

    async def arun_stream(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Streaming REAL: `ollama run` emite tokens a stdout por líneas."""
        b = self._bin()
        if not b:
            msg = "CLI 'ollama' no encontrado en esta máquina."
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        models = self._installed_models()
        if not models:
            msg = "Ollama está instalado pero no tiene modelos descargados (`ollama pull <modelo>`)."
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        model = self.model or models[0]
        proc = await asyncio.create_subprocess_exec(
            b,
            "run",
            model,
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
        )
        full: list[str] = []
        async for piece in _stream_subprocess(proc, timeout):
            full.append(piece)
            yield ("chunk", piece)
        yield ("done", (proc.returncode or 0, "".join(full)))

    def describe(self) -> str:
        return "Ollama — modelos 100% locales (`ollama run <modelo>`)"
