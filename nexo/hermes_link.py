#!/usr/bin/env python3
"""hermes_link — Enlace entre el Hermes CLI local y el bus de agentes del hub.

Atlantis Studio no usa API keys: la IA del estudio son los Hermes Agents
conectados por los propios usuarios. Este worker conecta el Hermes de ESTA
máquina al bus del hub:

  1. Pregunta cada N segundos en /api/agents/inbox?agent=<NOMBRE>&pendientes=true
  2. Por cada mensaje/tarea pendiente, invoca el binario real de Hermes:
         hermes -z "<prompt>" --in <workspace> --yolo
  3. Publica el resultado con /api/agents/ack para que el otro extremo
     (el humano o el agente amigo) lo reciba por el bus.

Si el binario de Hermes no está instalado, el worker queda inactivo de forma
honesta (no finge ser el agente): lo registra en el log y reintenta cada ciclo.

Uso:
    python3 nexo/hermes_link.py --hub http://127.0.0.1:8795 --agent "Koko" [--token ...] [--interval 5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    from ai_engine import detectar_hermes_bin, leer_hermes_env
except Exception as e:
    print(f"[hermes_link] No se pudo importar ai_engine: {e}")
    sys.exit(2)


def _http(hub: str, metodo: str, ruta: str, token: str, datos: dict | None = None) -> dict:
    url = hub.rstrip("/") + ruta
    if token:
        sep = "&" if "?" in url else "?"
        url += f"{sep}token={urllib.parse.quote(token)}"
    cuerpo = None
    cabeceras = {}
    if datos is not None:
        cuerpo = json.dumps(datos).encode("utf-8")
        cabeceras["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=cuerpo, headers=cabeceras, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        return {"_error": str(e)}


async def _ejecutar_hermes(hermes_bin: Path, prompt: str, cwd: Path, timeout: int = 180) -> tuple[int, str]:
    env = dict(__import__("os").environ)
    env.update(leer_hermes_env())
    env.setdefault("TERM", "xterm-256color")
    proc = await asyncio.create_subprocess_exec(
        str(hermes_bin), "-z", prompt, "--in", str(cwd), "--yolo",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd),
        env=env,
    )
    salida: list[str] = []
    try:
        while True:
            linea = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not linea:
                break
            salida.append(linea.decode("utf-8", errors="replace"))
    except TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        salida.append("\n[HERMES TIMEOUT] La operación superó el límite de tiempo.\n")
        return (-1, "".join(salida))
    await proc.wait()
    return (proc.returncode or 0, "".join(salida))


def _construir_prompt(msg: dict, agente: str) -> str:
    tipo = msg.get("tipo", "mensaje")
    texto = msg.get("texto", "")
    de = msg.get("de", "?")
    if tipo == "tarea":
        return (
            f"Eres {agente}, el Hermes Agent de este usuario en Atlantis Studio.\n"
            f"{de} te asignó esta tarea:\n\n{texto}\n\n"
            "Ejecútala con tus herramientas locales en el workspace y resume el resultado."
        )
    if tipo == "auditoria":
        return (
            f"Eres {agente}, el Hermes Agent de este usuario en Atlantis Studio.\n"
            f"{de} solicita tu auditoría:\n\n{texto}\n\n"
            "Responde como auditor senior: directo, accionable, en español."
        )
    return (
        f"Eres {agente}, el Hermes Agent de este usuario en Atlantis Studio.\n"
        f"Mensaje de {de} por el bus de agentes:\n\n{texto}\n\n"
        "Responde de forma útil y concisa, en español."
    )


async def _ack_con_reintentos(hub: str, token: str, mid: str, agente: str,
                              resultado: str, estado: str, intentos: int = 5) -> bool:
    """Intenta confirmar un resultado en el hub con reintentos.

    Si el hub cayó justo después de ejecutar Hermes, el mensaje queda
    pendiente y se reintentaría (doble ejecución). Con reintentos del ack
    evitamos la mayoría de esos casos: entrega at-least-once documentada.
    """
    espera = 2
    for i in range(intentos):
        ack = _http(hub, "POST", "/api/agents/ack", token, {
            "id": mid,
            "from": agente,
            "result": resultado,
            "estado": estado,
        })
        if "_error" not in ack:
            return True
        if i < intentos - 1:
            print(f"[hermes_link] Ack fallido ({ack['_error']}) — reintento {i + 1}/{intentos} en {espera}s")
            await asyncio.sleep(espera)
            espera = min(espera * 2, 30)
    return False


async def ciclo(hub: str, agente: str, token: str, intervalo: int, workspace: Path) -> None:
    import time as _time
    hermes_bin = detectar_hermes_bin()
    if not hermes_bin:
        print(f"[hermes_link] Hermes no instalado en esta máquina — worker inactivo "
              f"(agente '{agente}' no recogerá mensajes). Instala Hermes para activarlo.")
    else:
        print(f"[hermes_link] Enlazado: agente '{agente}' <-> hub {hub} (binario: {hermes_bin})")

    fallos_hub = 0          # racha de fallos consecutivos de conexión
    hub_caido = False       # para registrar caída/recuperación una sola vez
    procesados: dict[str, float] = {}  # mid -> timestamp (evita re-ejecución inmediata)

    def espera_actual() -> int:
        # Backoff exponencial con tope: 5, 10, 20, 40, 60, 60...
        return min(intervalo * (2 ** min(fallos_hub, 4)), 60)

    while True:
        try:
            if not hermes_bin:
                hermes_bin = detectar_hermes_bin()
                if hermes_bin:
                    print(f"[hermes_link] Hermes detectado: {hermes_bin}. Activando enlace.")
                await asyncio.sleep(intervalo)
                continue

            # Limpieza de la ventana anti-doble-ejecución (10 min)
            ahora = _time.time()
            for mid in [m for m, ts in procesados.items() if ahora - ts > 600]:
                del procesados[mid]

            inbox = _http(hub, "GET",
                          f"/api/agents/inbox?agent={urllib.parse.quote(agente)}&pendientes=true",
                          token)
            if "_error" in inbox:
                fallos_hub += 1
                if not hub_caido:
                    hub_caido = True
                    print(f"[hermes_link] ⚠ Hub inalcanzable: {inbox['_error']}")
                espera = espera_actual()
                print(f"[hermes_link] Reintentando en {espera}s (fallo #{fallos_hub})…")
                await asyncio.sleep(espera)
                continue

            if hub_caido:
                hub_caido = False
                print("[hermes_link] ✓ Hub recuperado. Reanudando sondeo.")
            fallos_hub = 0

            for msg in inbox.get("mensajes", []):
                mid = msg.get("id", "")
                if mid in procesados:
                    continue  # ya ejecutado recientemente; evita doble ejecución
                try:
                    print(f"[hermes_link] <== [{msg.get('tipo')}] de {msg.get('de')}: "
                          f"{str(msg.get('texto', ''))[:80]}")
                    prompt = _construir_prompt(msg, agente)
                    ret, salida = await _ejecutar_hermes(hermes_bin, prompt, workspace)
                    resultado = salida.strip() or f"(sin salida, código {ret})"
                    print(f"[hermes_link] ==> respuesta ({len(resultado)} caracteres, código {ret})")
                    ok = await _ack_con_reintentos(
                        hub, token, mid, agente, resultado,
                        "completado" if ret == 0 else "error")
                    procesados[mid] = _time.time()
                    if not ok:
                        print(f"[hermes_link] ⚠ No se pudo confirmar {mid} tras varios intentos. "
                              f"Quedará pendiente y NO se re-ejecutará en 10 min.")
                except Exception as e:
                    # Un mensaje problemático no tumba el resto del lote
                    print(f"[hermes_link] ⚠ Error procesando {mid or '?'}: {e} — sigo con el resto.")
        except Exception as e:
            print(f"[hermes_link] Error en ciclo: {e}")
        await asyncio.sleep(intervalo)


def main() -> None:
    ap = argparse.ArgumentParser(description="Enlace Hermes CLI <-> bus de agentes de Atlantis Studio")
    ap.add_argument("--hub", default="http://127.0.0.1:8795", help="URL del hub NEXO")
    ap.add_argument("--agent", default="Koko", help="Nombre de este agente en el bus")
    ap.add_argument("--token", default="", help="Token del hub (si no es local)")
    ap.add_argument("--interval", type=int, default=5, help="Segundos entre sondeos del inbox")
    ap.add_argument("--workspace", default="", help="Workspace para ejecutar Hermes")
    args = ap.parse_args()

    workspace = Path(args.workspace).expanduser() if args.workspace else (Path.home() / "AtlantisProjects")
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[hermes_link] Aviso: no se pudo crear el workspace {workspace}: {e}")
    try:
        asyncio.run(ciclo(args.hub, args.agent, args.token, args.interval, workspace))
    except KeyboardInterrupt:
        print("\n[hermes_link] Detenido.")


if __name__ == "__main__":
    main()
