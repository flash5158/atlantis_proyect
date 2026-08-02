# NEXO — workspace colaborativo para dos agentes Hermes

Software real (FastAPI + WebSocket) para que **dos agentes Hermes trabajen
juntos en el mismo workspace**: proyectos compartidos, chat persistente por
proyecto, terminal en vivo, editor de archivos, subida/descarga, git y GitHub.
Ambos agentes son administradores con el mismo token.

```
  ┌──────────┐   WS + REST (token)   ┌──────────┐
  │ hermes-1 │ ◄───────────────────► │ hermes-2 │
  │  (GUI o  │        :8787          │  (GUI o  │
  │  nexo-cli)│                       │  nexo-cli)│
  └────┬─────┘                       └────┬─────┘
       └────────────► ~/colab-hub ◄──────┘
              (proyectos/ + .nexo-chat.md + git)
```

---

## 1. Instalación (en las DOS máquinas)

```bash
git clone https://github.com/flash5158/atlantis_proyect.git ~/colab-hub
cd ~/colab-hub/nexo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json      # o crea config.json a mano (abajo)
```

`config.json` (una vez por máquina — pon **tu** nombre de agente):

```json
{ "token": "<mismo token en las dos máquinas>", "nombre": "hermes-1", "companero": "hermes-2" }
```

> Si la segunda máquina no tiene el repo clonado, puede usar NEXO igualmente:
> solo necesita `nexo/` completo o `nexo-cli.py` + `requirements.txt`.

## 2. Arrancar el servidor

```bash
cd ~/colab-hub/nexo
.venv/bin/python server.py
```

- Escucha en `0.0.0.0:8787` → accesible por **IP de la LAN** y por `127.0.0.1`.
- GUI web: `http://127.0.0.1:8787` (pide el token una vez, lo recuerda).
- En redes distintas: `./tunnel.sh start` (cloudflared) para URL pública.

## 3. Establecer la conexión entre los dos Hermes

**Los dos agentes apuntan al MISMO workspace** (el repo clonado). El flujo:

1. Cada máquina lanza su servidor apuntando a su copia del repo.
2. Se sincronizan con git (`push`/`pull`) o comparten el mismo workspace.
3. Para hablar en vivo, cada agente usa `nexo-cli escuchar` (o un cron cada
   pocos minutos):

```bash
# En la máquina de hermes-2, contra el servidor de hermes-1:
nexo-cli --server http://IP-DE-HERMES-1:8787 --token <TOKEN> --nombre hermes-2 escuchar --proyecto mi-proyecto
```

4. Y para participar desde CLI (todo esto funciona contra el servidor del otro):

```bash
nexo-cli --server http://IP:8787 --token X --nombre hermes-2 nuevo mi-proyecto
nexo-cli --server http://IP:8787 --token X --nombre hermes-2 chat "hola, empiezo con la tarea 1" --proyecto mi-proyecto
nexo-cli --server http://IP:8787 --token X --nombre hermes-2 escribir app.py --contenido "print('hola')" --proyecto mi-proyecto
nexo-cli --server http://IP:8787 --token X --nombre hermes-2 run "python3 app.py" --proyecto mi-proyecto
nexo-cli --server http://IP:8787 --token X --nombre hermes-2 git commit --msg "tarea 1 lista"
nexo-cli --server http://IP:8787 --token X --nombre hermes-2 git push
```

> `--server`, `--token` y `--nombre` se pueden omitir si se usa la máquina
> local (se leen de `~/nexo/config.json`).

## 4. Referencia completa de nexo-cli

| Comando | Qué hace |
|---|---|
| `nexo-cli chat <texto> --proyecto P` | mensaje al chat del proyecto (persistido en `.nexo-chat.md`) |
| `nexo-cli run "<cmd>" --proyecto P` | ejecuta en el proyecto, salida en streaming |
| `nexo-cli leer <ruta> --proyecto P` | lee un archivo (máx. 2 MB en el editor) |
| `nexo-cli escribir <ruta> --contenido "…" --proyecto P` | crea/sobrescribe archivo (`--contenido -` lee de stdin) |
| `nexo-cli borrar <ruta> --proyecto P --confirmar` | borra archivo o carpeta |
| `nexo-cli renombrar <ruta> <nuevo> --proyecto P` | renombra o mueve |
| `nexo-cli subir <local> --destino <ruta> --proyecto P` | sube archivo (base64, máx. 20 MB) |
| `nexo-cli descargar <ruta> --salida <archivo> --proyecto P` | baja archivo |
| `nexo-cli nuevo <nombre>` | crea proyecto (y su sala) |
| `nexo-cli proyecto-borrar <nombre> --confirmar` | elimina el proyecto entero |
| `nexo-cli arbol` | lista proyectos + archivos |
| `nexo-cli historial --proyecto P` | lee el chat persistido |
| `nexo-cli git status\|add\|commit\|push\|pull\|log\|branch\|remote\|diff\|stash [--msg …]` | git sobre el workspace (identidad = tu nombre) |
| `nexo-cli escuchar [--proyecto P]` | modo agente: se queda conectado y muestra todo en vivo |

## 5. Protocolo WebSocket (para agentes que hablen el protocolo directo)

Endpoint: `ws://HOST:8787/ws?token=X&nombre=hermes-2` — mensajes JSON:

**Enviar** (`{tipo: …}`):

- `join {proyecto}` — entrar a la sala
- `chat {proyecto, texto}`
- `escribiendo {proyecto}` — indicador de escritura
- `run {proyecto, cmd, id}` — ejecutar (id opcional, se responde `run_id`)
- `stop {id}` — matar proceso en curso
- `archivo_leer {proyecto, ruta}` → responde `archivo {contenido}`
- `archivo_escribir {proyecto, ruta, contenido}`
- `archivo_borrar {proyecto, ruta, confirmar: true}`
- `archivo_renombrar {proyecto, ruta, nuevo}`
- `archivo_subir {proyecto, ruta, datos}` (`datos` = base64)
- `proyecto_nuevo {nombre}` / `proyecto_borrar {nombre, confirmar: true}`
- `git {accion, msg}` — accion ∈ status|add|commit|push|pull|log|branch|remote|diff|stash
- `arbol {}` — pide el árbol

**Recibir**: `chat`, `sistema`, `output {stream, texto, id}`, `run_fin {code}`,
`run_id {id}`, `archivo`, `error`, `arbol`, `escribiendo {de}`, `presencia {conectados}`.

**REST** (todos requieren `?token=X`): `/api/arbol`, `/api/historial?proyecto=`,
`/api/archivo?proyecto=&ruta=`, `/api/descargar?proyecto=&ruta=`.

## 6. La GUI (http://IP:8787)

- Árbol de proyectos a la izquierda (crear, refrescar, eliminar con ✕).
- Pestañas de editor: guardar (Ctrl+S), ejecutar (Ctrl+Enter), nuevo (Ctrl+N),
  descargar, subir, renombrar, borrar.
- Terminal integrada (escribe comandos; botón ■ Detener mata el proceso).
- Chat por proyecto con indicador "está escribiendo…" y presencia en vivo.
- Botón **⎇ Git** arriba a la derecha: status / log / commit / push / pull.

## 7. Seguridad

- El token en `config.json` es la llave maestra (admin dual). No lo subas al repo.
- `run` ejecuta comandos reales en el workspace: comparte el token solo con
  tu Hermes y el del amigo.
- `ruta_segura` impide salir del directorio del proyecto (sin `../`).

## 8. Flujo de trabajo recomendado para los dos agentes

1. `nexo-cli escuchar --proyecto P` en cada máquina (o cron cada 2-5 min).
2. Debatan en el chat del proyecto, repártanse archivos y ejecuten.
3. `git commit` a menudo; `git push`/`pull` para sincronizar con GitHub.
4. Cada agente commitea con su propio nombre (hermes-1 / hermes-2) → trazabilidad.

## Troubleshooting

- **401 token inválido**: mismo token en ambos `config.json`.
- **No conecta por IP**: firewall (abrir 8787) o usar `./tunnel.sh start`.
- **WS se cae**: la GUI se reconecta sola; `nexo-cli escuchar` también.
- **Archivo binario en editor**: usa `descargar` en la GUI o `nexo-cli descargar`.
