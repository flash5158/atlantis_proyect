const { app, BrowserWindow, Menu, globalShortcut, shell, ipcMain, dialog } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

app.disableHardwareAcceleration();

const workspaceRoot = path.resolve(__dirname, "../..");
const HOME = process.env.HOME || "/home/daniel-sosa";

let windowRef;
let hubProcess;
let quitting = false;

function appRoot() {
  return app.isPackaged ? path.join(process.resourcesPath, "atlantis") : workspaceRoot;
}

function workspacePath() {
  const configured = String(process.env.ATLANTIS_WORKSPACE || "").trim();
  if (configured) return path.resolve(configured);
  const cleanWorkspace = path.join(HOME, "AtlantisProjects", "sandbox");
  fs.mkdirSync(cleanWorkspace, { recursive: true });
  return cleanWorkspace;
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function runtimeCommand(port) {
  const root = appRoot();
  let python = process.env.ATLANTIS_PYTHON;
  if (!python) {
    const localVenv = path.join(root, "nexo", ".venv", "bin", "python");
    const devVenv = path.join(HOME, ".gemini/antigravity/scratch/atlantis_proyect/nexo/.venv/bin/python");
    if (fs.existsSync(localVenv)) {
      python = localVenv;
    } else if (fs.existsSync(devVenv)) {
      python = devVenv;
    } else {
      python = "python3";
    }
  }
  const workspace = workspacePath();
  fs.mkdirSync(workspace, { recursive: true });
  const nexoDir = path.join(root, "nexo");
  const existingPyPath = process.env.PYTHONPATH ? `${path.delimiter}${process.env.PYTHONPATH}` : "";
  return {
    command: python,
    args: [path.join(nexoDir, "server.py")],
    env: {
      ...process.env,
      PORT: String(port),
      NEXO_WORKSPACE: workspace,
      PYTHONPATH: `${nexoDir}${existingPyPath}`
    }
  };
}

function startHub(port) {
  const runtime = runtimeCommand(port);
  hubProcess = spawn(runtime.command, runtime.args, {
    cwd: appRoot(),
    env: runtime.env,
    stdio: ["ignore", "pipe", "pipe"]
  });
  hubProcess.stdout.on("data", data => console.log(`[hub] ${String(data).trim()}`));
  hubProcess.stderr.on("data", data => console.error(`[hub] ${String(data).trim()}`));
  hubProcess.once("error", error => console.error("No se pudo iniciar el hub Atlantis:", error.message));
  hubProcess.once("exit", (code, signal) => {
    if (!quitting && code !== 0) console.error(`Hub Atlantis detenido (${code ?? signal})`);
  });
}

async function waitForHub(port, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/status`);
      if (response.ok || response.status === 401) return;
    } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error("El hub Atlantis no respondió a tiempo");
}

function setupApplicationMenu() {
  const template = [
    {
      label: "Archivo",
      submenu: [
        {
          label: "Abrir Archivo o Carpeta...",
          accelerator: "CmdOrCtrl+O",
          click: async () => {
            if (!windowRef) return;
            windowRef.webContents.send("action:openPrompt");
          }
        },
        {
          label: "Abrir Archivo Individual...",
          click: async () => {
            if (!windowRef) return;
            const result = await dialog.showOpenDialog(windowRef, {
              title: "Abrir Archivo en Atlantis Studio",
              properties: ["openFile"],
              defaultPath: HOME
            });
            if (!result.canceled && result.filePaths.length) {
              windowRef.webContents.send("action:openFile", result.filePaths[0]);
            }
          }
        },
        {
          label: "Abrir Carpeta...",
          accelerator: "CmdOrCtrl+Shift+O",
          click: async () => {
            if (!windowRef) return;
            const result = await dialog.showOpenDialog(windowRef, {
              title: "Abrir Carpeta / Proyecto en Atlantis Studio",
              properties: ["openDirectory"],
              defaultPath: HOME
            });
            if (!result.canceled && result.filePaths.length) {
              windowRef.webContents.send("action:openFolder", result.filePaths[0]);
            }
          }
        },
        { type: "separator" },
        { role: "quit", label: "Salir de Atlantis Studio" }
      ]
    },
    {
      label: "Edición",
      submenu: [
        { role: "undo", label: "Deshacer", accelerator: "CmdOrCtrl+Z" },
        { role: "redo", label: "Rehacer", accelerator: "CmdOrCtrl+Shift+Z" },
        { type: "separator" },
        { role: "cut", label: "Cortar", accelerator: "CmdOrCtrl+X" },
        { role: "copy", label: "Copiar", accelerator: "CmdOrCtrl+C" },
        { role: "paste", label: "Pegar", accelerator: "CmdOrCtrl+V" },
        { role: "pasteAndMatchStyle", label: "Pegar texto sin formato", accelerator: "CmdOrCtrl+Shift+V" },
        { role: "delete", label: "Eliminar" },
        { role: "selectAll", label: "Seleccionar todo", accelerator: "CmdOrCtrl+A" }
      ]
    },
    {
      label: "Ver",
      submenu: [
        { role: "reload", label: "Recargar", accelerator: "CmdOrCtrl+R" },
        { role: "forceReload", label: "Forzar recarga", accelerator: "CmdOrCtrl+Shift+R" },
        { role: "toggleDevTools", label: "Herramientas de Desarrollador", accelerator: "CmdOrCtrl+Shift+I" },
        { type: "separator" },
        { role: "resetZoom", label: "Tamaño original" },
        { role: "zoomIn", label: "Acercar", accelerator: "CmdOrCtrl+=" },
        { role: "zoomOut", label: "Alejar", accelerator: "CmdOrCtrl+-" },
        { type: "separator" },
        { role: "togglefullscreen", label: "Pantalla completa", accelerator: "F11" }
      ]
    }
  ];
  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

ipcMain.on("window:close", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender) || windowRef;
  win?.close();
});

ipcMain.on("window:minimize", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender) || windowRef;
  win?.minimize();
});

ipcMain.on("window:maximize", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender) || windowRef;
  if (win?.isMaximized()) {
    win.unmaximize();
  } else {
    win?.maximize();
  }
});

ipcMain.handle("dialog:openFile", async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender) || windowRef;
  const result = await dialog.showOpenDialog(win, {
    title: "Abrir Archivo en Atlantis Studio",
    properties: ["openFile"],
    defaultPath: HOME
  });
  if (result.canceled || !result.filePaths.length) return null;
  return result.filePaths[0];
});

ipcMain.handle("dialog:openDirectory", async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender) || windowRef;
  const result = await dialog.showOpenDialog(win, {
    title: "Abrir Carpeta / Proyecto en Atlantis Studio",
    properties: ["openDirectory"],
    defaultPath: HOME
  });
  if (result.canceled || !result.filePaths.length) return null;
  return result.filePaths[0];
});

async function createWindow() {
  const port = await findFreePort();
  startHub(port);
  await waitForHub(port);

  setupApplicationMenu();

  const iconPath = path.join(HOME, ".local/share/icons/atlantis-studio.png");
  const preloadPath = path.join(__dirname, "preload.js");

  windowRef = new BrowserWindow({
    width: 1540,
    height: 960,
    minWidth: 1000,
    minHeight: 650,
    title: "Atlantis Studio",
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    frame: true,
    backgroundColor: "#111318",
    autoHideMenuBar: false,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: false,
      nodeIntegration: true,
      sandbox: false
    }
  });

  // Habilitar menú contextual del botón derecho para Copiar, Pegar, Cortar y Seleccionar
  windowRef.webContents.on("context-menu", (e, params) => {
    const isTextOrEditable = params.isEditable || (params.selectionText && params.selectionText.trim().length > 0);
    if (isTextOrEditable) {
      const contextMenu = Menu.buildFromTemplate([
        { role: "undo", label: "Deshacer", enabled: params.editFlags.canUndo },
        { role: "redo", label: "Rehacer", enabled: params.editFlags.canRedo },
        { type: "separator" },
        { role: "cut", label: "Cortar", enabled: params.editFlags.canCut },
        { role: "copy", label: "Copiar", enabled: params.editFlags.canCopy },
        { role: "paste", label: "Pegar", enabled: params.editFlags.canPaste },
        { type: "separator" },
        { role: "selectAll", label: "Seleccionar todo", enabled: params.editFlags.canSelectAll }
      ]);
      contextMenu.popup();
    }
  });

  windowRef.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url) && !url.includes("127.0.0.1") && !url.includes("localhost")) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  windowRef.webContents.on("did-fail-load", (e, code, desc, url) => {
    console.error(`[app] Error al cargar ${url}: ${desc} (${code})`);
  });

  windowRef.webContents.on("console-message", (e, level, msg, line, source) => {
    console.log(`[web-console] ${msg} (${source}:${line})`);
  });

  const appHtml = path.join(appRoot(), "web", "index.html");
  console.log(`[app] Cargando Atlantis Studio (${appHtml}) con hub en puerto ${port}...`);
  await windowRef.loadFile(appHtml, {
    query: { native: "1", hub: `http://127.0.0.1:${port}` }
  });

  windowRef.on("closed", () => {
    console.log("[app] Ventana de Atlantis cerrada");
    windowRef = undefined;
  });
}

function stopHub() {
  if (!hubProcess || hubProcess.killed) return;
  hubProcess.kill("SIGTERM");
  setTimeout(() => { if (hubProcess && !hubProcess.killed) hubProcess.kill("SIGKILL"); }, 2500);
}

app.whenReady().then(async () => {
  globalShortcut.register("CommandOrControl+Shift+I", () => windowRef?.webContents.toggleDevTools());
  globalShortcut.register("CommandOrControl+Shift+R", () => windowRef?.reload());
  globalShortcut.register("CommandOrControl+Shift+Q", () => app.quit());
  try {
    await createWindow();
  } catch (error) {
    console.error("Error al iniciar ventana de Atlantis:", error);
    app.quit();
  }
});

app.on("before-quit", () => {
  quitting = true;
  globalShortcut.unregisterAll();
  stopHub();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (!windowRef) createWindow().catch(console.error);
});
