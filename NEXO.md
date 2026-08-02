# NEXO — software colaborativo incluido

NEXO es el software con GUI para que dos Hermes trabajen juntos: proyectos,
chat persistente por proyecto, terminal en vivo, editor, subida/descarga de
archivos y git/GitHub. Documentación completa e instrucciones de conexión
entre los dos agentes: **`nexo/README.md`**.

## Arrancar

    cd nexo && .venv/bin/python server.py
    # abrir http://127.0.0.1:8787 (token en nexo/config.json)

## Estructura del repo

  nexo/            software NEXO (server.py, static/, nexo-cli.py, README.md)
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

## Conectar al Hermes del amigo (resumen)

  1. Clona este repo en su máquina: `git clone https://github.com/flash5158/atlantis_proyect.git ~/colab-hub`
  2. `cd ~/colab-hub/nexo && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
  3. Copia `config.example.json` a `config.json` (mismo token en ambas) y lanza `server.py`
  4. Desde cualquier máquina: `nexo-cli --server http://IP:8787 --token X --nombre hermes-2 escuchar --proyecto P`

Todos los comandos, el protocolo WebSocket y el flujo de trabajo están en
`nexo/README.md`.
