"""Adaptador Google Antigravity — CLI `agy` (modo print, local)."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

from .base import AgentAdapter, _stream_subprocess


class AntigravityAdapter(AgentAdapter):
    id = "antigravity"
    label = "Google Antigravity (AGY)"

    def _bin(self) -> str | None:
        home_agy = Path.home() / ".local/bin/agy"
        if home_agy.is_file() and os.access(home_agy, os.X_OK):
            return str(home_agy)
        return shutil.which("agy")

    def detect(self) -> dict[str, Any] | None:
        b = self._bin()
        if not b:
            return None
        return {"detail": b, "default_model": "gemini-flash"}

    async def arun(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> tuple[int, str]:
        b = self._bin()
        if not b:
            return (1, "CLI 'agy' (Google Antigravity) no encontrado en esta máquina.")
        proc = await asyncio.create_subprocess_exec(
            b,
            "-p",
            prompt,
            "--output-format",
            "text",
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
            return (-1, "⏱ Google Antigravity superó el límite de tiempo.")
        text = out.decode("utf-8", errors="replace")
        return (proc.returncode or 0, text)

    def describe(self) -> str:
        return "Google Antigravity CLI (~/.local/bin/agy)"

    async def arun_stream(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Streaming de salida de `agy -p` por líneas."""
        b = self._bin()
        if not b:
            msg = "CLI 'agy' no encontrado en esta máquina."
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        proc = await asyncio.create_subprocess_exec(
            b,
            "-p",
            prompt,
            "--output-format",
            "text",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
        )
        full: list[str] = []
        async for piece in _stream_subprocess(proc, timeout):
            full.append(piece)
            yield ("chunk", piece)
        yield ("done", (proc.returncode or 0, "".join(full)))
