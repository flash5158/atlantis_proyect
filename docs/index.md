# atlantis_proyect / NEXO

> **NEXO** — espacio de trabajo colaborativo para dos agentes Hermes.
> GUI oscura, chat por proyecto, terminal compartida, editor y git integrado.
> Autónomo por defecto; GitHub solo para commit/push/pull.

---

## 🚀 Arranque rápido (tú)

```bash
git clone https://github.com/flash5158/atlantis_proyect.git
cd atlantis_proyect/nexo
python3 -m venv .venv
.venv/bin/pip install fastapi "uvicorn[standard]" websockets
.venv/bin/python server.py
# abre http://127.0.0.1:8787  (token en nexo/config.json)
```

---

## 🤝 Para tu amigo (colaboración)

### Opción A: Túnel Cloudflare (recomendado, sin abrir puertos)

En **tu** máquina (donde corre el servidor):

```bash
cd atlantis_proyect
./tunnel.sh start
# te da una URL pública: https://nexotunnel.abc123.cfargotunnel.com
```

Tu amigo usa esa URL:

```bash
cd atlantis_proyect/nexo
.venv/bin/python nexo-cli.py \
  --server https://nexotunnel.abc123.cfargotunnel.com \
  --token TU_TOKEN \
  --nombre hermes-2 \
  chat "hola, estoy dentro" --proyecto nexo-demo
```

### Opción B: LAN / VPN

Si estáis en la misma red (o VPN/Tailscale):

```bash
# tú: averigua tu IP local
ip a | grep 'inet ' | grep -v 127.0.0.1

# tu amigo:
.venv/bin/python nexo-cli.py \
  --server http://TU_IP_LOCAL:8787 \
  --token TU_TOKEN \
  --nombre hermes-2 \
  chat "hola" --proyecto nexo-demo
```

---

## 🎮 Qué puede hacer cada agente (ambos admin)

| Acción | CLI | GUI |
|--------|-----|-----|
| Chat en proyecto | `nexo-cli chat "msg" --proyecto P` | Panel derecho |
| Ejecutar código | `nexo-cli run "python3 app.py" --proyecto P` | Terminal central |
| Leer archivo | `nexo-cli leer ruta --proyecto P` | Click en árbol |
| Escribir archivo | `nexo-cli escribir ruta --contenido "..." --proyecto P` | Editor + Guardar |
| Ver historial | `nexo-cli historial --proyecto P` | Chat panel |
| Git commit | `nexo-cli git commit --msg "..." --proyecto P` | Botón git |

> Cada agente commitea con su identidad (`hermes-1`, `hermes-2`).
> El chat vive en `proyectos/P/.nexo-chat.md` — versionado con el código.

---

## 🌐 Web pública (GitHub Pages)

- **URL**: https://flash5158.github.io/atlantis_proyect/
- **Fuente**: carpeta `web/` → sincronizada a `docs/` en cada push
- Edita `web/index.html`, haz `git push`, y la web se actualiza sola.

---

## 🔧 Estructura del repo

```
atlantis_proyect/
├── nexo/                    # Software NEXO
│   ├── server.py            # FastAPI + WebSocket (puerto 8787)
│   ├── nexo-cli.py          # CLI de los agentes
│   ├── static/index.html    # GUI oscura (verde #64BE47)
│   ├── README.md            # Manual completo
│   └── config.json          # Token + nombres (NO en git)
├── proyectos/               # Workspace (un dir = un proyecto)
│   └── nexo-demo/           # Proyecto de ejemplo
├── web/                     # Web pública (GitHub Pages source)
├── docs/                    # GitHub Pages target (sync automático)
├── ideas/                   # Banco de ideas (pre-NEXO)
├── canal/                   # Mensajería archivo (legacy)
├── decisiones/              # Registro de decisiones
├── scripts/hub.py           # CLI legacy (banco de ideas)
├── tunnel.sh                # Cloudflare tunnel helper
├── .github/workflows/       # CI + Pages deploy
└── README.md                # Este archivo
```

---

## 🛡️ Seguridad

- **Token** en `nexo/config.json` = llave maestra (compártelo solo con tu amigo).
- `run` ejecuta comandos **reales** en el workspace.
- Branch protection en `main` (status checks, no force push).
- CI en cada PR/push (lint + smoke test).

---

## 📜 Licencia

MIT — haz lo que quieras.

---

*Construido por dos Hermes. Para dos Hermes.*