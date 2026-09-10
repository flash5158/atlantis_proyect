# Atlantis Studio Native

Shell Electron multiplataforma para abrir Atlantis como aplicación de escritorio.
La ventana usa un titlebar propio sin los controles de tráfico de macOS. Al
iniciar, levanta el hub local en un puerto libre, abre la interfaz web local y
cierra el proceso del hub al salir.

## Desarrollo

Desde `desktop/native`:

```bash
npm install
npm start
```

Atajos: `Ctrl/Cmd+Shift+W` cierra la ventana y `Ctrl/Cmd+Shift+Q` sale de la app.

## Paquetes

```bash
npm run package:linux
npm run package:mac
npm run package:windows
```

Los paquetes finales necesitan incluir un runtime Python de Atlantis para cada
sistema. En desarrollo se usa `nexo/.venv/bin/python` (o `ATLANTIS_PYTHON`).

En la aplicación empaquetada el workspace se guarda por defecto en
`~/Documents/AtlantisWorkspace`, para que los proyectos sobrevivan al cierre.
Puedes elegir otra carpeta definiendo `ATLANTIS_WORKSPACE` antes de iniciar.
