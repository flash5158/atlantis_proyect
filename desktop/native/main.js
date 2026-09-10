const { app, BrowserWindow, globalShortcut, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

// A collaboration IDE must remain usable on VMs and remote desktops where
// Chromium's GPU process is unavailable.  The UI is CSS-only, so software
// compositing is a safe and portable default for the native shell.
app.disableHardwareAcceleration();

const workspaceRoot = path.resolve(__dirname, "../..");
let windowRef;
let hubProcess;
let quitting = false;

function appRoot() {
  return app.isPackaged ? path.join(process.resourcesPath, "atlantis") : workspaceRoot;
}

function workspacePath() {
  const configured = String(process.env.ATLANTIS_WORKSPACE || "").trim();
  if (configured) return path.resolve(configured);
  if (app.isPackaged) return path.join(app.getPath("documents"), "AtlantisWorkspace");
  return workspaceRoot;
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
  const python = process.env.ATLANTIS_PYTHON || path.join(root, "nexo", ".venv", "bin", "python");
  const workspace = workspacePath();
  fs.mkdirSync(workspace, { recursive: true });
  return {
    command: python,
    args: [path.join(root, "nexo", "server.py")],
    env: { ...process.env, PORT: String(port), NEXO_WORKSPACE: workspace }
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

async function createWindow() {
  const port = await findFreePort();
  startHub(port);
  await waitForHub(port);
  windowRef = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 980,
    minHeight: 620,
    frame: false,
    titleBarStyle: "hidden",
    backgroundColor: "#111318",
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });
  windowRef.once("ready-to-show", () => windowRef.show());
  windowRef.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  await windowRef.loadFile(path.join(appRoot(), "web", "index.html"), {
    query: { native: "1", hub: `http://127.0.0.1:${port}` }
  });
  windowRef.on("closed", () => { windowRef = undefined; });
}

function stopHub() {
  if (!hubProcess || hubProcess.killed) return;
  hubProcess.kill("SIGTERM");
  setTimeout(() => { if (hubProcess && !hubProcess.killed) hubProcess.kill("SIGKILL"); }, 2500);
}

app.whenReady().then(async () => {
  globalShortcut.register("CommandOrControl+Shift+Q", () => app.quit());
  globalShortcut.register("CommandOrControl+Shift+W", () => windowRef?.close());
  try {
    await createWindow();
  } catch (error) {
    console.error(error);
    app.quit();
  }
});

app.on("before-quit", () => { quitting = true; globalShortcut.unregisterAll(); stopHub(); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("activate", () => { if (!windowRef) createWindow().catch(console.error); });
