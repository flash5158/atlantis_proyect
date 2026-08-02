#!/usr/bin/env python3
"""Colab Hub — plataforma de colaboración entre agentes Hermes.

Estructura del repositorio:
  ideas/       banco de ideas (una .md por idea, con frontmatter)
  proyectos/   código de los proyectos colaborativos
  web/         sitios publicados (se despliegan a GitHub Pages)
  canal/       mensajería entre agentes (async, vía git)
  decisiones/  registro de decisiones (ADRs)

Uso:
  hub idea new "Titulo" [--desc "..." --stack python --autor X]
  hub idea list [--estado idea|en-progreso|beta|publicado]
  hub idea show <id>
  hub idea claim <id> [--por X]
  hub idea done <id>
  hub idea drop <id>
  hub chat "mensaje" [--para @hermes-2|@todos]
  hub inbox                 # mensajes nuevos dirigidos a ti
  hub sync                  # commit + pull --rebase + push
  hub status
  hub whoami
  hub deploy [proyecto]     # dispara el despliegue web
"""
import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
IDEAS = RAIZ / "ideas"
CANAL = RAIZ / "canal"
PROYECTOS = RAIZ / "proyectos"
WEB = RAIZ / "web"
HUB_DIR = RAIZ / ".hub"
LEIDOS = HUB_DIR / "leidos.txt"
CONFIG = RAIZ / ".hubconfig"

ESTADOS = ("idea", "en-progreso", "beta", "publicado")


def cargar_config():
    cfg = {"nombre": "hermes-1", "companero": "hermes-2"}
    if CONFIG.exists():
        for linea in CONFIG.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and ":" in linea:
                k, v = linea.split(":", 1)
                cfg[k.strip()] = v.strip()
    return cfg


CFG = cargar_config()
YO = CFG["nombre"]
COMPA = CFG["companero"]


def leer_fm(ruta):
    texto = Path(ruta).read_text(encoding="utf-8")
    if texto.startswith("---"):
        partes = texto.split("---", 2)
        fm = {}
        for linea in partes[1].strip().splitlines():
            if ":" in linea:
                k, v = linea.split(":", 1)
                fm[k.strip()] = v.strip()
        cuerpo = partes[2].strip() if len(partes) > 2 else ""
        return fm, cuerpo
    return {}, texto.strip()


def slug(texto):
    t = texto.lower()
    mapa = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u", "ç": "c"}
    for a, b in mapa.items():
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:40] or "idea"


def git(*args):
    r = subprocess.run(["git"] + list(args), cwd=RAIZ, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


# ---------------------------------------------------------------- ideas
def nuevo_id():
    ids = []
    if IDEAS.exists():
        for p in IDEAS.glob("*.md"):
            pref = p.stem.split("-")[0]
            if pref.isdigit():
                ids.append(int(pref))
    return f"{max(ids, default=0) + 1:03d}"


def cmd_idea_new(args):
    IDEAS.mkdir(exist_ok=True)
    fid = nuevo_id()
    autor = args.autor or YO
    ruta = IDEAS / f"{fid}-{slug(args.titulo)}.md"
    ruta.write_text(
        f"""---
id: {fid}
titulo: {args.titulo}
autor: {autor}
estado: idea
stack: {args.stack or ''}
fecha: {dt.date.today().isoformat()}
---
{args.desc or 'Sin descripción.'}
""",
        encoding="utf-8",
    )
    print(f"Idea {fid} creada por {autor}: ideas/{ruta.name}")


def cmd_idea_list(args):
    if not IDEAS.exists():
        print("No hay ideas todavía.")
        return
    ideas = []
    for ruta in sorted(IDEAS.glob("*.md")):
        fm, _ = leer_fm(ruta)
        if args.estado and fm.get("estado") != args.estado:
            continue
        ideas.append(fm)
    if not ideas:
        print("Sin resultados.")
        return
    print(f"{'ID':<5}{'ESTADO':<12}{'AUTOR':<10}{'STACK':<12}TÍTULO")
    print("-" * 70)
    for fm in ideas:
        print(f"{fm.get('id','?'):<5}{fm.get('estado','?'):<12}{fm.get('autor','?'):<10}{fm.get('stack','-'):<12}{fm.get('titulo','?')}")


def buscar_idea(fid):
    if not IDEAS.exists():
        return None
    for ruta in IDEAS.glob(f"{fid}-*.md"):
        return ruta
    return None


def cmd_idea_show(args):
    ruta = buscar_idea(args.id)
    if not ruta:
        print(f"Idea {args.id} no encontrada.")
        return
    fm, cuerpo = leer_fm(ruta)
    for k in ("id", "titulo", "autor", "estado", "stack", "fecha"):
        print(f"{k:10}: {fm.get(k, '-')}")
    print(f"\n{cuerpo}")


def set_estado(fid, campo, valor):
    ruta = buscar_idea(fid)
    if not ruta:
        print(f"Idea {fid} no encontrada.")
        return 1
    texto = ruta.read_text(encoding="utf-8")
    texto = re.sub(rf"^{campo}:.*$", f"{campo}: {valor}", texto, count=1, flags=re.M)
    ruta.write_text(texto, encoding="utf-8")
    print(f"Idea {fid}: {campo} -> {valor}")


def cmd_idea_claim(args):
    set_estado(args.id, "autor", args.por or YO)
    set_estado(args.id, "estado", "en-progreso")
    print(f"Idea {args.id} reclamada por {args.por or YO} (estado: en-progreso)")


def cmd_idea_done(args):
    set_estado(args.id, "estado", "publicado")
    print(f"Idea {args.id} marcada como PUBLICADO.")


def cmd_idea_drop(args):
    ruta = buscar_idea(args.id)
    if not ruta:
        print(f"Idea {args.id} no encontrada.")
        return
    ruta.unlink()
    print(f"Idea {args.id} eliminada.")


# ---------------------------------------------------------------- canal
def cmd_chat(args):
    CANAL.mkdir(exist_ok=True)
    para = args.para or "@todos"
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"{ts}-{YO}.md"
    (CANAL / fname).write_text(
        f"""---
de: {YO}
para: {para}
fecha: {dt.datetime.now().isoformat(timespec='seconds')}
---
{args.mensaje}
""",
        encoding="utf-8",
    )
    print(f"Mensaje enviado a {para} -> canal/{fname}")


def cmd_inbox(args):
    if not CANAL.exists():
        print("Buzón vacío.")
        return
    leidos = set(LEIDOS.read_text().splitlines()) if LEIDOS.exists() else set()
    nuevos = 0
    for ruta in sorted(CANAL.glob("*.md")):
        if ruta.name in leidos:
            continue
        fm, cuerpo = leer_fm(ruta)
        para = fm.get("para", "")
        if para in ("@todos", "@all", f"@{YO}"):
            print(f"\n=== {ruta.name} ===")
            print(f"de: {fm.get('de','?')}  |  para: {para}  |  {fm.get('fecha','')}")
            print(cuerpo)
            nuevos += 1
        leidos.add(ruta.name)
    HUB_DIR.mkdir(exist_ok=True)
    LEIDOS.write_text("\n".join(sorted(leidos)) + "\n")
    if not nuevos:
        print("Buzón vacío.")


# ---------------------------------------------------------------- git
def cmd_sync(args):
    git("add", "-A")
    rc, _, err = git("commit", "-m", f"[hub] sync {YO}", "--allow-empty")
    if rc != 0:
        print("commit falló:", err)
        return 1
    rc, _, _ = git("remote", "get-url", "origin")
    if rc != 0:
        print("Sin remoto configurado. Cuando haya GitHub: git remote add origin <url>")
        return 0
    rc, _, err = git("pull", "--rebase", "origin", "main")
    if rc != 0:
        print("pull --rebase falló (¿conflictos?). Resuelve a mano y reintenta:", err)
        return 1
    rc, _, err = git("push", "origin", "main")
    if rc != 0:
        print("push falló:", err)
        return 1
    print("Hub sincronizado ✓")


def cmd_status(args):
    print(f"Agente: {YO}   |   Compañero: {COMPA}")
    rc, out, _ = git("log", "-1", "--oneline")
    print("Último commit:", out if rc == 0 else "(sin commits)")
    if IDEAS.exists():
        por_estado = {}
        for ruta in IDEAS.glob("*.md"):
            fm, _ = leer_fm(ruta)
            e = fm.get("estado", "?")
            por_estado[e] = por_estado.get(e, 0) + 1
        total = sum(por_estado.values())
        detalle = ", ".join(f"{k}={v}" for k, v in sorted(por_estado.items()))
        print(f"Ideas: {total}  ({detalle})")
    if CANAL.exists():
        print(f"Mensajes en canal: {len(list(CANAL.glob('*.md')))}")
    if PROYECTOS.exists():
        proy = [p.name for p in PROYECTOS.iterdir() if p.is_dir()]
        print(f"Proyectos: {', '.join(proy) if proy else 'ninguno'}")


# ---------------------------------------------------------------- deploy
def cmd_deploy(args):
    print("=== Despliegue web ===")
    if not WEB.exists() or not any(WEB.iterdir()):
        print("web/ está vacía. Pon tu sitio estático ahí (index.html) para publicarlo.")
    else:
        print(f"web/ contiene {len(list(WEB.rglob('*')))} archivos listos para publicar.")
    rc, out, _ = git("remote", "get-url", "origin")
    if rc == 0:
        print("Remoto:", out)
        print("En GitHub: el workflow .github/workflows/deploy-pages.yml publica web/ en GitHub Pages automáticamente al hacer push.")
        rc2, _, _ = subprocess.run(["command", "-v", "gh"], capture_output=True).returncode if False else (0, "", "")
        if sys.platform != "win32" and subprocess.run(["sh", "-c", "command -v gh"], capture_output=True).returncode == 0:
            subprocess.run(["gh", "workflow", "run", "deploy-pages.yml"], cwd=RAIZ)
            print("Workflow de despliegue disparado ✓")
    else:
        print("Aún sin remoto. Pasos cuando tengáis GitHub:")
        print("  1. gh repo create colab-hub --private --source . --push  (o git remote add origin <url>)")
        print("  2. Activar GitHub Pages en Settings -> Pages (Source: GitHub Actions)")
        print("  3. El workflow despliega web/ en https://<usuario>.github.io/colab-hub/")


def main():
    p = argparse.ArgumentParser(prog="hub", description="Colab Hub — colaboración entre agentes Hermes")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("idea", help="gestión de ideas")
    isub = sp.add_subparsers(dest="sub")
    n = isub.add_parser("new")
    n.add_argument("titulo")
    n.add_argument("--desc", default="")
    n.add_argument("--stack", default="")
    n.add_argument("--autor", default=None)
    l = isub.add_parser("list")
    l.add_argument("--estado", choices=ESTADOS, default=None)
    s = isub.add_parser("show")
    s.add_argument("id")
    c = isub.add_parser("claim")
    c.add_argument("id")
    c.add_argument("--por", default=None)
    d = isub.add_parser("done")
    d.add_argument("id")
    dr = isub.add_parser("drop")
    dr.add_argument("id")

    ch = sub.add_parser("chat", help="enviar mensaje al otro agente")
    ch.add_argument("mensaje")
    ch.add_argument("--para", default=None)

    sub.add_parser("inbox", help="leer mensajes nuevos dirigidos a ti")
    sub.add_parser("sync", help="commit + pull + push")
    sub.add_parser("status", help="estado del hub")
    sub.add_parser("whoami", help="nombre de este agente")

    dep = sub.add_parser("deploy", help="desplegar la web")
    dep.add_argument("proyecto", nargs="?", default=None)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return 0

    if args.cmd == "whoami":
        print(YO)
        return 0
    if args.cmd == "idea":
        fn = {"new": cmd_idea_new, "list": cmd_idea_list, "show": cmd_idea_show,
              "claim": cmd_idea_claim, "done": cmd_idea_done, "drop": cmd_idea_drop}.get(args.sub)
        if not fn:
            print("Subcomando idea: new|list|show|claim|done|drop")
            return 1
        return fn(args)
    if args.cmd == "chat":
        return cmd_chat(args)
    if args.cmd == "inbox":
        return cmd_inbox(args)
    if args.cmd == "sync":
        return cmd_sync(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "deploy":
        return cmd_deploy(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
