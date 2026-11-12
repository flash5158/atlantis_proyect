"""Router archivos y workspace — movido intacto desde server.py (NEXO 2.0).
Build v0.6.0: endpoints de conflictos (/api/conflictos*).

Comportamiento idéntico: mismas rutas, métodos, formas de request/response
y códigos de estado. Solo cambió el andamiaje (package backend/).
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, FileResponse

from backend.core import state
from backend.core.config import config, es_host, es_local, token_ok
from backend.core.state import (
    _arbol,
    _autor_version_archivo,
    _bump_version_archivo,
    _eliminar_version_archivo,
    _mover_version_archivo,
    _version_archivo,
    difundir_arbol,
    enviar_todos,
    resolver_archivo,
    sala_de,
)
from backend import sync as sync_mod

router = APIRouter()

@router.get("/api/arbol")
async def api_arbol(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    return {
        "arbol": _arbol(),
        "nombre": config.CFG.get("nombre", "jaime"),
        "companero": config.CFG.get("companero", "amigo"),
        "ia_nombre": config.CFG.get("ia_nombre", "antigravity"),
        "ia_companero": config.CFG.get("ia_companero", "hermes"),
        "tema": config.CFG.get("tema", "jarvis"),
        "token": config.TOKEN if es_local(request) else "",
        "file_locks": state.FILE_LOCKS,
    }


@router.get("/api/historial")
async def api_historial(proyecto: str, request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        return {"historial": sala_de(proyecto).historial()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/archivo")
async def api_archivo(ruta: str, request: Request, proyecto: str = Query("general"), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        p = resolver_archivo(proyecto, ruta)
        if not p.is_file():
            return JSONResponse({"error": "no es un archivo"}, status_code=404)
        texto = p.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "ruta": ruta, "contenido": texto,
                "version": _version_archivo(proyecto, ruta)}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/archivo")
async def api_archivo_guardar(ruta: str, request: Request, proyecto: str = Query("general"), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        contenido = body.get("contenido", "")
        esperado = body.get("expected_version")
        actual = _version_archivo(proyecto, ruta)
        # Last-write-wins honesto: si el cliente traía una versión vieja, avisa conflicto
        if esperado is not None and int(esperado) != actual:
            return JSONResponse(
                {"error": "conflicto de versión",
                 "detalle": "Otro colaborador guardó este archivo mientras lo editabas.",
                 "version": actual,
                 "autor": _autor_version_archivo(proyecto, ruta)},
                status_code=409,
            )
        p = resolver_archivo(proyecto, ruta)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contenido, encoding="utf-8")
        autor = str(body.get("autor", "")).strip() or config.CFG.get("nombre", "usuario")
        nueva_version = _bump_version_archivo(proyecto, ruta, autor)
        await difundir_arbol()
        await enviar_todos({
            "tipo": "file_changed",
            "proyecto": proyecto,
            "ruta": ruta,
            "version": nueva_version,
            "autor": autor,
            "size": len(contenido),
            # Contenido inline solo si es liviano; si no, el cliente lo pide por GET
            "contenido": contenido if len(contenido) <= 65536 else None,
        })
        # Sync entre servidores: este guardado viaja a los peers del equipo.
        await sync_mod.notificar_cambio_local(
            proyecto, ruta, base_version=actual, version=nueva_version,
            autor=autor, contenido=contenido, operacion="upsert")
        return {"ok": True, "ruta": ruta, "bytes": len(contenido), "version": nueva_version}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/archivo")
async def api_archivo_eliminar(ruta: str, request: Request, proyecto: str = Query("general"), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        borrados = sync_mod.colectar_borrado(proyecto, ruta)
        p = resolver_archivo(proyecto, ruta)
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
        else:
            return JSONResponse({"error": "archivo o carpeta no existe"}, status_code=404)
        await difundir_arbol()
        for ruta_borrada, version_borrada in borrados:
            await sync_mod.notificar_cambio_local(
                proyecto, ruta_borrada, base_version=version_borrada,
                version=version_borrada, autor=config.CFG.get("nombre", "usuario"),
                contenido=None, operacion="delete")
        return {"ok": True, "ruta": ruta}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _parsear_conflicto(nombre_backup: str) -> tuple[str, str] | None:
    """(ruta_original, autor) de un backup ``<ruta>.conflict.<autor>.<ts>``,
    o None si el nombre no es un backup de conflicto."""
    if ".conflict." not in nombre_backup:
        return None
    ruta, resto = nombre_backup.split(".conflict.", 1)
    if not ruta or not resto or "." not in resto:
        return None
    autor, _ts = resto.rsplit(".", 1)
    return ruta, autor


@router.get("/api/conflictos")
async def api_conflictos(request: Request, proyecto: str = Query("general"),
                         token: str = Query("")):
    """Archivos con backup de conflicto (.conflict.*) en el proyecto:
    [{ruta, conflicto_archivo, autor}]. Solo local o token válido."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        sala = sala_de(proyecto)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    conflictos = []
    try:
        for p in sorted(sala.directorio.rglob("*")):
            if not p.is_file() or ".conflict." not in p.name:
                continue
            rel = str(p.relative_to(sala.directorio))
            parsed = _parsear_conflicto(rel)
            if parsed is None:
                continue
            ruta, autor = parsed
            conflictos.append({"ruta": ruta, "conflicto_archivo": rel,
                               "autor": autor})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True, "conflictos": conflictos}


@router.get("/api/conflictos/contenido")
async def api_conflictos_contenido(request: Request, proyecto: str = Query("general"),
                                   ruta: str = Query(""),
                                   conflicto_archivo: str = Query(""),
                                   token: str = Query("")):
    """Contenido de ambos lados de un conflicto: {mio, suyo, version}."""
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        p_backup = resolver_archivo(proyecto, conflicto_archivo)
        parsed = _parsear_conflicto(conflicto_archivo)
        if parsed is None or parsed[0] != ruta:
            return JSONResponse({"error": "conflicto_archivo no es un backup de esa ruta."},
                                status_code=400)
        if not p_backup.is_file():
            return JSONResponse({"error": "backup de conflicto no encontrado."},
                                status_code=404)
        suyo = p_backup.read_text(encoding="utf-8", errors="replace")
        try:
            mio = resolver_archivo(proyecto, ruta).read_text(encoding="utf-8",
                                                             errors="replace")
        except Exception:
            mio = ""
        return {"ok": True, "ruta": ruta, "conflicto_archivo": conflicto_archivo,
                "mio": mio, "suyo": suyo,
                "version": _version_archivo(proyecto, ruta)}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/conflictos/resolver")
async def api_conflictos_resolver(request: Request, token: str = Query("")):
    """Resuelve un conflicto: escribe el contenido elegido, sube la
    versión, lo sincroniza y borra el backup de conflicto.

    eleccion: "mio" (mantener mi versión) | "suyo" (la versión del
    compañero) | "mezcla" (contenido manual en ``contenido``).
    """
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    proyecto = str(body.get("proyecto", "general") or "general")
    ruta = str(body.get("ruta", "") or "")
    conflicto_archivo = str(body.get("conflicto_archivo", "") or "")
    eleccion = str(body.get("eleccion", "") or "")
    if eleccion not in ("mio", "suyo", "mezcla"):
        return JSONResponse({"error": "eleccion debe ser mio, suyo o mezcla."},
                            status_code=400)
    if eleccion == "mezcla" and not isinstance(body.get("contenido"), str):
        return JSONResponse({"error": "mezcla requiere contenido."}, status_code=400)
    try:
        p_backup = resolver_archivo(proyecto, conflicto_archivo)
        parsed = _parsear_conflicto(conflicto_archivo)
        if parsed is None or parsed[0] != ruta:
            return JSONResponse({"error": "conflicto_archivo no es un backup de esa ruta."},
                                status_code=400)
        if not p_backup.is_file():
            return JSONResponse({"error": "backup de conflicto no encontrado."},
                                status_code=404)
        p = resolver_archivo(proyecto, ruta)
        version_antes = _version_archivo(proyecto, ruta)
        autor = config.CFG.get("nombre", "usuario")
        if eleccion == "mio":
            if p.is_file():
                contenido = p.read_text(encoding="utf-8", errors="replace")
                operacion = "upsert"
            else:
                contenido, operacion = None, "delete"
        elif eleccion == "suyo":
            contenido = p_backup.read_text(encoding="utf-8", errors="replace")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(contenido, encoding="utf-8")
            operacion = "upsert"
        else:
            contenido = body["contenido"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(contenido, encoding="utf-8")
            operacion = "upsert"
        nueva_version = _bump_version_archivo(proyecto, ruta, autor)
        p_backup.unlink()
        await difundir_arbol()
        await enviar_todos({
            "tipo": "file_changed", "proyecto": proyecto, "ruta": ruta,
            "version": nueva_version, "autor": autor,
            "size": len(contenido) if contenido else 0,
            "contenido": contenido if contenido and len(contenido) <= 65536 else None,
            "conflicto_resuelto": True,
        })
        # Sync: la resolución viaja a los peers (base = versión previa).
        await sync_mod.notificar_cambio_local(
            proyecto, ruta, base_version=version_antes, version=nueva_version,
            autor=autor, contenido=contenido, operacion=operacion)
        return {"ok": True, "ruta": ruta, "version": nueva_version}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/archivo/renombrar")
async def api_archivo_renombrar(request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        body = await request.json()
        origen = body.get("origen", "").strip()
        destino = body.get("destino", "").strip()
        proyecto = body.get("proyecto", "general")
        if not origen or not destino:
            return JSONResponse({"error": "rutas requeridas"}, status_code=400)
        p_origen = resolver_archivo(proyecto, origen)
        p_destino = resolver_archivo(proyecto, destino)
        if not p_origen.exists():
            return JSONResponse({"error": f"no existe {origen}"}, status_code=404)
        es_dir = p_origen.is_dir()
        pares_dir = (sync_mod.pares_renombre_directorio(proyecto, origen, destino)
                     if es_dir else [])
        version_origen = _version_archivo(proyecto, origen)
        contenido_origen = None
        binario_origen = False
        if p_origen.is_file():
            try:
                datos = p_origen.read_bytes()
                if len(datos) <= sync_mod.MAX_SYNC_CONTENIDO:
                    contenido_origen = datos.decode("utf-8")
                else:
                    contenido_origen = None
            except UnicodeDecodeError:
                binario_origen = True
                contenido_origen = None
            except OSError:
                contenido_origen = None
        p_destino.parent.mkdir(parents=True, exist_ok=True)
        p_origen.rename(p_destino)
        _mover_version_archivo(proyecto, origen, destino)
        await difundir_arbol()
        if es_dir:
            # Rename de carpeta: sync archivo por archivo (las versiones
            # viven por archivo; la carpeta misma tiene versión 0).
            for rel_viejo, rel_nuevo in pares_dir:
                v = _version_archivo(proyecto, rel_viejo)
                _mover_version_archivo(proyecto, rel_viejo, rel_nuevo)
                cont = None
                bin_f = False
                try:
                    datos = resolver_archivo(proyecto, rel_nuevo).read_bytes()
                    if len(datos) <= sync_mod.MAX_SYNC_CONTENIDO:
                        cont = datos.decode("utf-8")
                except UnicodeDecodeError:
                    bin_f = True
                except (OSError, ValueError):
                    cont = None
                await sync_mod.notificar_cambio_local(
                    proyecto, rel_nuevo, base_version=v, version=v,
                    autor=config.CFG.get("nombre", "usuario"),
                    contenido=cont, operacion="rename", origen=rel_viejo,
                    binario=bin_f)
        else:
            await sync_mod.notificar_cambio_local(
                proyecto, destino, base_version=version_origen,
                version=version_origen,
                autor=config.CFG.get("nombre", "usuario"),
                contenido=contenido_origen, operacion="rename", origen=origen,
                binario=binario_origen)
        return {"ok": True, "origen": origen, "destino": destino}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/buscar")
async def api_buscar(q: str, request: Request, token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    if not q or len(q) < 2:
        return {"resultados": []}
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "grep", "-n", "-I", "-i", q,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(config.WORKSPACE)
        )
        out, _ = await proc.communicate()
        resultados = []
        for line in out.decode("utf-8", errors="replace").splitlines()[:50]:
            parts = line.split(":", 2)
            if len(parts) == 3:
                resultados.append({"archivo": parts[0], "linea": int(parts[1]), "texto": parts[2].strip()})
        return {"ok": True, "resultados": resultados}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/descargar")
async def api_descargar(ruta: str, request: Request, proyecto: str = Query("general"), token: str = Query("")):
    if not es_local(request) and not token_ok(token):
        return JSONResponse({"error": "token inválido"}, status_code=401)
    try:
        p = resolver_archivo(proyecto, ruta)
        if not p.is_file():
            return JSONResponse({"error": "no es un archivo"}, status_code=404)
        return FileResponse(p, filename=Path(ruta).name)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/workspace/select")
async def api_workspace_select(request: Request, token: str = Query("")):
    # Solo el dueño: cambiar la raíz del workspace redefiniría qué puede
    # leer/escribir todo el mundo (incluidos los invitados).
    if not es_host(request, token):
        return JSONResponse(
            {"error": "Solo el anfitrión puede cambiar el workspace."}, status_code=403)
    try:
        body = await request.json()
        mode = str(body.get("mode", "clean"))
        target_path = str(body.get("path", "")).strip()
        project_name = str(body.get("name", "")).strip()

        if mode == "new" and project_name:
            clean_dir = config.HOME / "AtlantisProjects" / project_name
        elif mode == "existing" and target_path:
            clean_dir = Path(target_path).expanduser().resolve()
        else:
            clean_dir = config.HOME / "AtlantisProjects" / "sandbox"

        clean_dir.mkdir(parents=True, exist_ok=True)
        config.set_active_workspace(clean_dir)
        try:
            await difundir_arbol()
        except Exception as e:
            print(f"[WorkspaceSelect] Aviso difundiendo arbol: {e}")

        return {
            "ok": True,
            "workspace": str(clean_dir),
            "name": clean_dir.name,
            "arbol": _arbol(),
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Error seleccionando workspace: {e}"}, status_code=500)
