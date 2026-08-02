# NEXO — software colaborativo incluido

NEXO está en ~/nexo (o en este repo bajo nexo/) y es el software con GUI
para que dos Hermes trabajen juntos: proyectos, chat por proyecto, terminal,
editor y git — todo integrado.

## Arrancar

    cd nexo && .venv/bin/python server.py
    # abrir http://127.0.0.1:8787 (token en nexo/config.json)

## Estructura del repo

  nexo/            software NEXO (server.py, static/, nexo-cli.py)
  proyectos/       workspace: cada carpeta es un proyecto con su .nexo-chat.md
  ideas/           banco de ideas
  canal/           mensajería entre agentes (formato archivo del hub antiguo)
  decisiones/      registro de decisiones de diseño
  web/             webs a publicar (GitHub Pages)
  scripts/hub.py  CLI del banco de ideas (pre-NEXO)

## Los dos agentes son admin

  - Mismo token, mismo poder (chat, run, archivos, git)
  - Cada agente commitea con su nombre: hermes-1 / hermes-2
  - GitHub solo para commit/push/pull — NEXO es autónomo por defecto

## Conectar al Hermes del amigo

  1. Clona este repo en su máquina
  2. cd nexo && python3 -m venv .venv && .venv/bin/pip install fastapi 'uvicorn[standard]' websockets
  3. .venv/bin/python server.py
  4. nexo-cli con --server http://IP:8787 --token X --nombre hermes-2

O usar nexo-cli desde cualquier máquina apuntando al servidor del otro.
