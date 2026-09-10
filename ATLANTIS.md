# Atlantis Studio

Atlantis es un entorno Code-OSS/VSCodium con dos canales persistentes: `team`
para Daniel y su amigo y `agents` para sus dos workers Hermes. Cada persona
ejecuta su Hermes localmente; el hub coordina tareas, presencia, mensajes y
leases, pero nunca recibe las credenciales del modelo ni ejecuta el shell del
ordenador remoto.

## Desarrollo local

```bash
python3 -m venv .venv
.venv/bin/pip install -r nexo/requirements-atlantis.txt
PYTHONPATH=nexo .venv/bin/python -m atlantis_runtime \
  --workspace "$PWD" --port 8790 --name Daniel
```

La extensión guarda el token del hub en `SecretStorage`. El worker Hermes usa
`ATLANTIS_WORKER_TOKEN` y necesita la opción explícita `--allow-execution` para
modificar un worktree. La primera conexión del amigo se hace mediante una
invitación de un solo uso; el hub asigna una identidad propia y limita sus
permisos.

## Protocolo

Todas las respuestas y eventos usan un sobre versionado. Los eventos llevan un
`seq` monotónico y pueden reproducirse después de una desconexión con
`last_seq`. Los mensajes HTTP son idempotentes mediante `client_id`; las
escrituras de archivos usan `expected_revision`. Una tarea tiene un lease,
heartbeat, salida acotada, diff y verificaciones declaradas. Un worker no puede
hacer merge automático a la rama principal.

El canal `agents` conserva el diálogo técnico entre ambos Hermes. Una tarea
puede delegarse como máximo una vez y cada job puede producir cuatro handoffs;
el servidor valida estos límites. El resultado de un agente es evidencia para
la revisión, no una aprobación automática.

## Distribución

El editor se distribuye como extensión VS Code compatible con Code-OSS y
VSCodium. El runtime Python se congela con PyInstaller por plataforma; el
proyecto mantiene el mismo protocolo en Linux, macOS y Windows. Para un equipo
remoto, solo se expone el hub mediante HTTPS/WSS con autenticación; los workers
siguen en las máquinas de sus dueños.
