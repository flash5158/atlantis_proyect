const { ipcRenderer, contextBridge } = require("electron");

const api = {
  close: () => ipcRenderer.send("window:close"),
  minimize: () => ipcRenderer.send("window:minimize"),
  maximize: () => ipcRenderer.send("window:maximize"),
  openFileDialog: () => ipcRenderer.invoke("dialog:openFile"),
  openDirectoryDialog: () => ipcRenderer.invoke("dialog:openDirectory"),
  onOpenFile: (callback) => {
    ipcRenderer.on("action:openFile", (_, filePath) => callback(filePath));
  },
  onOpenFolder: (callback) => {
    ipcRenderer.on("action:openFolder", (_, folderPath) => callback(folderPath));
  },
  onOpenPrompt: (callback) => {
    ipcRenderer.on("action:openPrompt", () => callback());
  },
  isElectron: true
};

try {
  contextBridge.exposeInMainWorld("electronAPI", api);
} catch (e) {
  window.electronAPI = api;
}

window.electronAPI = api;
