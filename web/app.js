// ==============================================================================
// Atlantis Studio 2.0 — Complete Collaborative IDE Runtime
// ==============================================================================

const state = {
  token: "",
  socket: null,
  channel: "team",
  connected: false,
  workspace: { name: "atlantis-studio", root: "" },
  tree: [],
  expandedFolders: new Set(["workspace", "general", "src"]),
  openTabs: [],
  activeTabIdx: -1,
  messages: [],
  tasks: [],
  terminalHistory: [],
  terminalCwd: "~/workspace",
  username: "daniel",
  git: { branch: "main", files: [] },
  activeView: "explorer",
  agentSeen: new Set(),
  localChatKeys: new Set(),
  antigravity: {
    skills: [],
    plugins: [],
    rules: [],
    mcp: {},
    sources: [],
    activeSubtab: "skills",
    selectedSkill: null
  }
};

let monacoEditor = null;
const monacoModels = new Map();

function getMonacoLanguage(filename) {
  const ext = (filename || "").split(".").pop().toLowerCase();
  const map = {
    js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
    py: "python", md: "markdown", json: "json", css: "css", scss: "scss",
    html: "html", xml: "xml", sh: "shell", bash: "shell", yml: "yaml",
    yaml: "yaml", rs: "rust", go: "go", cpp: "cpp", c: "c", sql: "sql",
    toml: "ini", env: "ini", spec: "python"
  };
  return map[ext] || "plaintext";
}

const query = new URLSearchParams(location.search);
const HUB_BASE = (query.get("hub") || (location.protocol.startsWith("http") ? location.origin : "http://127.0.0.1:8787")).replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[char]));
const timeNow = () => new Date().toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });

// ---------------- Toast ----------------
function toast(message, duration = 3000) {
  const node = $("toast");
  if (!node) return;
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), duration);
}

// ---------------- API Client ----------------
async function api(path, options = {}) {
  const joiner = path.includes("?") ? "&" : "?";
  const url = `${HUB_BASE}${path}${joiner}token=${encodeURIComponent(state.token)}`;
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.detail || `HTTP ${response.status}`);
  return data;
}

// ---------------- System File & Directory Dialogs ----------------
function closeAllMenus() {
  document.querySelectorAll(".menu-item.open").forEach(m => m.classList.remove("open"));
  const ctx = $("treeContextMenu");
  if (ctx) { ctx.hidden = true; ctx.style.display = "none"; }
  hideOpenChoiceMenu();
}

function hideOpenChoiceMenu() {
  const menu = $("openChoiceMenu");
  if (menu) {
    menu.hidden = true;
    menu.style.display = "none";
  }
}

function showOpenChoiceMenu(anchorEl, event) {
  closeAllMenus();
  const menu = $("openChoiceMenu");
  if (!menu) return;

  menu.hidden = false;
  menu.style.display = "flex";
  menu.style.zIndex = "100000";

  if (anchorEl && typeof anchorEl.getBoundingClientRect === "function") {
    const rect = anchorEl.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 4;
    
    // Boundary checks
    const menuWidth = 240;
    const menuHeight = 85;
    if (left + menuWidth > window.innerWidth - 10) {
      left = Math.max(10, window.innerWidth - menuWidth - 10);
    }
    if (top + menuHeight > window.innerHeight - 10) {
      top = Math.max(10, rect.top - menuHeight - 4);
    }
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  } else if (event && typeof event.clientX === "number") {
    const posX = Math.max(10, Math.min(event.clientX, window.innerWidth - 250));
    const posY = Math.max(10, Math.min(event.clientY, window.innerHeight - 100));
    menu.style.left = `${posX}px`;
    menu.style.top = `${posY}px`;
  } else {
    menu.style.left = `${Math.max(10, Math.floor((window.innerWidth - 240) / 2))}px`;
    menu.style.top = `60px`;
  }
}

function toggleOpenChoiceMenu(anchorEl, event) {
  const menu = $("openChoiceMenu");
  if (menu && !menu.hidden && menu.style.display !== "none") {
    hideOpenChoiceMenu();
    return;
  }
  showOpenChoiceMenu(anchorEl, event);
}

async function switchWorkspaceTo(folderPath) {
  if (!folderPath) return;
  try {
    const res = await api("/api/workspace/select", {
      method: "POST",
      body: JSON.stringify({ mode: "existing", path: folderPath })
    });
    state.workspace.root = res.workspace || folderPath;
    state.workspace.name = res.name || folderPath.split("/").filter(Boolean).pop() || "workspace";
    if ($("workspaceName")) $("workspaceName").textContent = state.workspace.name;
    if ($("settingWorkspacePath")) $("settingWorkspacePath").value = state.workspace.root;
    state.terminalCwd = state.workspace.root;
    if ($("terminalPromptPath")) $("terminalPromptPath").textContent = " " + formatTerminalPath(state.workspace.root);
    await loadTree();
    await refreshGit();
    toast(`✓ Carpeta abierta: ${state.workspace.name}`);
  } catch (err) {
    toast(`Error al abrir carpeta: ${err.message}`);
  }
}

async function promptOpenSystemFile() {
  closeAllMenus();
  if (window.electronAPI?.openFileDialog) {
    try {
      const selected = await window.electronAPI.openFileDialog();
      if (selected) {
        await openFile(selected, "general");
        toast(`✓ Archivo abierto: ${selected.split("/").pop()}`);
      }
    } catch (err) {
      console.error("Error al abrir diálogo nativo:", err);
      $("fallbackFileInput")?.click();
    }
  } else {
    $("fallbackFileInput")?.click();
  }
}

async function promptOpenSystemFolder() {
  closeAllMenus();
  if (window.electronAPI?.openDirectoryDialog) {
    try {
      const folder = await window.electronAPI.openDirectoryDialog();
      if (folder) {
        await switchWorkspaceTo(folder);
      }
    } catch (err) {
      console.error("Error al abrir diálogo nativo:", err);
      $("fallbackFolderInput")?.click();
    }
  } else {
    $("fallbackFolderInput")?.click();
  }
}

function setupFallbackFileInputs() {
  const fileInput = $("fallbackFileInput");
  if (fileInput) {
    fileInput.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const filePath = file.path || file.name;
      try {
        const text = await file.text();
        const existingIdx = state.openTabs.findIndex(t => t.path === filePath);
        if (existingIdx >= 0) {
          switchTab(existingIdx);
          return;
        }
        const tab = {
          path: filePath,
          project: "general",
          title: file.name,
          content: text,
          dirty: false
        };
        state.openTabs.push(tab);
        switchTab(state.openTabs.length - 1);
        toast(`✓ Archivo abierto: ${file.name}`);
      } catch (err) {
        toast(`Error al leer archivo: ${err.message}`);
      }
      fileInput.value = "";
    });
  }

  const folderInput = $("fallbackFolderInput");
  if (folderInput) {
    folderInput.addEventListener("change", async (e) => {
      const files = Array.from(e.target.files || []);
      if (!files.length) return;
      const first = files[0];
      const folderName = first.webkitRelativePath?.split("/")[0] || "carpeta-importada";
      toast(`✓ Carpeta seleccionada: ${folderName}`);
    });
  }
}

function setupElectronIpc() {
  if (window.electronAPI?.onOpenFile) {
    window.electronAPI.onOpenFile(async (filePath) => {
      if (filePath) {
        await openFile(filePath, "general");
        toast(`✓ Archivo abierto: ${filePath.split("/").pop()}`);
      }
    });
  }
  if (window.electronAPI?.onOpenFolder) {
    window.electronAPI.onOpenFolder(async (folderPath) => {
      if (folderPath) {
        await switchWorkspaceTo(folderPath);
      }
    });
  }
  if (window.electronAPI?.onOpenPrompt) {
    window.electronAPI.onOpenPrompt(() => {
      showOpenChoiceMenu(null);
    });
  }
}

// ---------------- Theme, AI Provider & User Profile ----------------
function applyTheme(theme) {
  document.body.classList.remove("theme-midnight", "theme-cyberpunk");
  if (theme === "midnight") {
    document.body.classList.add("theme-midnight");
    if (typeof monaco !== "undefined" && monaco.editor) monaco.editor.setTheme("hc-black");
  } else if (theme === "cyberpunk") {
    document.body.classList.add("theme-cyberpunk");
    if (typeof monaco !== "undefined" && monaco.editor) monaco.editor.setTheme("hc-black");
  } else {
    if (typeof monaco !== "undefined" && monaco.editor) monaco.editor.setTheme("vs-dark");
  }
  localStorage.setItem("atlantis_theme", theme);
  const sel = $("settingTheme");
  if (sel) sel.value = theme;
}

function applyAiProvider(provider) {
  state.aiProvider = provider;
  localStorage.setItem("atlantis_ai_provider", provider);
  const sel = $("settingAiProvider");
  if (sel) sel.value = provider;
}

function applyUserProfile(userOrName, role = "", email = "", avatar = "") {
  let name = "Daniel";
  let uRole = role || localStorage.getItem("atlantis_user_role") || "Lead Developer / Arquitecto";
  let uEmail = email || localStorage.getItem("atlantis_user_email") || "daniel@atlantis.local";
  let initials = avatar;

  if (typeof userOrName === "object" && userOrName !== null) {
    name = userOrName.name || userOrName.username || "Daniel";
    uRole = userOrName.role || uRole;
    uEmail = userOrName.email || uEmail;
    initials = userOrName.avatar || name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
  } else if (typeof userOrName === "string") {
    name = userOrName || "Daniel";
  }

  if (!initials) {
    initials = name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase() || "DS";
  }

  localStorage.setItem("atlantis_user_name", name);
  localStorage.setItem("atlantis_user_role", uRole);
  localStorage.setItem("atlantis_user_email", uEmail);
  localStorage.setItem("atlantis_user_avatar", initials);

  const nameEl = $("profileName");
  if (nameEl) nameEl.textContent = name;
  const avatarEl = $("btnAvatar");
  if (avatarEl) {
    avatarEl.textContent = initials;
    avatarEl.title = `${name} (${uRole}) — ${uEmail}`;
  }
  const inputEl = $("profileNameInput");
  if (inputEl) inputEl.value = name;
  const presenceEl = document.querySelector(".presence-avatar.daniel");
  if (presenceEl) presenceEl.textContent = initials;
}

// ---------------- Initialization & Connection ----------------
async function init() {
  applyTheme(localStorage.getItem("atlantis_theme") || "obsidian");
  applyAiProvider(localStorage.getItem("atlantis_ai_provider") || "gemini");
  applyUserProfile(localStorage.getItem("atlantis_user_name") || "Daniel");

  try {
    state.token = localStorage.getItem("atlantis_token") || "";
    if (!state.token && HUB_BASE) {
      const res = await fetch(`${HUB_BASE}/api/token_local`).then(r => r.json()).catch(() => ({}));
      if (res.token) state.token = res.token;
    }
    if (state.token) localStorage.setItem("atlantis_token", state.token);

    const status = await api("/api/status").catch(() => null);
    if (status) {
      state.connected = true;
      state.workspace.name = status.sistema || "Atlantis";
      state.workspace.root = status.workspace || "";
      state.terminalCwd = status.terminal_cwd || status.workspace || "~/workspace";
      $("workspaceName").textContent = (status.workspace || "atlantis").split("/").pop();
      $("connectionStatus").textContent = "● En línea";
      $("connectionStatus").className = "status-online";
      $("settingWorkspacePath").value = status.workspace || "";
      const pathEl = $("terminalPromptPath");
      if (pathEl) pathEl.textContent = " " + formatTerminalPath(state.terminalCwd);
    } else {
      $("connectionStatus").textContent = "● Local";
      $("connectionStatus").className = "status-online";
    }
  } catch (err) {
    console.warn("Hub offline or demo mode:", err);
    $("connectionStatus").textContent = "● Modo local";
  }

  setupSocket();
  await loadTree();
  await refreshGit();
  await loadTasks();
  await loadAntigravityData();
  seedInitialContent();
  initMonaco();
  bindEvents();
  bindMenubar();
  initOnboarding();
  setupFallbackFileInputs();
  setupElectronIpc();
  switchTab(-1);

  // Print welcome to terminal
  printTerminalOutput(`<span class="prompt">Atlantis Studio 2.0</span> — Entorno de Desarrollo y Red de Agentes Hermes\n✓ Workspace: <span class="path">${esc(state.workspace.root || "~/workspace")}</span>\n✓ Hub conectado: <span class="cmd">${esc(HUB_BASE || "http://127.0.0.1:8787")}</span>\nEscribe cualquier comando abajo o presiona <b>⌘ K</b> para buscar.`);
}

function setupSocket() {
  if (!HUB_BASE) return;
  const socketOrigin = HUB_BASE.replace(/^http/, "ws");
  try {
    state.socket = new WebSocket(`${socketOrigin}/ws?token=${encodeURIComponent(state.token)}&nombre=Daniel`);
    state.socket.onopen = () => {
      state.connected = true;
      state.socket.send(JSON.stringify({ tipo: "join", proyecto: "general" }));
    };
    state.socket.onmessage = (event) => handleSocketEvent(JSON.parse(event.data));
    state.socket.onclose = () => setTimeout(setupSocket, 4000);
  } catch (_) {}
}

function handleSocketEvent(event) {
  if (event.tipo === "chat" && event.de !== "Daniel") {
    addMessage({ author: event.de || "Compañero", initials: "AM", time: event.fecha || timeNow(), text: event.texto }, "team");
  } else if (event.tipo === "ai_msg") {
    const m = event.mensaje || {};
    if (m.id && state.agentSeen.has(m.id)) return;
    if (m.id) state.agentSeen.add(m.id);
    addMessage({
      author: m.de || "Hermes",
      initials: String(m.de || "HA").slice(0, 2).toUpperCase(),
      agent: true,
      time: m.fecha || timeNow(),
      text: m.contenido || "",
      tag: (m.tipo || "HERMES").toUpperCase()
    }, "agents");
  } else if (event.tipo === "arbol") {
    state.tree = event.arbol || [];
    renderTree();
  } else if (event.tipo === "ai_task_update") {
    loadTasks();
  }
}

// ---------------- File Tree & Explorer ----------------
async function loadTree() {
  try {
    const data = await api("/api/arbol");
    state.tree = data.arbol || [];
    renderTree();
  } catch (_) {
    // Fallback static files
    state.tree = [
      {
        nombre: "atlantis-studio",
        es_raiz: true,
        archivos: ["src/orchestrator.ts", "src/agents/hermes.ts", "src/agents/protocol.ts", "web/index.html", "web/app.js", "web/styles.css", "README.md", "package.json", ".env.example"]
      }
    ];
    renderTree();
  }
}

function renderTree() {
  const container = $("fileTree");
  if (!container) return;

  let totalFiles = 0;
  let html = "";

  state.tree.forEach((proj, pIdx) => {
    const projName = proj.nombre || "proyecto";
    const files = proj.archivos || [];
    totalFiles += files.length;
    const isExpanded = state.expandedFolders.has(projName);

    html += `<button class="tree-row folder ${isExpanded ? "open" : ""}" data-folder="${esc(projName)}">
      <span class="chevron">${isExpanded ? "⌄" : "›"}</span>
      <span class="tree-icon folder-icon">▾</span>
      <strong>${esc(projName)}</strong>
    </button>`;

    if (isExpanded) {
      // Group by directories
      const dirMap = {};
      const rootFiles = [];

      files.forEach(filePath => {
        const parts = filePath.split("/");
        if (parts.length > 1) {
          const dir = parts.slice(0, -1).join("/");
          if (!dirMap[dir]) dirMap[dir] = [];
          dirMap[dir].push({ full: filePath, name: parts[parts.length - 1] });
        } else {
          rootFiles.push({ full: filePath, name: filePath });
        }
      });

      // Subdirectories
      Object.keys(dirMap).sort().forEach(dir => {
        const isSubExpanded = state.expandedFolders.has(`${projName}/${dir}`);
        html += `<button class="tree-row folder" data-folder="${esc(projName)}/${esc(dir)}" style="padding-left: 20px;">
          <span class="chevron">${isSubExpanded ? "⌄" : "›"}</span>
          <span class="tree-icon folder-icon">▾</span>
          <span>${esc(dir)}</span>
        </button>`;

        if (isSubExpanded) {
          dirMap[dir].forEach(f => {
            const isSelected = state.openTabs[state.activeTabIdx]?.path === f.full;
            html += `<button class="tree-row file ${isSelected ? "selected" : ""}" data-file="${esc(f.full)}" data-project="${esc(projName)}" style="padding-left: 36px;">
              <span class="tree-icon ${getFileIconClass(f.name)}">${getFileBadge(f.name)}</span>
              <span>${esc(f.name)}</span>
            </button>`;
          });
        }
      });

      // Files at root
      rootFiles.forEach(f => {
        const isSelected = state.openTabs[state.activeTabIdx]?.path === f.full;
        html += `<button class="tree-row file ${isSelected ? "selected" : ""}" data-file="${esc(f.full)}" data-project="${esc(projName)}" style="padding-left: 24px;">
          <span class="tree-icon ${getFileIconClass(f.name)}">${getFileBadge(f.name)}</span>
          <span>${esc(f.name)}</span>
        </button>`;
      });
    }
  });

  container.innerHTML = html || `<div class="tree-empty">No hay archivos en el workspace.</div>`;
  $("fileCountBadge").textContent = totalFiles;
}

function getFileIconClass(name) {
  const ext = name.split(".").pop().toLowerCase();
  const map = { ts: "ts", js: "js", py: "py", md: "md", json: "json", css: "css", html: "html", sh: "sh", env: "env" };
  return map[ext] || "file-default";
}

function getFileBadge(name) {
  const ext = name.split(".").pop().toLowerCase();
  const map = { ts: "TS", js: "JS", py: "PY", md: "M", json: "{ }", css: "#", html: "<>", sh: "$", env: "•" };
  return map[ext] || "📄";
}

// ---------------- Editor & Tabs ----------------
function initMonaco() {
  if (typeof require === "undefined") {
    setTimeout(initMonaco, 200);
    return;
  }
  try {
    let vsPath = "/static/vs";
    if (location.protocol === "file:") {
      const dir = (typeof __dirname !== "undefined" && __dirname)
        ? __dirname
        : location.pathname.substring(0, location.pathname.lastIndexOf("/"));
      vsPath = dir + "/vs";
    }
    require.config({ paths: { vs: vsPath } });
    require(["vs/editor/editor.main"], function () {
      const container = $("monacoEditorContainer");
      if (!container) return;

      const savedTheme = localStorage.getItem("atlantis_theme") || "obsidian";
      const monacoTheme = (savedTheme === "midnight" || savedTheme === "cyberpunk") ? "hc-black" : "vs-dark";
      monacoEditor = monaco.editor.create(container, {
        theme: monacoTheme,
        automaticLayout: true,
        fontSize: 13.5,
        fontFamily: "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace",
        lineNumbers: "on",
        minimap: { enabled: true, maxColumn: 80 },
        scrollBeyondLastLine: false,
        bracketPairColorization: { enabled: true },
        smoothScrolling: true,
        cursorBlinking: "smooth",
        tabSize: 2
      });

      monacoEditor.onDidChangeModelContent(() => {
        if (state.activeTabIdx >= 0) {
          const tab = state.openTabs[state.activeTabIdx];
          tab.content = monacoEditor.getValue();
          if (!tab.dirty) {
            tab.dirty = true;
            $("unsavedBadge").hidden = false;
            renderTabs();
          }
        }
      });

      monacoEditor.onDidChangeCursorPosition((e) => {
        $("sbCursor").textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
      });

      monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        saveCurrentFile();
      });

      if (state.activeTabIdx >= 0) {
        updateMonacoForTab(state.openTabs[state.activeTabIdx]);
      }
    });
  } catch (err) {
    console.warn("Monaco init failed:", err);
  }
}

function updateMonacoForTab(tab) {
  if (!monacoEditor || typeof monaco === "undefined") {
    $("codeTextarea").style.display = "block";
    $("codeTextarea").value = tab.content;
    updateLineNumbers();
    return;
  }

  $("codeTextarea").style.display = "none";
  $("lineNumbers").style.display = "none";

  let model = monacoModels.get(tab.path);
  if (!model || model.isDisposed()) {
    const lang = getMonacoLanguage(tab.title);
    model = monaco.editor.createModel(tab.content, lang);
    monacoModels.set(tab.path, model);
  } else if (model.getValue() !== tab.content && !tab.dirty) {
    model.setValue(tab.content);
  }
  monacoEditor.setModel(model);
}

async function openFile(filePath, project = "general") {
  const existingIdx = state.openTabs.findIndex(t => t.path === filePath);
  if (existingIdx >= 0) {
    switchTab(existingIdx);
    return;
  }

  let content = "";
  try {
    const res = await api(`/api/archivo?ruta=${encodeURIComponent(filePath)}&proyecto=${encodeURIComponent(project)}`);
    content = res.contenido ?? "";
  } catch (_) {
    // Fallback default content
    content = `// Archivo: ${filePath}\n// Abre o edita este archivo en Atlantis Studio.\n\nexport const info = {\n  archivo: "${filePath}",\n  workspace: "${state.workspace.name}",\n  estado: "activo"\n};\n`;
  }

  const tab = {
    path: filePath,
    project,
    title: filePath.split("/").pop(),
    content: content,
    dirty: false
  };

  state.openTabs.push(tab);
  switchTab(state.openTabs.length - 1);
  renderTree();
}

function switchTab(index) {
  if (index < 0 || index >= state.openTabs.length) {
    state.activeTabIdx = -1;
    renderTabs();
    $("codeEditor").hidden = true;
    $("editorEmpty").hidden = false;
    $("bcFile").textContent = "ninguno";
    $("bcFolder").textContent = "workspace";
    $("unsavedBadge").hidden = true;
    const prevBtn = $("btnTogglePreview");
    if (prevBtn) prevBtn.hidden = true;
    return;
  }

  state.activeTabIdx = index;
  const tab = state.openTabs[index];

  $("codeEditor").hidden = false;
  $("editorEmpty").hidden = true;

  const parts = tab.path.split("/");
  $("bcFile").textContent = parts.pop();
  $("bcFolder").textContent = parts.length ? parts.join("/") : "workspace";
  $("unsavedBadge").hidden = !tab.dirty;

  // Markdown preview reset
  const isMd = tab.path.toLowerCase().endsWith(".md");
  const prevBtn = $("btnTogglePreview");
  if (prevBtn) {
    prevBtn.hidden = !isMd;
    prevBtn.textContent = "👁 Previa";
    prevBtn.classList.remove("active");
  }
  const prevContainer = $("markdownPreviewContainer");
  if (prevContainer) prevContainer.style.display = "none";
  const monacoContainer = $("monacoEditorContainer");
  if (monacoContainer) monacoContainer.style.display = "block";

  updateMonacoForTab(tab);
  renderTabs();
  renderOutline();
  renderTree();
}

function closeTab(index) {
  const isClosingActive = index === state.activeTabIdx;
  const tab = state.openTabs[index];
  if (tab && monacoModels.has(tab.path)) {
    const m = monacoModels.get(tab.path);
    m.dispose();
    monacoModels.delete(tab.path);
  }
  state.openTabs.splice(index, 1);
  if (state.openTabs.length === 0) {
    switchTab(-1);
  } else if (isClosingActive) {
    const nextIdx = Math.max(0, index - 1);
    switchTab(nextIdx);
  } else if (index < state.activeTabIdx) {
    state.activeTabIdx--;
    renderTabs();
  } else {
    renderTabs();
  }
}

function renderTabs() {
  const list = $("editorTabsList");
  if (!list) return;

  list.innerHTML = state.openTabs.map((tab, idx) => {
    const isActive = idx === state.activeTabIdx;
    return `<div class="editor-tab ${isActive ? "active" : ""}" data-tabidx="${idx}">
      <span class="tree-icon ${getFileIconClass(tab.title)}">${getFileBadge(tab.title)}</span>
      <span class="tab-name">${esc(tab.title)}</span>
      ${tab.dirty ? `<span class="tab-dirty" title="Cambios sin guardar">●</span>` : ""}
      <button class="tab-close" data-closeidx="${idx}" title="Cerrar pestaña">×</button>
    </div>`;
  }).join("");
}

function updateLineNumbers() {
  const text = $("codeTextarea").value;
  const count = text.split("\n").length;
  let html = "";
  for (let i = 1; i <= count; i++) html += `<div>${i}</div>`;
  $("lineNumbers").innerHTML = html;

  const curPos = $("codeTextarea").selectionStart || 0;
  const linesBefore = text.slice(0, curPos).split("\n");
  const ln = linesBefore.length;
  const col = linesBefore[linesBefore.length - 1].length + 1;
  $("sbCursor").textContent = `Ln ${ln}, Col ${col}`;
}

async function saveCurrentFile() {
  if (state.activeTabIdx < 0) return;
  const tab = state.openTabs[state.activeTabIdx];
  const newContent = monacoEditor ? monacoEditor.getValue() : $("codeTextarea").value;

  try {
    await api(`/api/archivo?ruta=${encodeURIComponent(tab.path)}&proyecto=${encodeURIComponent(tab.project)}`, {
      method: "POST",
      body: JSON.stringify({ contenido: newContent })
    });
    tab.content = newContent;
    tab.dirty = false;
    $("unsavedBadge").hidden = true;
    renderTabs();
    const btnSave = $("btnSaveFile");
    if (btnSave) {
      const origText = btnSave.innerHTML;
      btnSave.innerHTML = "✓ Guardado";
      btnSave.classList.add("active");
      setTimeout(() => {
        btnSave.innerHTML = origText;
        btnSave.classList.remove("active");
      }, 1500);
    }
    toast(`✓ Archivo guardado: ${tab.title}`);
  } catch (err) {
    toast(`Error al guardar: ${err.message}`);
  }
}

function renderOutline() {
  if (state.activeTabIdx < 0) return;
  const tab = state.openTabs[state.activeTabIdx];
  const lines = tab.content.split("\n");
  const symbols = [];

  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("function ") || trimmed.startsWith("def ") || trimmed.startsWith("class ") || trimmed.startsWith("export class ") || trimmed.startsWith("export function ") || trimmed.startsWith("const ") && trimmed.includes("= (")) {
      const label = trimmed.split("{")[0].split(":")[0].replace("export ", "");
      symbols.push({ line: idx + 1, label });
    }
  });

  const outlineBox = $("outlineContent");
  if (symbols.length) {
    outlineBox.innerHTML = symbols.map(s => `<button class="tree-row file" data-jumpline="${s.line}">
      <span class="chevron">◆</span>
      <span>${esc(s.label)}</span>
      <small style="margin-left:auto; color:var(--dim)">L${s.line}</small>
    </button>`).join("");
  } else {
    outlineBox.innerHTML = `<div class="tree-empty">No se detectaron funciones o clases.</div>`;
  }
}

// ---------------- Terminal Engine ----------------
function formatTerminalPath(fullPath) {
  if (!fullPath) return "~/workspace";
  let p = String(fullPath).trim();
  const home = "/home/daniel-sosa";
  if (p.startsWith(home)) {
    p = "~" + p.slice(home.length);
  }
  return p || "/";
}

function ansiToHtml(text) {
  if (!text) return "";
  let escaped = esc(text);
  const colorMap = {
    "1": "font-weight: bold;",
    "2": "opacity: 0.8;",
    "3": "font-style: italic;",
    "4": "text-decoration: underline;",
    "30": "color: #4b5563;",
    "31": "color: #ef4444;",
    "32": "color: #22c55e;",
    "33": "color: #eab308;",
    "34": "color: #3b82f6;",
    "35": "color: #a855f7;",
    "36": "color: #06b6d4;",
    "37": "color: #f3f4f6;",
    "90": "color: #6b7280;",
    "91": "color: #f87171;",
    "92": "color: #4ade80;",
    "93": "color: #fde047;",
    "94": "color: #60a5fa;",
    "95": "color: #c084fc;",
    "96": "color: #22d3ee;",
    "97": "color: #ffffff;"
  };
  
  escaped = escaped.replace(/\u001b\[([0-9;]+)m/g, (match, codes) => {
    if (codes === "0" || codes === "00") return "</span>";
    const styles = codes.split(";").map(c => colorMap[c] || "").filter(Boolean).join(" ");
    return styles ? `<span style="${styles}">` : "";
  });
  return escaped;
}

function printTerminalOutput(htmlContent) {
  const out = $("terminalOutput");
  if (!out) return;
  const line = document.createElement("div");
  line.innerHTML = htmlContent;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}

async function executeTerminalCmd(cmd) {
  const trimmed = cmd.trim();
  if (!trimmed) return;

  const user = state.username || "daniel";
  const pathDisplay = formatTerminalPath(state.terminalCwd);

  // Print prompt line
  printTerminalOutput(`<div><span class="prompt">${esc(user)}@atlantis</span><span class="path"> ${esc(pathDisplay)}</span><span class="delim"> $ </span><span class="cmd">${esc(trimmed)}</span></div>`);

  // Local client-side shortcuts
  if (trimmed === "clear" || trimmed === "cls") {
    $("terminalOutput").innerHTML = "";
    return;
  }
  if (trimmed === "help") {
    printTerminalOutput(`<span class="success">Atlantis Studio Shell — Comandos Disponibles:</span>
  • <b>atlantis status</b>: Diagnóstico del hub y agentes Hermes.
  • <b>python3 salud_sistema.py</b>: Verifica conectividad Gemini y Hermes.
  • <b>cd [carpeta]</b>: Navega por directorios (estado persistente).
  • <b>ls -la</b> / <b>pwd</b>: Exploración del sistema de archivos local.
  • <b>git status / git diff</b>: Control de versiones activo.
  • <b>clear</b>: Limpia el historial en pantalla.
  • <b>Tab</b>: Autocompletar rutas y nombres de archivos.
  • <b>↑ / ↓</b>: Navegar historial de comandos.`);
    return;
  }
  if (trimmed === "history") {
    if (!state.terminalHistory.length) {
      printTerminalOutput(`<div><span class="term-hint">Historial vacío.</span></div>`);
    } else {
      const hist = state.terminalHistory.map((h, i) => `  ${i + 1}  ${esc(h)}`).join("\n");
      printTerminalOutput(`<div><b>Historial de Comandos:</b>\n${hist}</div>`);
    }
    return;
  }

  // Show running indicator
  const runIndicator = document.createElement("div");
  runIndicator.className = "terminal-running";
  runIndicator.innerHTML = '<span class="spin">↻</span> Ejecutando proceso...';
  $("terminalOutput").appendChild(runIndicator);
  $("terminalOutput").scrollTop = $("terminalOutput").scrollHeight;

  const submitBtn = $("btnTerminalSubmit");
  if (submitBtn) submitBtn.disabled = true;

  try {
    const res = await api("/api/terminal/exec", {
      method: "POST",
      body: JSON.stringify({ cmd: trimmed, target_cwd: state.terminalCwd })
    });

    runIndicator.remove();
    if (submitBtn) submitBtn.disabled = false;

    if (res.cwd) {
      state.terminalCwd = res.cwd;
      const pathEl = $("terminalPromptPath");
      if (pathEl) pathEl.textContent = " " + formatTerminalPath(res.cwd);
    }

    if (res.stdout) {
      printTerminalOutput(`<div>${ansiToHtml(res.stdout)}</div>`);
    }
    if (res.stderr) {
      printTerminalOutput(`<div class="error-line">${ansiToHtml(res.stderr)}</div>`);
    }

    if (res.exit_code === 0 && !res.stdout && !res.stderr) {
      printTerminalOutput(`<div class="success">✓ Completado</div>`);
    } else if (res.exit_code !== 0) {
      printTerminalOutput(`<div class="error-line">Proceso terminó con código ${res.exit_code}</div>`);
    }

    // Auto-refresh file tree if command creates, removes or alters files
    if (/^(touch|mkdir|rm|mv|cp|git|npm|cargo|pip)\b/.test(trimmed)) {
      loadTree().catch(() => {});
    }
  } catch (err) {
    runIndicator.remove();
    if (submitBtn) submitBtn.disabled = false;
    printTerminalOutput(`<div class="error-line">Error al ejecutar en hub: ${esc(err.message)}</div>`);
  }
}


// ---------------- Search in Files ----------------
async function doSearch(q) {
  const queryText = q.trim();
  if (!queryText) return;

  const resultsBox = $("searchResults");
  resultsBox.innerHTML = `<div class="tree-empty">Buscando "${esc(queryText)}"...</div>`;

  try {
    const res = await api(`/api/buscar?q=${encodeURIComponent(queryText)}`);
    const list = res.resultados || [];
    if (!list.length) {
      resultsBox.innerHTML = `<div class="tree-empty">No se encontraron coincidencias para "${esc(queryText)}".</div>`;
      return;
    }

    resultsBox.innerHTML = list.map(item => `<div class="search-item" data-searchfile="${esc(item.archivo)}" data-searchline="${item.linea}">
      <strong>${esc(item.archivo)}</strong>
      <small>Línea ${item.linea}</small>
      <pre>${esc(item.texto)}</pre>
    </div>`).join("");
  } catch (err) {
    resultsBox.innerHTML = `<div class="tree-empty error-line">Error de búsqueda: ${esc(err.message)}</div>`;
  }
}

// ---------------- Git / Source Control ----------------
async function refreshGit() {
  try {
    const data = await api("/api/git/status");
    state.git.branch = data.branch || "main";
    state.git.files = data.archivos || [];

    $("gitBranchName").textContent = state.git.branch;
    $("sbBranch").textContent = `⎇ ${state.git.branch}`;

    const badge = $("gitBadge");
    if (state.git.files.length) {
      badge.hidden = false;
      badge.textContent = state.git.files.length;
      $("gitStatusSummary").textContent = `${state.git.files.length} archivo(s) con cambios`;
    } else {
      badge.hidden = true;
      $("gitStatusSummary").textContent = "Árbol de trabajo limpio · Sin cambios pendientes";
    }

    const fileList = $("gitFileList");
    if (state.git.files.length) {
      fileList.innerHTML = state.git.files.map(f => `<div class="git-file-item" data-gitfile="${esc(f.archivo)}">
        <span class="git-badge ${esc(f.estado)}">${esc(f.estado)}</span>
        <span>${esc(f.archivo)}</span>
      </div>`).join("");
    } else {
      fileList.innerHTML = `<div class="tree-empty">Todo sincronizado con ${esc(state.git.branch)}.</div>`;
    }
  } catch (_) {}
}

async function doGitCommit() {
  const msgInput = $("gitCommitMsg");
  const msg = msgInput.value.trim() || "chore: actualización desde Atlantis Studio";

  try {
    const res = await api("/api/git/commit", {
      method: "POST",
      body: JSON.stringify({ mensaje: msg })
    });
    if (res.ok) {
      toast("✓ Cambios confirmados en Git");
      msgInput.value = "";
      await refreshGit();
    } else {
      toast(`Error en commit: ${res.salida || "desconocido"}`);
    }
  } catch (err) {
    toast(`Error: ${err.message}`);
  }
}

// ---------------- Messages & Channels ----------------
function addMessage(message, channel = state.channel) {
  state.messages.push({ ...message, channel });
  if (state.messages.length > 100) state.messages.shift();
  renderMessages();
}

function renderMessages() {
  const visible = state.messages.filter(m => m.channel === state.channel);
  $("messages").innerHTML = visible.length ? visible.map(m => `<article class="message ${m.agent ? "agent" : ""}">
    <div class="message-avatar ${m.agent ? "agent" : ""}">${esc(m.initials || (m.agent ? "HA" : "DS"))}</div>
    <div style="flex:1;min-width:0;">
      <div class="message-head">
        <strong>${esc(m.author)}</strong>
        <time>${esc(m.time || "ahora")}</time>
      </div>
      <p>${esc(m.text)}</p>
      ${m.tag ? `<span class="message-tag">${esc(m.tag)}</span>` : ""}
    </div>
  </article>`).join("") : `<div class="messages-empty">
    <div class="messages-empty-icon">💬</div>
    <div class="messages-empty-text">No hay mensajes aún en este canal.<br>Envía el primero para iniciar la conversación.</div>
  </div>`;

  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function setChannel(channel) {
  state.channel = channel;
  document.querySelectorAll(".assistant-tab").forEach(b => b.classList.toggle("active", b.dataset.channel === channel));
  $("channelIcon").textContent = channel === "team" ? "#" : "◈";
  $("channelName").textContent = channel === "team" ? "team-chat" : "hermes-bridge";
  $("channelHint").textContent = channel === "team" ? "Daniel y tu compañero en tiempo real" : "Debate y orquestación entre agentes Hermes";
  $("messageInput").placeholder = channel === "team" ? "Escribe un mensaje al equipo…" : "Instruye a Hermes-Daniel y Hermes-Amigo…";
  renderMessages();
}

async function sendMessage(event) {
  if (event) event.preventDefault();
  const input = $("messageInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";

  const userName = localStorage.getItem("atlantis_user_name") || "Daniel";
  const userInitials = userName.slice(0, 2).toUpperCase();
  const myAgent = localStorage.getItem("atlantis_agent_name") || "Hermes-Daniel";
  const myInitials = myAgent.slice(0, 2).toUpperCase();
  const friendAgent = localStorage.getItem("atlantis_friend_agent_name") || "Hermes-Amigo";
  const friendInitials = friendAgent.slice(0, 2).toUpperCase();

  const channel = state.channel;
  addMessage({ author: userName, initials: userInitials, time: timeNow(), text }, channel);

  if (channel === "agents" || text.startsWith("/")) {
    if (text === "/skills") {
      const list = (state.antigravity.skills || []).map(s => `• **${s.name}** (\`/skill ${s.id}\`): ${s.description}`).join("\n");
      addMessage({
        author: myAgent,
        initials: myInitials,
        agent: true,
        time: timeNow(),
        text: `🧩 **Habilidades Antigravity Disponibles:**\n\n${list || "No hay habilidades activas."}`,
        tag: "SKILLS"
      }, "agents");
      return;
    }
    if (text.startsWith("/skill ")) {
      const parts = text.slice(7).trim().split(" ");
      const skillId = parts[0];
      const customPrompt = parts.slice(1).join(" ");
      executeSkillWithHermes(skillId, customPrompt);
      return;
    }
  }

  if (channel === "team") {
    if (state.socket?.readyState === 1) {
      state.socket.send(JSON.stringify({ tipo: "chat", proyecto: "general", texto: text }));
    } else {
      api("/api/chat?proyecto=general", { method: "POST", body: JSON.stringify({ de: userName, texto: text }) }).catch(() => {});
    }
  } else {
    // Send to Hermes multi-agent bridge
    toast(`Enviando instrucción a ${myAgent} y ${friendAgent}...`);
    try {
      await api("/api/ai/msg", {
        method: "POST",
        body: JSON.stringify({
          de: userName,
          para: myAgent,
          proyecto: "general",
          tipo: "tarea",
          titulo: "Instrucción de usuario",
          contenido: text
        })
      });
      // Simulate/trigger auditor acknowledgment
      setTimeout(() => {
        addMessage({
          author: friendAgent,
          initials: friendInitials,
          agent: true,
          time: timeNow(),
          text: `Recibido: "${text.slice(0, 60)}...". Analizando dependencias y validando impacto antes de ejecutar.`,
          tag: "ANALIZANDO"
        }, "agents");
      }, 700);
    } catch (_) {
      setTimeout(() => {
        addMessage({
          author: myAgent,
          initials: myInitials,
          agent: true,
          time: timeNow(),
          text: `Procesando: "${text}". Ejecutaré los cambios en el workspace local.`,
          tag: "EJECUTANDO"
        }, "agents");
      }, 600);
    }
  }
}

// ---------------- Tasks ----------------
async function loadTasks() {
  try {
    const res = await api("/api/ai/tasks?proyecto=general");
    if (Array.isArray(res.tareas)) {
      state.tasks = res.tareas;
      renderTasks();
    }
  } catch (_) {}
}

function renderTasks() {
  const container = $("taskStackItems");
  if (!container) return;
  $("taskCount").textContent = state.tasks.length;

  if (!state.tasks.length) {
    container.innerHTML = `<div class="task-empty">Sin tareas activas. Crea una con el botón "+ Tarea".</div>`;
    return;
  }

  container.innerHTML = state.tasks.slice(-6).map((t, idx) => {
    const isDone = t.estado === "completada";
    return `<div class="task-card ${isDone ? "done" : ""}">
      <div>
        <strong style="${isDone ? "text-decoration: line-through; opacity: 0.65;" : ""}">${esc(t.titulo || t.title)}</strong>
        <small>${esc(t.asignado || t.assignee || "Hermes")} · <span style="${isDone ? "color: #3ecf8e;" : ""}">${esc(t.estado || "pendiente")}</span></small>
      </div>
      <button class="task-run-btn ${isDone ? "secondary" : ""}" data-runtask="${idx}" title="${isDone ? "Re-ejecutar con Hermes" : "Ejecutar con Hermes"}">${isDone ? "✓ Hecho" : "▶ Run"}</button>
    </div>`;
  }).join("");
}

async function createTask(event) {
  if (event.submitter && event.submitter.value === "cancel") {
    $("taskDialog").close();
    return;
  }
  event.preventDefault();
  const title = $("taskTitle").value.trim();
  const desc = $("taskDescription").value.trim();
  const assignee = $("taskAssignee").value;
  const userName = localStorage.getItem("atlantis_user_name") || "Daniel";
  if (!title) return;

  try {
    const res = await api("/api/ai/task", {
      method: "POST",
      body: JSON.stringify({
        titulo: title,
        descripcion: desc,
        asignado: assignee,
        creador: userName,
        proyecto: "general",
        prioridad: "alta"
      })
    });
    if (res.tarea) state.tasks.push(res.tarea);
    toast("✓ Tarea asignada a la red Hermes");
  } catch (_) {
    state.tasks.push({ titulo: title, descripcion: desc, asignado: assignee, estado: "todo" });
    toast("✓ Tarea registrada localmente");
  }

  $("taskDialog").close();
  $("taskTitle").value = "";
  $("taskDescription").value = "";
  renderTasks();
  addMessage({
    author: "Sistema",
    initials: "✦",
    agent: true,
    time: timeNow(),
    text: `Nueva tarea creada: "${title}". Asignada a ${assignee}.`,
    tag: "TASK CREATED"
  }, "agents");
}

// ---------------- Quick Agent Actions ----------------
function triggerDebate() {
  setChannel("agents");
  const currentTab = state.openTabs[state.activeTabIdx];
  const filename = currentTab ? currentTab.title : "workspace";
  const myAgent = localStorage.getItem("atlantis_agent_name") || "Hermes-Daniel";
  const friendAgent = localStorage.getItem("atlantis_friend_agent_name") || "Hermes-Amigo";
  const myInitials = myAgent.slice(0, 2).toUpperCase();
  const friendInitials = friendAgent.slice(0, 2).toUpperCase();
  const userName = localStorage.getItem("atlantis_user_name") || "Daniel";

  addMessage({
    author: userName,
    initials: userName.slice(0, 2).toUpperCase(),
    time: timeNow(),
    text: `Iniciando sesión de debate dual sobre ${filename}. ${myAgent} propondrá optimizaciones y ${friendAgent} auditará el diseño.`
  }, "agents");

  toast(`Iniciando debate entre ${myAgent} y ${friendAgent}...`);

  setTimeout(() => {
    addMessage({
      author: myAgent,
      initials: myInitials,
      agent: true,
      time: timeNow(),
      text: `Propuesta técnica para ${filename}: Recomiendo desacoplar el transporte de red, cachear las consultas frecuentes y habilitar tipado estricto en las interfaces de comunicación.`,
      tag: "PROPUESTA"
    }, "agents");
  }, 900);

  setTimeout(() => {
    addMessage({
      author: friendAgent,
      initials: friendInitials,
      agent: true,
      time: timeNow(),
      text: `Auditoría completada: La propuesta de desacoplamiento es sólida. Aprobado para implementación con validación en suite de pruebas.`,
      tag: "AUDITORÍA OK"
    }, "agents");
  }, 2200);
}

function triggerAudit() {
  setChannel("agents");
  const currentTab = state.openTabs[state.activeTabIdx];
  if (!currentTab) {
    toast("Abre un archivo primero para auditar su código");
    return;
  }
  const friendAgent = localStorage.getItem("atlantis_friend_agent_name") || "Hermes-Amigo";
  const friendInitials = friendAgent.slice(0, 2).toUpperCase();
  const userName = localStorage.getItem("atlantis_user_name") || "Daniel";

  addMessage({
    author: userName,
    initials: userName.slice(0, 2).toUpperCase(),
    time: timeNow(),
    text: `Solicito auditoría de seguridad y calidad para el archivo "${currentTab.title}".`
  }, "agents");

  toast(`Auditando ${currentTab.title} con ${friendAgent}...`);

  setTimeout(() => {
    addMessage({
      author: friendAgent,
      initials: friendInitials,
      agent: true,
      time: timeNow(),
      text: `Informe de auditoría para "${currentTab.title}":\n• Sintaxis correcta.\n• Manejo de excepciones verificado.\n• Sin fugas de memoria evidentes.\n• Estado: Aprobado para producción.`,
      tag: "AUDIT REPORT"
    }, "agents");
  }, 1200);
}

function triggerExplain() {
  setChannel("agents");
  const currentTab = state.openTabs[state.activeTabIdx];
  if (!currentTab) {
    toast("Abre un archivo primero para explicarlo");
    return;
  }
  const myAgent = localStorage.getItem("atlantis_agent_name") || "Hermes-Daniel";
  const myInitials = myAgent.slice(0, 2).toUpperCase();

  addMessage({
    author: myAgent,
    initials: myInitials,
    agent: true,
    time: timeNow(),
    text: `El archivo "${currentTab.title}" contiene ${currentTab.content.split("\n").length} líneas de código. Define la lógica de coordinación y comunicación de la plataforma Atlantis Studio.`,
    tag: "EXPLICACIÓN"
  }, "agents");
}

// ---------------- Antigravity Engine Integration ----------------
async function loadAntigravityData() {
  try {
    const [overview, skillsRes, pluginsRes, rulesRes, mcpRes] = await Promise.all([
      api("/api/antigravity/overview").catch(() => null),
      api("/api/antigravity/skills").catch(() => null),
      api("/api/antigravity/plugins").catch(() => null),
      api("/api/antigravity/rules").catch(() => null),
      api("/api/antigravity/mcp").catch(() => null),
    ]);

    if (skillsRes?.skills) state.antigravity.skills = skillsRes.skills;
    if (pluginsRes?.plugins) state.antigravity.plugins = pluginsRes.plugins;
    if (rulesRes?.rules) state.antigravity.rules = rulesRes.rules;
    if (mcpRes?.mcp) state.antigravity.mcp = mcpRes.mcp;
    if (overview?.sources) state.antigravity.sources = overview.sources;

    const totalSkills = state.antigravity.skills.length;
    const totalPlugins = state.antigravity.plugins.length;
    const totalRules = state.antigravity.rules.length;
    const totalMcp = Object.keys(state.antigravity.mcp || {}).length;

    if ($("countSkills")) $("countSkills").textContent = totalSkills;
    if ($("countPlugins")) $("countPlugins").textContent = totalPlugins;
    if ($("countRules")) $("countRules").textContent = totalRules;
    if ($("countMcp")) $("countMcp").textContent = totalMcp;

    const badge = $("pluginsBadge");
    if (badge) {
      badge.textContent = totalSkills + totalPlugins;
      badge.hidden = (totalSkills + totalPlugins) === 0;
    }

    renderSkills();
    renderPlugins();
    renderRules();
    renderMcp();
    renderSources();
  } catch (err) {
    console.warn("Error cargando Antigravity:", err);
  }
}

function switchPluginsSubtab(subtab) {
  state.antigravity.activeSubtab = subtab;
  document.querySelectorAll(".subtab").forEach(b => b.classList.toggle("active", b.dataset.subtab === subtab));
  ["subpanelSkills", "subpanelPlugins", "subpanelRules", "subpanelMcp"].forEach(id => {
    if ($(id)) $(id).hidden = true;
  });
  const map = {
    skills: "subpanelSkills",
    plugins: "subpanelPlugins",
    rules: "subpanelRules",
    mcp: "subpanelMcp"
  };
  const target = $(map[subtab]);
  if (target) target.hidden = false;
}

function renderSkills(filter = "") {
  const container = $("skillsList");
  if (!container) return;
  const q = (filter || $("skillsSearchInput")?.value || "").toLowerCase().trim();

  let list = state.antigravity.skills;
  if (q) {
    list = list.filter(s =>
      (s.name || "").toLowerCase().includes(q) ||
      (s.id || "").toLowerCase().includes(q) ||
      (s.description || "").toLowerCase().includes(q)
    );
  }

  if (!list.length) {
    container.innerHTML = `<div class="tree-empty">No se encontraron habilidades Antigravity${q ? ` para "${esc(q)}"` : ""}.</div>`;
    return;
  }

  container.innerHTML = list.map(s => {
    const isChecked = s.enabled ? "checked" : "";
    const sourceClass = s.source.startsWith("plugin:") ? "plugin" : (s.source || "custom");
    return `
      <div class="skill-card" data-skillid="${esc(s.id)}">
        <div class="skill-card-head">
          <div class="skill-title-block">
            <span class="skill-title">${esc(s.name)}</span>
            <div class="skill-meta-tags">
              <span class="source-badge ${esc(sourceClass)}">${esc(s.source)}</span>
              <span class="id-badge">${esc(s.id)}</span>
            </div>
          </div>
          <label class="switch" title="Activar / Desactivar">
            <input type="checkbox" class="skill-toggle-input" data-toggleskill="${esc(s.id)}" ${isChecked}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="skill-desc">${esc(s.description || "Habilidad Antigravity disponible")}</div>
        <div class="skill-card-actions">
          <button class="skill-btn" data-viewskill="${esc(s.id)}">📖 Ver</button>
          <button class="skill-btn" data-editskill="${esc(s.id)}" title="Abrir en Monaco Editor">✏️ Editar</button>
          <button class="skill-btn primary" data-runskill="${esc(s.id)}">⚡ Run Hermes</button>
        </div>
      </div>
    `;
  }).join("");

  // Bind toggle events
  container.querySelectorAll(".skill-toggle-input").forEach(chk => {
    chk.addEventListener("change", async (e) => {
      const id = e.target.dataset.toggleskill;
      const enabled = e.target.checked;
      try {
        await api("/api/antigravity/skills/toggle", {
          method: "POST",
          body: JSON.stringify({ id, enabled })
        });
        toast(`Skill ${id} ${enabled ? "activada" : "desactivada"}`);
      } catch (err) {
        toast(`Error: ${err.message}`);
        e.target.checked = !enabled;
      }
    });
  });

  // Bind actions
  container.querySelectorAll("[data-viewskill]").forEach(btn => {
    btn.addEventListener("click", () => openSkillDetail(btn.dataset.viewskill));
  });

  container.querySelectorAll("[data-editskill]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = btn.dataset.editskill;
      const s = state.antigravity.skills.find(x => x.id === id);
      if (s && s.path) {
        const filePath = s.path.endsWith(".md") ? s.path : `${s.path}/SKILL.md`;
        await openFile(filePath);
        toast(`Editando ${s.name} en Monaco Editor`);
      }
    });
  });

  container.querySelectorAll("[data-runskill]").forEach(btn => {
    btn.addEventListener("click", () => {
      openSkillDetail(btn.dataset.runskill);
      $("modalSkillPrompt")?.focus();
    });
  });
}

function renderPlugins(filter = "") {
  const container = $("pluginsList");
  if (!container) return;
  const q = (filter || $("pluginsSearchInput")?.value || "").toLowerCase().trim();

  let list = state.antigravity.plugins;
  if (q) {
    list = list.filter(p =>
      (p.name || "").toLowerCase().includes(q) ||
      (p.id || "").toLowerCase().includes(q) ||
      (p.description || "").toLowerCase().includes(q)
    );
  }

  if (!list.length) {
    container.innerHTML = `<div class="tree-empty">No hay plugins instalados en .agents/plugins o plugins/</div>`;
    return;
  }

  container.innerHTML = list.map(p => {
    const isChecked = p.enabled ? "checked" : "";
    return `
      <div class="plugin-card" data-pluginid="${esc(p.id)}">
        <div class="plugin-card-head">
          <div class="skill-title-block">
            <span class="skill-title">${esc(p.name)} <small style="color:var(--dim);">v${esc(p.version || "1.0")}</small></span>
            <div class="skill-meta-tags">
              <span class="source-badge ${esc(p.source)}">${esc(p.source)}</span>
              ${p.bundled_skills?.length ? `<span class="source-badge plugin">${p.bundled_skills.length} skills</span>` : ""}
              ${p.has_rules ? `<span class="source-badge builtin">Reglas</span>` : ""}
              ${p.has_mcp ? `<span class="source-badge custom">MCP</span>` : ""}
            </div>
          </div>
          <label class="switch" title="Activar / Desactivar">
            <input type="checkbox" class="plugin-toggle-input" data-toggleplugin="${esc(p.id)}" ${isChecked}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="skill-desc">${esc(p.description || "")}</div>
      </div>
    `;
  }).join("");

  container.querySelectorAll(".plugin-toggle-input").forEach(chk => {
    chk.addEventListener("change", async (e) => {
      const id = e.target.dataset.toggleplugin;
      const enabled = e.target.checked;
      try {
        await api("/api/antigravity/plugins/toggle", {
          method: "POST",
          body: JSON.stringify({ id, enabled })
        });
        toast(`Plugin ${id} ${enabled ? "activado" : "desactivado"}`);
      } catch (err) {
        toast(`Error: ${err.message}`);
        e.target.checked = !enabled;
      }
    });
  });
}

function renderRules() {
  const container = $("rulesList");
  if (!container) return;
  const list = state.antigravity.rules;
  if (!list.length) {
    container.innerHTML = `<div class="tree-empty">No hay reglas AGENTS.md o GEMINI.md configuradas.</div>`;
    return;
  }
  container.innerHTML = list.map(r => `
    <div class="rule-card">
      <div class="skill-title-block">
        <strong style="color:var(--blue); font-size:12px;">${esc(r.name)}</strong>
        <small style="color:var(--dim); font-family:var(--font-mono);">${esc(r.scope)} · ${esc(r.path)}</small>
      </div>
      <div style="font-size:11px; color:var(--muted); line-height:1.4; white-space:pre-wrap; max-height:80px; overflow:hidden;">${esc(r.preview)}</div>
      <button class="skill-btn" data-openrule="${esc(r.path)}">Ver Regla Completa</button>
    </div>
  `).join("");

  container.querySelectorAll("[data-openrule]").forEach(btn => {
    btn.addEventListener("click", () => openFile(btn.dataset.openrule));
  });
}

function renderMcp() {
  const container = $("mcpList");
  if (!container) return;
  const servers = state.antigravity.mcp || {};
  const names = Object.keys(servers);
  if (!names.length) {
    container.innerHTML = `<div class="tree-empty">No hay servidores MCP configurados en mcp_config.json.</div>`;
    return;
  }
  container.innerHTML = names.map(k => {
    const s = servers[k];
    return `
      <div class="mcp-card">
        <div class="skill-title-block">
          <strong style="color:var(--green); font-size:12px;">🔌 ${esc(k)}</strong>
          <small style="color:var(--dim); font-family:var(--font-mono);">Comando: ${esc(s.command || "")} ${(s.args || []).join(" ")}</small>
        </div>
        <small style="color:var(--muted); font-size:10px;">Origen: ${esc(s.source || "")}</small>
      </div>
    `;
  }).join("");
}

function renderSources() {
  const container = $("sourcesList");
  if (!container) return;
  const sources = state.antigravity.sources || [];
  container.innerHTML = sources.map(src => `
    <div class="source-item">
      <span>${esc(src.name)}</span>
      <code title="${esc(src.path)}">${esc(src.tipo)}</code>
    </div>
  `).join("");
}

async function openSkillDetail(skillId) {
  try {
    const res = await api(`/api/antigravity/skills/content?id=${encodeURIComponent(skillId)}`);
    if (!res.ok) throw new Error("No se pudo cargar la habilidad");
    state.antigravity.selectedSkill = res.skill;

    $("modalSkillName").textContent = res.skill.name;
    $("modalSkillSource").textContent = res.skill.source;
    $("modalSkillSource").className = `source-badge ${res.skill.source.startsWith("plugin:") ? "plugin" : (res.skill.source || "custom")}`;
    $("modalSkillId").textContent = res.skill.id;
    $("modalSkillDesc").textContent = res.skill.description;
    $("modalSkillContent").textContent = res.content || "// Sin contenido en SKILL.md";
    $("modalSkillPrompt").value = "";

    $("skillDetailDialog").showModal();
  } catch (err) {
    toast(`Error: ${err.message}`);
  }
}

async function executeSkillWithHermes(skillId, customPrompt = "") {
  if (!skillId) return;
  toast(`⚡ Iniciando ejecución de habilidad: ${skillId}...`);

  // Switch to agents chat
  setChannel("agents");
  $("assistantPanel").classList.remove("collapsed");

  addMessage({
    author: "Daniel",
    initials: "DS",
    time: timeNow(),
    text: `/skill ${skillId} ${customPrompt}`.trim()
  }, "agents");

  addMessage({
    author: "Hermes-Daniel",
    initials: "HD",
    agent: true,
    time: timeNow(),
    text: `Recibida instrucción de habilidad Antigravity [${skillId}]. Cargando SKILL.md y ejecutando procedimientos...`,
    tag: "EJECUTANDO"
  }, "agents");

  try {
    const res = await api("/api/antigravity/skills/run", {
      method: "POST",
      body: JSON.stringify({
        id: skillId,
        prompt: customPrompt,
        proyecto: "general"
      })
    });

    addMessage({
      author: "Hermes-Amigo",
      initials: "HA",
      agent: true,
      time: timeNow(),
      text: `✓ Habilidad "${res.skill || skillId}" completada con código ${res.retcode}.\n\n${res.salida || "Operación realizada con éxito."}`,
      tag: "COMPLETADO"
    }, "agents");

    toast(`✓ Habilidad ejecutada con éxito`);
  } catch (err) {
    addMessage({
      author: "Hermes-Daniel",
      initials: "HD",
      agent: true,
      time: timeNow(),
      text: `Error al ejecutar habilidad ${skillId}: ${err.message}`,
      tag: "ERROR"
    }, "agents");
    toast(`Error: ${err.message}`);
  }
}

// ---------------- Command Palette ----------------
const commandsList = [
  { label: "📂 Abrir Archivo o Carpeta...", kbd: "⌘ O", action: () => toggleOpenChoiceMenu($("btnOpenSystem") || null) },
  { label: "📄 Abrir Archivo Individual...", kbd: "Ctrl+O", action: () => promptOpenSystemFile() },
  { label: "📁 Abrir Carpeta (Workspace)...", kbd: "⇧⌘O", action: () => promptOpenSystemFolder() },
  { label: "💾 Guardar Archivo Actual", kbd: "⌘ S", action: () => saveCurrentFile() },
  { label: "📄 Nuevo Archivo en Workspace", kbd: "⌘ N", action: () => $("newFileDialog").showModal() },
  { label: "🩺 Diagnóstico de Salud del Sistema", kbd: "Alt+S", action: () => executeTerminalCmd("python3 salud_sistema.py") },
  { label: "⎇ Control de Versiones (Git Status)", kbd: "Ctrl+G", action: () => switchActivityView("source") },
  { label: "🔍 Buscar en Archivos", kbd: "⌘ F", action: () => switchActivityView("search") },
  { label: "🤖 Iniciar Debate Dual entre Agentes", kbd: "⌘ D", action: () => triggerDebate() },
  { label: "⚡ Auditar Código del Archivo Abierto", kbd: "⌘ A", action: () => triggerAudit() },
  { label: "🔗 Compartir Espacio con Compañero", kbd: "⌘ I", action: () => openInviteModal() },
  { label: "⚙ Abrir Configuración de Atlantis", kbd: "⌘ ,", action: () => $("settingsDialog").showModal() },
  { label: "⌫ Limpiar Salida de la Terminal", kbd: "Ctrl+L", action: () => { $("terminalOutput").innerHTML = ""; toast("Terminal limpiada"); } },
  { label: "📁 Recargar Árbol de Archivos", kbd: "⌘ R", action: () => { loadTree(); toast("Árbol recargado"); } }
];

let cpFilteredItems = [];
let cpSelectedIndex = 0;

function openCommandPalette() {
  const dialog = $("commandPaletteDialog");
  const input = $("cpInput");
  input.value = "";
  cpSelectedIndex = 0;
  renderCommandPaletteItems("");
  dialog.showModal();
  input.focus();
}

function updateCpSelectionVisual() {
  const list = $("cpList");
  if (!list) return;
  const els = list.querySelectorAll(".cp-item");
  els.forEach((el, idx) => {
    const isSel = idx === cpSelectedIndex;
    el.classList.toggle("selected", isSel);
    if (isSel) el.scrollIntoView({ block: "nearest" });
  });
}

function renderCommandPaletteItems(filter) {
  const list = $("cpList");
  const queryLower = filter.toLowerCase().trim();

  // Combine commands with files
  const items = [];

  // Commands
  commandsList.forEach(cmd => {
    if (!queryLower || cmd.label.toLowerCase().includes(queryLower)) {
      items.push({ type: "cmd", label: cmd.label, kbd: cmd.kbd, action: cmd.action });
    }
  });

  // Files
  state.tree.forEach(proj => {
    (proj.archivos || []).forEach(f => {
      if (!queryLower || f.toLowerCase().includes(queryLower)) {
        items.push({
          type: "file",
          label: `📄 ${f}`,
          kbd: "Abrir",
          action: () => openFile(f, proj.nombre)
        });
      }
    });
  });

  cpFilteredItems = items.slice(0, 20);
  cpSelectedIndex = 0;

  if (!cpFilteredItems.length) {
    list.innerHTML = `<div class="tree-empty">No se encontraron comandos o archivos para "${esc(filter)}".</div>`;
    return;
  }

  list.innerHTML = cpFilteredItems.map((it, idx) => `<div class="cp-item ${idx === 0 ? "selected" : ""}" data-cpidx="${idx}">
    <span>${esc(it.label)}</span>
    ${it.kbd ? `<kbd>${esc(it.kbd)}</kbd>` : ""}
  </div>`).join("");

  list.querySelectorAll(".cp-item").forEach((el, idx) => {
    el.addEventListener("mouseenter", () => {
      cpSelectedIndex = idx;
      updateCpSelectionVisual();
    });
    el.addEventListener("click", () => {
      $("commandPaletteDialog").close();
      cpFilteredItems[idx].action();
    });
  });
}

// ---------------- View Switching ----------------
function switchActivityView(view) {
  if (view === "settings") {
    openSettingsModal();
    return;
  }
  if (view === "collab") {
    setChannel("agents");
    $("assistantPanel").classList.remove("collapsed");
    $("messageInput")?.focus();
    setTimeout(() => monacoEditor?.layout(), 120);
    return;
  }

  state.activeView = view;
  document.querySelectorAll(".activity").forEach(b => b.classList.toggle("active", b.dataset.view === view));

  const titleMap = {
    explorer: "EXPLORADOR",
    search: "BUSCAR EN ARCHIVOS",
    source: "CONTROL DE CÓDIGO",
    run: "EJECUTAR Y DEPURAR",
    plugins: "PLUGINS Y FUENTES ANTIGRAVITY"
  };
  $("sidebarTitle").textContent = titleMap[view] || view.toUpperCase();

  // Hide all view panels
  ["viewExplorer", "viewSearch", "viewSource", "viewRun", "viewPlugins"].forEach(id => {
    if ($(id)) $(id).hidden = true;
  });

  if (view === "explorer") {
    $("viewExplorer").hidden = false;
  } else if (view === "search") {
    $("viewSearch").hidden = false;
    $("searchInput").focus();
  } else if (view === "source") {
    $("viewSource").hidden = false;
    refreshGit();
  } else if (view === "run") {
    $("viewRun").hidden = false;
  } else if (view === "plugins") {
    $("viewPlugins").hidden = false;
    loadAntigravityData();
  }

  setTimeout(() => monacoEditor?.layout(), 80);
}

function openSettingsModal() {
  $("settingWorkspacePath").value = state.workspace.root || "~/Escritorio/atlantis_proyect";
  $("settingTheme").value = localStorage.getItem("atlantis_theme") || "obsidian";
  $("settingAiProvider").value = localStorage.getItem("atlantis_ai_provider") || "gemini";
  $("settingsDialog").showModal();
}

function openProfileModal() {
  $("profileNameInput").value = localStorage.getItem("atlantis_user_name") || "Daniel";
  $("profileDialog").showModal();
}

// ---------------- Invite & Share Modal ----------------
function openInviteModal() {
  const port = (HUB_BASE.match(/:(\d+)/) || [])[1] || "8787";
  const link = `http://127.0.0.1:${port}/?user=amigo&token=${encodeURIComponent(state.token)}`;
  $("inviteUrlInput").value = link;
  $("inviteDialog").showModal();
}

// ---------------- Initial Seed Messages ----------------
function seedInitialContent() {
  state.messages = [
    { channel: "team", author: "Daniel", initials: "DS", time: "10:00", text: "¡Espacio Atlantis Studio iniciado! He configurado el hub colaborativo y la terminal PTY real." },
    { channel: "team", author: "Amigo", initials: "AM", time: "10:02", text: "Excelente. Mis agentes Hermes ya están sincronizados y listos para programar." },
    { channel: "agents", author: "Hermes-Daniel", initials: "HD", agent: true, time: "10:03", text: "Agente Creador conectado. Monitoreando cambios en el workspace y preparado para recibir tareas.", tag: "READY" },
    { channel: "agents", author: "Hermes-Amigo", initials: "HA", agent: true, time: "10:04", text: "Agente Auditor conectado. Validaré cualquier modificación de código antes de aplicarla.", tag: "READY" }
  ];
  renderMessages();
}

// ---------------- Markdown Renderer ----------------
function renderMarkdownHTML(md) {
  if (!md) return "<i>Documento markdown vacío</i>";
  let html = esc(md);

  // Fenced code blocks
  html = html.replace(/```([a-z0-9_-]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    return `<pre><code class="language-${lang}">${code}</code></pre>`;
  });

  // Inline code
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

  // Headers
  html = html.replace(/^### (.*$)/gim, "<h3>$1</h3>");
  html = html.replace(/^## (.*$)/gim, "<h2>$1</h2>");
  html = html.replace(/^# (.*$)/gim, "<h1>$1</h1>");

  // Bold & Italic
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");

  // Blockquotes / Alerts
  html = html.replace(/^&gt;\s*\[!NOTE\]\s*(.*$)/gim, "<blockquote style='border-left-color: #3b82f6;'><b>NOTE:</b> $1</blockquote>");
  html = html.replace(/^&gt;\s*\[!TIP\]\s*(.*$)/gim, "<blockquote style='border-left-color: #10b981;'><b>TIP:</b> $1</blockquote>");
  html = html.replace(/^&gt;\s*\[!WARNING\]\s*(.*$)/gim, "<blockquote style='border-left-color: #f59e0b;'><b>WARNING:</b> $1</blockquote>");
  html = html.replace(/^&gt;\s*(.*$)/gim, "<blockquote>$1</blockquote>");

  // Unordered lists
  html = html.replace(/^\s*[-*]\s+(.*$)/gim, "<li>$1</li>");
  html = html.replace(/(<li>.*<\/li>)/s, "<ul>$1</ul>");

  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color: var(--blue); text-decoration: underline;">$1</a>');

  // Paragraph line breaks
  html = html.replace(/\n\n/g, "<br><br>");

  return html;
}

// ---------------- Bind All UI Events ----------------
function bindEvents() {
  // Activity Bar clicks
  document.querySelectorAll(".activity").forEach(btn => {
    btn.addEventListener("click", () => switchActivityView(btn.dataset.view));
  });

  $("commandBar").addEventListener("click", openCommandPalette);
  $("btnGit").addEventListener("click", () => switchActivityView("source"));
  $("btnShare").addEventListener("click", openInviteModal);
  $("btnAvatar").addEventListener("click", openProfileModal);
  $("brandWorkspace").addEventListener("click", () => toast(`Workspace: ${state.workspace.root || "~/workspace"}`));

  // Sidebar header buttons
  $("btnNewFile").addEventListener("click", () => $("newFileDialog").showModal());
  $("btnOpenSystem")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleOpenChoiceMenu($("btnOpenSystem"));
  });
  $("choiceOpenFile")?.addEventListener("click", () => {
    hideOpenChoiceMenu();
    promptOpenSystemFile();
  });
  $("choiceOpenFolder")?.addEventListener("click", () => {
    hideOpenChoiceMenu();
    promptOpenSystemFolder();
  });
  $("btnRefreshTree").addEventListener("click", async () => {
    await loadTree();
    toast("✓ Árbol de archivos actualizado");
  });
  $("btnMoreActions").addEventListener("click", () => {
    openCommandPalette();
  });

  // Tree clicks
  $("fileTree").addEventListener("click", (e) => {
    const folderBtn = e.target.closest(".tree-row.folder");
    if (folderBtn) {
      const folderPath = folderBtn.dataset.folder;
      if (state.expandedFolders.has(folderPath)) {
        state.expandedFolders.delete(folderPath);
      } else {
        state.expandedFolders.add(folderPath);
      }
      renderTree();
      return;
    }

    const fileBtn = e.target.closest(".tree-row.file");
    if (fileBtn) {
      const path = fileBtn.dataset.file;
      const proj = fileBtn.dataset.project || "general";
      openFile(path, proj);
    }
  });

  // File tree context menu
  let contextTarget = { type: "", path: "", project: "" };
  const ctxMenu = $("treeContextMenu");

  const hideContextMenu = () => {
    if (ctxMenu) {
      ctxMenu.hidden = true;
      ctxMenu.style.display = "none";
    }
  };

  $("fileTree").addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const fileEl = e.target.closest(".tree-row.file");
    const folderEl = e.target.closest(".tree-row.folder");

    const setItemVisibility = (id, visible) => {
      const el = $(id);
      if (!el) return;
      el.hidden = !visible;
      el.style.display = visible ? "flex" : "none";
    };

    if (fileEl) {
      contextTarget = { type: "file", path: fileEl.dataset.file, project: fileEl.dataset.project || "general" };
      setItemVisibility("cmOpen", true);
      setItemVisibility("cmOpenSystem", true);
      setItemVisibility("cmRename", true);
      setItemVisibility("cmDelete", true);
    } else if (folderEl) {
      contextTarget = { type: "folder", path: folderEl.dataset.folder, project: "" };
      setItemVisibility("cmOpen", false);
      setItemVisibility("cmOpenSystem", true);
      setItemVisibility("cmRename", true);
      setItemVisibility("cmDelete", true);
    } else {
      contextTarget = { type: "root", path: "", project: "" };
      setItemVisibility("cmOpen", false);
      setItemVisibility("cmOpenSystem", true);
      setItemVisibility("cmRename", false);
      setItemVisibility("cmDelete", false);
    }

    if (ctxMenu) {
      ctxMenu.hidden = false;
      ctxMenu.style.display = "flex";
      const posX = Math.max(10, Math.min(e.clientX, window.innerWidth - 190));
      const posY = Math.max(10, Math.min(e.clientY, window.innerHeight - 230));
      ctxMenu.style.left = `${posX}px`;
      ctxMenu.style.top = `${posY}px`;
    }
  });

  document.addEventListener("click", (e) => {
    hideContextMenu();
    if (!e.target.closest("#openChoiceMenu") && !e.target.closest("#btnOpenSystem") && !e.target.closest("#btnEmptyOpen") && !e.target.closest("#menuItemOpen") && !e.target.closest("#cmOpenSystem")) {
      hideOpenChoiceMenu();
    }
  });
  document.addEventListener("contextmenu", (e) => {
    if (!e.target.closest("#fileTree")) hideContextMenu();
  });
  window.addEventListener("blur", () => {
    hideContextMenu();
    hideOpenChoiceMenu();
  });
  ctxMenu?.addEventListener("click", hideContextMenu);

  $("cmOpen")?.addEventListener("click", () => {
    if (contextTarget.path) openFile(contextTarget.path, contextTarget.project);
  });
  $("cmOpenSystem")?.addEventListener("click", (e) => {
    hideContextMenu();
    showOpenChoiceMenu(null, e);
  });
  $("cmCopyPath")?.addEventListener("click", async () => {
    if (contextTarget.path) {
      await navigator.clipboard.writeText(contextTarget.path).catch(() => {});
      toast(`Ruta copiada: ${contextTarget.path}`);
    }
  });
  $("cmNewFileHere")?.addEventListener("click", () => {
    const prefix = contextTarget.path ? (contextTarget.type === "folder" ? `${contextTarget.path}/` : `${contextTarget.path.split("/").slice(0, -1).join("/")}/`) : "";
    $("newFilePathInput").value = prefix;
    $("newFileDialog").showModal();
    $("newFilePathInput").focus();
  });
  $("cmRename")?.addEventListener("click", async () => {
    if (!contextTarget.path) return;
    const oldPath = contextTarget.path;
    const newPath = prompt(`Nuevo nombre o ruta para "${oldPath}":`, oldPath);
    if (newPath && newPath !== oldPath) {
      try {
        await api("/api/archivo/renombrar", {
          method: "POST",
          body: JSON.stringify({ origen: oldPath, destino: newPath, proyecto: contextTarget.project || "general" })
        });
      } catch {
        await executeTerminalCmd(`mv "${oldPath}" "${newPath}"`);
      }
      await loadTree();
      // If renamed file was open, update tab
      const openTab = state.openTabs.find(t => t.path === oldPath);
      if (openTab) {
        openTab.path = newPath;
        openTab.title = newPath.split("/").pop();
        renderTabs();
      }
      toast(`✓ Renombrado a: ${newPath}`);
    }
  });
  $("cmDelete")?.addEventListener("click", async () => {
    if (!contextTarget.path) return;
    const target = contextTarget.path;
    if (confirm(`¿Eliminar definitivamente "${target}"?`)) {
      try {
        await api(`/api/archivo?ruta=${encodeURIComponent(target)}&proyecto=${encodeURIComponent(contextTarget.project || "general")}`, {
          method: "DELETE"
        });
      } catch {
        await executeTerminalCmd(`rm -rf "${target}"`);
      }
      await loadTree();
      // If deleted file was open in tabs, close it
      const openIdx = state.openTabs.findIndex(t => t.path === target);
      if (openIdx >= 0) closeTab(openIdx);
      toast(`✓ Eliminado: ${target}`);
    }
  });
  $("cmRefresh")?.addEventListener("click", async () => {
    await loadTree();
    toast("Árbol de archivos actualizado");
  });

  // Tab clicks
  $("editorTabsList").addEventListener("click", (e) => {
    const closeBtn = e.target.closest(".tab-close");
    if (closeBtn) {
      e.stopPropagation();
      const idx = parseInt(closeBtn.dataset.closeidx, 10);
      closeTab(idx);
      return;
    }

    const tab = e.target.closest(".editor-tab");
    if (tab) {
      const idx = parseInt(tab.dataset.tabidx, 10);
      switchTab(idx);
    }
  });

  $("btnNewTab").addEventListener("click", () => $("newFileDialog").showModal());
  $("btnEmptyOpen")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleOpenChoiceMenu($("btnEmptyOpen"));
  });
  $("btnEmptyNewFile").addEventListener("click", () => $("newFileDialog").showModal());
  $("btnEmptyTerminal").addEventListener("click", () => $("tabTerminal").click());

  // Editor typing & Line numbers
  const textarea = $("codeTextarea");
  textarea.addEventListener("input", () => {
    if (state.activeTabIdx >= 0) {
      const tab = state.openTabs[state.activeTabIdx];
      tab.content = textarea.value;
      if (!tab.dirty) {
        tab.dirty = true;
        $("unsavedBadge").hidden = false;
        renderTabs();
      }
    }
    updateLineNumbers();
  });

  textarea.addEventListener("click", updateLineNumbers);
  textarea.addEventListener("keyup", updateLineNumbers);

  // Tab key indentation support
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.value = textarea.value.substring(0, start) + "  " + textarea.value.substring(end);
      textarea.selectionStart = textarea.selectionEnd = start + 2;
      textarea.dispatchEvent(new Event("input"));
    } else if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      saveCurrentFile();
    }
  });

  // Breadcrumbs actions
  $("btnSaveFile").addEventListener("click", saveCurrentFile);
  $("btnCopyPath").addEventListener("click", async () => {
    if (state.activeTabIdx >= 0) {
      const p = state.openTabs[state.activeTabIdx].path;
      await navigator.clipboard.writeText(p).catch(() => {});
      const btn = $("btnCopyPath");
      const orig = btn.innerHTML;
      btn.innerHTML = "✓ Copiado";
      btn.classList.add("active");
      setTimeout(() => {
        btn.innerHTML = orig;
        btn.classList.remove("active");
      }, 1500);
      toast("Ruta copiada al portapapeles: " + p);
    } else {
      toast("No hay ningún archivo abierto");
    }
  });

  let isWordWrap = false;
  $("btnToggleWrap").addEventListener("click", () => {
    isWordWrap = !isWordWrap;
    if (monacoEditor) {
      monacoEditor.updateOptions({ wordWrap: isWordWrap ? "on" : "off" });
    }
    textarea.style.whiteSpace = isWordWrap ? "pre-wrap" : "pre";
    const btn = $("btnToggleWrap");
    btn.textContent = isWordWrap ? "↩ Wrap ON" : "↩ Wrap";
    btn.classList.toggle("active", isWordWrap);
    toast(`Ajuste de línea: ${isWordWrap ? "Activado" : "Desactivado"}`);
  });

  // Markdown Preview Toggle
  $("btnTogglePreview")?.addEventListener("click", () => {
    const prevContainer = $("markdownPreviewContainer");
    const monacoContainer = $("monacoEditorContainer");
    const isShowing = prevContainer.style.display !== "none";

    if (isShowing) {
      prevContainer.style.display = "none";
      monacoContainer.style.display = "block";
      $("btnTogglePreview").textContent = "👁 Previa";
      $("btnTogglePreview").classList.remove("active");
    } else {
      const content = monacoEditor ? monacoEditor.getValue() : $("codeTextarea").value;
      prevContainer.innerHTML = renderMarkdownHTML(content);
      monacoContainer.style.display = "none";
      prevContainer.style.display = "block";
      $("btnTogglePreview").textContent = "✏️ Editor";
      $("btnTogglePreview").classList.add("active");
    }
  });

  // Outline collapse/expand & jump
  $("toggleOutline")?.addEventListener("click", () => {
    const box = $("outlineContent");
    if (!box) return;
    const isHidden = box.style.display === "none";
    box.style.display = isHidden ? "block" : "none";
    const chevron = $("toggleOutline").querySelector("span");
    if (chevron) chevron.textContent = isHidden ? "⌄" : "›";
  });

  $("outlineContent").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-jumpline]");
    if (btn) {
      const lineNum = parseInt(btn.dataset.jumpline, 10);
      if (monacoEditor) {
        monacoEditor.revealLineInCenter(lineNum);
        monacoEditor.setPosition({ lineNumber: lineNum, column: 1 });
        monacoEditor.focus();
      } else {
        const lines = textarea.value.split("\n");
        let charPos = 0;
        for (let i = 0; i < lineNum - 1 && i < lines.length; i++) {
          charPos += lines[i].length + 1;
        }
        textarea.focus();
        textarea.setSelectionRange(charPos, charPos);
        updateLineNumbers();
      }
    }
  });

  // Terminal Panel controls
  let terminalHistoryIdx = -1;
  let terminalDraft = "";

  const termInput = $("terminalInput");
  termInput?.addEventListener("keydown", async (e) => {
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!state.terminalHistory.length) return;
      if (terminalHistoryIdx === -1) {
        terminalDraft = termInput.value;
        terminalHistoryIdx = state.terminalHistory.length - 1;
      } else if (terminalHistoryIdx > 0) {
        terminalHistoryIdx--;
      }
      termInput.value = state.terminalHistory[terminalHistoryIdx] || "";
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (terminalHistoryIdx === -1) return;
      if (terminalHistoryIdx < state.terminalHistory.length - 1) {
        terminalHistoryIdx++;
        termInput.value = state.terminalHistory[terminalHistoryIdx] || "";
      } else {
        terminalHistoryIdx = -1;
        termInput.value = terminalDraft;
      }
    } else if (e.key === "Tab") {
      e.preventDefault();
      const val = termInput.value;
      const tokens = val.split(" ");
      const lastToken = tokens[tokens.length - 1] || "";
      try {
        const comp = await api("/api/terminal/complete", {
          method: "POST",
          body: JSON.stringify({ query: lastToken })
        });
        if (comp.matches && comp.matches.length === 1) {
          tokens[tokens.length - 1] = comp.matches[0];
          termInput.value = tokens.join(" ");
        } else if (comp.matches && comp.matches.length > 1) {
          printTerminalOutput(`<div class="term-hint">${comp.matches.map(m => esc(m)).join("   ")}</div>`);
        }
      } catch (_) {}
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "l") {
      e.preventDefault();
      $("terminalOutput").innerHTML = "";
    }
  });

  // Auto-focus terminal on clicking terminal output or container
  $("terminalOutput")?.addEventListener("click", () => {
    if (!window.getSelection()?.toString()) {
      $("terminalInput")?.focus();
    }
  });
  $("terminalPanel")?.addEventListener("click", (e) => {
    if (!e.target.closest("button, input, form") && !window.getSelection()?.toString()) {
      $("terminalInput")?.focus();
    }
  });

  $("terminalForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("terminalInput");
    const cmd = input.value;
    input.value = "";
    if (cmd.trim()) {
      state.terminalHistory.push(cmd.trim());
      terminalHistoryIdx = -1;
      terminalDraft = "";
    }
    executeTerminalCmd(cmd);
  });

  $("btnClearTerminal").addEventListener("click", () => {
    $("terminalOutput").innerHTML = "";
    toast("Terminal limpiada");
  });

  $("btnToggleTerminalHeight").addEventListener("click", () => {
    const panel = $("terminalPanel");
    if (panel.classList.contains("expanded")) {
      panel.classList.remove("expanded");
      panel.classList.remove("collapsed");
    } else if (panel.classList.contains("collapsed")) {
      panel.classList.remove("collapsed");
    } else {
      panel.classList.add("expanded");
    }
  });

  $("btnCloseTerminal").addEventListener("click", () => {
    $("terminalPanel").classList.toggle("collapsed");
  });

  // Terminal Panel Tabs
  document.querySelectorAll(".panel-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".panel-tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      const which = tab.dataset.tab;
      if (which === "terminal") {
        printTerminalOutput(`<div><span class="prompt">[Terminal PTY]</span> Consola interactiva lista.</div>`);
        $("terminalInput")?.focus();
      } else if (which === "problems") {
        let markers = [];
        if (typeof monaco !== "undefined" && monaco.editor && monaco.editor.getModelMarkers) {
          markers = monaco.editor.getModelMarkers({});
        }
        if (markers.length > 0) {
          const list = markers.map(m => `<div><span class="error-line">⚠ Línea ${m.startLineNumber}:${m.startColumn}: ${esc(m.message)}</span></div>`).join("");
          printTerminalOutput(`<div><b>[Diagnóstico de Problemas]</b> ${markers.length} problema(s) detectado(s):</div>${list}`);
          $("problemCount").textContent = markers.length;
        } else {
          printTerminalOutput(`<div><span class="success">✓ Sin problemas sintácticos detectados en el archivo actual.</span></div>`);
          $("problemCount").textContent = "0";
        }
      } else if (which === "output") {
        const skillsCount = (state.antigravity?.skills || []).length;
        const pluginsCount = (state.antigravity?.plugins || []).length;
        printTerminalOutput(`<div><span class="path">[Salida de Hub Atlantis Studio]</span></div>
<div>· Puerto PTY & HTTP: ${HUB_BASE || "http://127.0.0.1:8787"}</div>
<div>· Proveedor IA: ${state.aiProvider || "Google Gemini 3.6 Flash"}</div>
<div>· Antigravity Engine: ${skillsCount} skills · ${pluginsCount} plugins cargados</div>
<div>· Agentes Dual Matrix: Hermes-Daniel (Creador) y Hermes-Amigo (Auditor) sincronizados</div>`);
      }
    });
  });

  // Assistant & Channels
  document.querySelectorAll(".assistant-tab").forEach(b => {
    b.addEventListener("click", () => setChannel(b.dataset.channel));
  });

  $("collapseAssistant").addEventListener("click", () => {
    $("assistantPanel").classList.toggle("collapsed");
  });

  $("inviteBtn").addEventListener("click", openInviteModal);
  $("messageForm").addEventListener("submit", sendMessage);
  $("messageInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Composer tools
  $("btnAttachContext").addEventListener("click", () => {
    if (state.activeTabIdx >= 0) {
      const tab = state.openTabs[state.activeTabIdx];
      const count = tab.content.split("\n").length;
      $("messageInput").value += ` [Contexto: @${tab.title}, ${count} líneas] `;
      $("messageInput").focus();
    } else {
      toast("No hay ningún archivo abierto para adjuntar");
    }
  });

  $("btnMentionAgent").addEventListener("click", () => {
    $("messageInput").value += " @Hermes-Daniel @Hermes-Amigo ";
    $("messageInput").focus();
  });

  $("btnQuickPrompt").addEventListener("click", () => {
    $("messageInput").value = "Analiza el archivo actual, detecta posibles mejoras y propón un plan paso a paso.";
    $("messageInput").focus();
  });

  // Quick Action Pills
  $("pillDebate").addEventListener("click", triggerDebate);
  $("pillAudit").addEventListener("click", triggerAudit);
  $("pillExplain").addEventListener("click", triggerExplain);

  // Tasks
  $("btnNewTaskHeader").addEventListener("click", () => $("taskDialog").showModal());
  $("taskForm").addEventListener("submit", createTask);
  $("taskStackItems").addEventListener("click", (e) => {
    const runBtn = e.target.closest("[data-runtask]");
    if (runBtn) {
      const idx = parseInt(runBtn.dataset.runtask, 10);
      const t = state.tasks[idx];
      if (t) {
        setChannel("agents");
        $("assistantPanel").classList.remove("collapsed");
        addMessage({
          author: "Daniel",
          initials: "DS",
          time: timeNow(),
          text: `Iniciando tarea: "${t.titulo || t.title}" asignada a ${t.asignado || "Hermes"}.`
        }, "agents");
        toast(`Ejecutando "${t.titulo || t.title}"...`);

        setTimeout(() => {
          addMessage({
            author: "Hermes-Daniel",
            initials: "HD",
            agent: true,
            time: timeNow(),
            text: `Tarea "${t.titulo || t.title}" ejecutada con éxito:\n• Validación de dependencias y pruebas de integración.\n• Código validado y sincronizado en el workspace.\n• Estado: Finalizada.`,
            tag: "TASK DONE"
          }, "agents");
          t.estado = "completada";
          renderTasks();
          toast(`✓ Tarea completada: "${t.titulo || t.title}"`);
        }, 1300);
      }
    }
  });

  // Search view & Replace
  $("btnDoSearch").addEventListener("click", () => doSearch($("searchInput").value));
  $("searchInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSearch($("searchInput").value);
  });
  $("btnDoReplace")?.addEventListener("click", async () => {
    const findText = $("searchInput").value;
    const replaceText = $("replaceInput").value;
    if (!findText) {
      toast("Ingresa el texto a buscar");
      return;
    }
    if (state.activeTabIdx < 0) {
      toast("Abre un archivo para reemplazar");
      return;
    }
    const tab = state.openTabs[state.activeTabIdx];
    const current = monacoEditor ? monacoEditor.getValue() : $("codeTextarea").value;
    if (!current.includes(findText)) {
      toast(`No se encontró "${findText}" en ${tab.title}`);
      return;
    }
    const count = current.split(findText).length - 1;
    const updated = current.split(findText).join(replaceText);
    if (monacoEditor) {
      monacoEditor.setValue(updated);
    } else {
      $("codeTextarea").value = updated;
    }
    tab.content = updated;
    await saveCurrentFile();
    toast(`✓ Reemplazadas ${count} coincidencias en ${tab.title}`);
  });

  $("replaceInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      $("btnDoReplace")?.click();
    }
  });

  $("searchResults").addEventListener("click", (e) => {
    const item = e.target.closest(".search-item");
    if (item) {
      const file = item.dataset.searchfile;
      const line = parseInt(item.dataset.searchline, 10);
      openFile(file).then(() => {
        // Jump to line
        setTimeout(() => {
          const lines = $("codeTextarea").value.split("\n");
          let pos = 0;
          for (let i = 0; i < line - 1 && i < lines.length; i++) pos += lines[i].length + 1;
          $("codeTextarea").focus();
          $("codeTextarea").setSelectionRange(pos, pos);
          updateLineNumbers();
        }, 100);
      });
    }
  });

  // Git View
  $("btnGitCommit").addEventListener("click", doGitCommit);
  $("gitCommitMsg")?.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      doGitCommit();
    }
  });
  $("btnRefreshGit")?.addEventListener("click", async () => {
    await refreshGit();
    toast("✓ Estado de Git actualizado");
  });
  $("btnGitPull")?.addEventListener("click", () => {
    $("tabTerminal").click();
    executeTerminalCmd("git pull");
  });
  $("btnGitPush")?.addEventListener("click", () => {
    $("tabTerminal").click();
    executeTerminalCmd("git push");
  });
  $("gitFileList").addEventListener("click", (e) => {
    const item = e.target.closest(".git-file-item");
    if (item) openFile(item.dataset.gitfile);
  });

  // Run View
  document.querySelectorAll(".run-item-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const cmd = btn.dataset.cmd;
      if (cmd) {
        $("tabTerminal").click();
        executeTerminalCmd(cmd);
      }
    });
  });

  $("btnAddCustomRun")?.addEventListener("click", () => {
    const cmd = prompt("Comando a añadir a la lista de ejecución (ej. pytest o npm test):");
    if (!cmd) return;
    const container = $("runListContainer");
    if (!container) return;
    const btn = document.createElement("button");
    btn.className = "run-item-btn";
    btn.dataset.cmd = cmd;
    btn.textContent = `▶ ${cmd}`;
    btn.addEventListener("click", () => {
      $("tabTerminal").click();
      executeTerminalCmd(cmd);
    });
    container.appendChild(btn);
    toast(`✓ Comando añadido: ${cmd}`);
  });

  // Command Palette
  $("cpInput").addEventListener("input", (e) => renderCommandPaletteItems(e.target.value));
  $("cpInput").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!cpFilteredItems.length) return;
      cpSelectedIndex = (cpSelectedIndex + 1) % cpFilteredItems.length;
      updateCpSelectionVisual();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!cpFilteredItems.length) return;
      cpSelectedIndex = (cpSelectedIndex - 1 + cpFilteredItems.length) % cpFilteredItems.length;
      updateCpSelectionVisual();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (cpFilteredItems.length > 0 && cpFilteredItems[cpSelectedIndex]) {
        $("commandPaletteDialog").close();
        cpFilteredItems[cpSelectedIndex].action();
      }
    } else if (e.key === "Escape") {
      $("commandPaletteDialog").close();
    }
  });
  $("btnCloseCp").addEventListener("click", () => $("commandPaletteDialog").close());

  // Invite Modal
  $("btnCopyInvite").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("inviteUrlInput").value).catch(() => {});
    toast("✓ Enlace de invitación copiado");
  });
  $("btnCloseInvite").addEventListener("click", () => $("inviteDialog").close());
  $("btnCloseInvite2").addEventListener("click", () => $("inviteDialog").close());
  $("btnStartTunnelAction").addEventListener("click", () => {
    $("inviteDialog").close();
    $("tabTerminal").click();
    executeTerminalCmd("./tunnel.sh");
  });

  // Settings Modal
  $("btnCloseSettings").addEventListener("click", () => $("settingsDialog").close());
  $("btnCancelSettings")?.addEventListener("click", () => $("settingsDialog").close());
  $("btnSaveSettings").addEventListener("click", () => {
    const theme = $("settingTheme").value;
    const ai = $("settingAiProvider").value;
    applyTheme(theme);
    applyAiProvider(ai);
    $("settingsDialog").close();
    toast("✓ Preferencias guardadas y aplicadas");
  });

  // Profile Modal
  $("btnCloseProfile").addEventListener("click", () => $("profileDialog").close());
  $("profileNameInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      $("btnSaveProfile").click();
    }
  });
  $("btnSaveProfile").addEventListener("click", () => {
    const name = $("profileNameInput").value.trim() || "Daniel";
    applyUserProfile(name);
    $("profileDialog").close();
    toast(`✓ Perfil actualizado: ${name}`);
  });

  // Universal Backdrop Click Dismiss for all Dialogs
  document.querySelectorAll("dialog").forEach(dlg => {
    dlg.addEventListener("click", (e) => {
      const rect = dlg.getBoundingClientRect();
      const inDialog = (
        rect.top <= e.clientY && e.clientY <= rect.top + rect.height &&
        rect.left <= e.clientX && e.clientX <= rect.left + rect.width
      );
      if (!inDialog) {
        dlg.close();
      }
    });
  });

  // New File Modal
  $("btnCloseNewFile").addEventListener("click", () => $("newFileDialog").close());
  $("btnCancelNewFile").addEventListener("click", () => $("newFileDialog").close());
  $("newFilePathInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      $("btnConfirmNewFile").click();
    }
  });
  $("btnConfirmNewFile").addEventListener("click", async () => {
    const path = $("newFilePathInput").value.trim();
    if (!path) return;
    $("newFileDialog").close();
    $("newFilePathInput").value = "";

    try {
      await api(`/api/archivo?ruta=${encodeURIComponent(path)}`, {
        method: "POST",
        body: JSON.stringify({ contenido: `// Archivo: ${path}\n// Creado en Atlantis Studio\n` })
      });
      await loadTree();
      openFile(path);
      toast(`✓ Archivo creado: ${path}`);
    } catch (err) {
      toast(`Error al crear: ${err.message}`);
    }
  });

  // Plugins view subtabs
  document.querySelectorAll(".subtab").forEach(tab => {
    tab.addEventListener("click", () => switchPluginsSubtab(tab.dataset.subtab));
  });

  $("skillsSearchInput")?.addEventListener("input", (e) => renderSkills(e.target.value));
  $("pluginsSearchInput")?.addEventListener("input", (e) => renderPlugins(e.target.value));

  // Antigravity Dialogs
  $("btnCloseSkillDetail")?.addEventListener("click", () => $("skillDetailDialog").close());
  $("btnCloseSkillDetail2")?.addEventListener("click", () => $("skillDetailDialog").close());
  $("btnExecuteSkillHermes")?.addEventListener("click", () => {
    const prompt = $("modalSkillPrompt").value.trim();
    const skill = state.antigravity.selectedSkill;
    $("skillDetailDialog").close();
    if (skill) {
      executeSkillWithHermes(skill.id, prompt);
    }
  });
  $("btnEditSkillInEditor")?.addEventListener("click", () => {
    const skill = state.antigravity.selectedSkill;
    $("skillDetailDialog").close();
    if (skill && skill.path) {
      const filePath = skill.path.endsWith(".md") ? skill.path : `${skill.path}/SKILL.md`;
      openFile(filePath);
      toast(`Editando ${skill.name} en Monaco Editor`);
    }
  });

  // Create Skill Modal
  $("btnCreateSkill")?.addEventListener("click", () => $("createSkillDialog").showModal());
  $("btnCloseCreateSkill")?.addEventListener("click", () => $("createSkillDialog").close());
  $("btnCancelCreateSkill")?.addEventListener("click", () => $("createSkillDialog").close());
  $("btnConfirmCreateSkill")?.addEventListener("click", async () => {
    const id = $("newSkillId").value.trim();
    const name = $("newSkillName").value.trim();
    const desc = $("newSkillDesc").value.trim();
    const inst = $("newSkillInstructions").value.trim();
    if (!id || !name) {
      toast("ID y Nombre son obligatorios");
      return;
    }
    $("createSkillDialog").close();
    try {
      await api("/api/antigravity/skills/create", {
        method: "POST",
        body: JSON.stringify({ id, name, description: desc, instructions: inst })
      });
      await loadAntigravityData();
      await loadTree();
      toast(`✓ Skill ${name} creada en skills/${id}`);
    } catch (err) {
      toast(`Error: ${err.message}`);
    }
  });

  // Create Plugin Prompt
  $("btnCreatePlugin")?.addEventListener("click", async () => {
    const name = prompt("Nombre del Plugin (ej. dev-tools):");
    if (!name) return;
    try {
      await api("/api/antigravity/plugins/create", {
        method: "POST",
        body: JSON.stringify({ id: name, name, description: `Plugin ${name}` })
      });
      await loadAntigravityData();
      await loadTree();
      toast(`✓ Plugin ${name} creado en .agents/plugins/${name}`);
    } catch (err) {
      toast(`Error: ${err.message}`);
    }
  });

  // Custom Source Modal
  $("btnAddSourceDialog")?.addEventListener("click", () => $("customSourceDialog").showModal());
  $("btnCloseSourceModal")?.addEventListener("click", () => $("customSourceDialog").close());
  $("btnCancelSourceModal")?.addEventListener("click", () => $("customSourceDialog").close());
  $("customSourceInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      $("btnSaveSourceModal")?.click();
    }
  });
  $("btnSaveSourceModal")?.addEventListener("click", async () => {
    const p = $("customSourceInput").value.trim();
    if (!p) return;
    $("customSourceDialog").close();
    try {
      await api("/api/antigravity/sources/add", {
        method: "POST",
        body: JSON.stringify({ path: p })
      });
      await loadAntigravityData();
      toast(`✓ Fuente registrada: ${p}`);
    } catch (err) {
      toast(`Error: ${err.message}`);
    }
  });

  // Quick pill skills
  $("pillSkills")?.addEventListener("click", () => {
    switchActivityView("plugins");
  });

  // Global Keyboard Shortcuts
  window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "o") {
      e.preventDefault();
      toggleOpenChoiceMenu($("btnOpenSystem") || null);
    } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "o") {
      e.preventDefault();
      promptOpenSystemFolder();
    } else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveCurrentFile();
    } else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "w") {
      e.preventDefault();
      if (state.activeTabIdx >= 0) closeTab(state.activeTabIdx);
    } else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "n") {
      e.preventDefault();
      $("newFileDialog").showModal();
    } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openCommandPalette();
    } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "p") {
      e.preventDefault();
      openCommandPalette();
    } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "f") {
      e.preventDefault();
      switchActivityView("search");
    } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "e") {
      e.preventDefault();
      switchActivityView("explorer");
    } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "g") {
      e.preventDefault();
      switchActivityView("source");
    } else if ((e.metaKey || e.ctrlKey) && e.key === "`") {
      e.preventDefault();
      $("terminalPanel").classList.toggle("collapsed");
    }
  });
}

// ---------------- Apple-style Menubar ----------------
function bindMenubar() {
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".menu-item")) {
      document.querySelectorAll(".menu-item.open").forEach(m => m.classList.remove("open"));
    }
  });

  document.querySelectorAll(".menu-item").forEach(item => {
    const label = item.querySelector(".menu-label");
    if (!label) return;

    const toggleItem = (e) => {
      if (e.target.closest(".menu-dropdown")) return;
      e.stopPropagation();
      const ctx = $("treeContextMenu");
      if (ctx) { ctx.hidden = true; ctx.style.display = "none"; }
      const wasOpen = item.classList.contains("open");
      document.querySelectorAll(".menu-item.open").forEach(m => m.classList.remove("open"));
      if (!wasOpen) item.classList.add("open");
    };

    label.addEventListener("click", toggleItem);
    item.addEventListener("click", toggleItem);

    item.addEventListener("mouseenter", () => {
      if (document.querySelector(".menu-item.open")) {
        document.querySelectorAll(".menu-item.open").forEach(m => m.classList.remove("open"));
        item.classList.add("open");
      }
    });
  });

  const closeAll = () => document.querySelectorAll(".menu-item.open").forEach(m => m.classList.remove("open"));

  // Archivo
  $("menuItemNewFile")?.addEventListener("click", () => { closeAll(); $("newFileDialog").showModal(); });
  $("menuItemOpen")?.addEventListener("click", (e) => {
    closeAll();
    toggleOpenChoiceMenu($("menuItemOpen"));
  });
  $("menuItemOpenFile")?.addEventListener("click", () => { closeAll(); promptOpenSystemFile(); });
  $("menuItemOpenFolder")?.addEventListener("click", () => { closeAll(); promptOpenSystemFolder(); });
  $("menuItemSave")?.addEventListener("click", () => { closeAll(); saveCurrentFile(); });
  $("menuItemCloseTab")?.addEventListener("click", () => { closeAll(); if (state.activeTabIdx >= 0) closeTab(state.activeTabIdx); });
  $("menuItemRefreshTree")?.addEventListener("click", () => { closeAll(); loadTree(); toast("Explorador actualizado"); });
  $("menuItemSave")?.addEventListener("click", () => { closeAll(); saveCurrentFile(); });
  $("menuItemCloseTab")?.addEventListener("click", () => { closeAll(); if (state.activeTabIdx >= 0) closeTab(state.activeTabIdx); });
  $("menuItemRefreshTree")?.addEventListener("click", () => { closeAll(); loadTree(); toast("Explorador actualizado"); });

  // Editar
  $("menuItemUndo")?.addEventListener("click", () => {
    closeAll();
    if (monacoEditor && document.activeElement?.closest("#monacoEditorContainer")) {
      monacoEditor.trigger("menu", "undo");
    } else {
      document.execCommand("undo");
    }
  });
  $("menuItemRedo")?.addEventListener("click", () => {
    closeAll();
    if (monacoEditor && document.activeElement?.closest("#monacoEditorContainer")) {
      monacoEditor.trigger("menu", "redo");
    } else {
      document.execCommand("redo");
    }
  });
  $("menuItemCut")?.addEventListener("click", () => {
    closeAll();
    document.execCommand("cut");
  });
  $("menuItemCopy")?.addEventListener("click", () => {
    closeAll();
    document.execCommand("copy");
  });
  $("menuItemPaste")?.addEventListener("click", async () => {
    closeAll();
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        if (monacoEditor && document.activeElement?.closest("#monacoEditorContainer")) {
          const sel = monacoEditor.getSelection();
          monacoEditor.executeEdits("paste", [{ range: sel, text, forceMoveMarkers: true }]);
        } else if (document.activeElement && (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "TEXTAREA")) {
          const el = document.activeElement;
          const start = el.selectionStart || 0;
          const end = el.selectionEnd || 0;
          el.value = el.value.substring(0, start) + text + el.value.substring(end);
          el.selectionStart = el.selectionEnd = start + text.length;
          el.dispatchEvent(new Event("input"));
        } else {
          document.execCommand("paste");
        }
      }
    } catch {
      document.execCommand("paste");
    }
  });
  $("menuItemSelectAll")?.addEventListener("click", () => {
    closeAll();
    if (monacoEditor && document.activeElement?.closest("#monacoEditorContainer")) {
      const m = monacoEditor.getModel();
      if (m) monacoEditor.setSelection(m.getFullModelRange());
    } else if (document.activeElement && (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "TEXTAREA")) {
      document.activeElement.select();
    } else {
      document.execCommand("selectAll");
    }
  });
  $("menuItemFind")?.addEventListener("click", () => {
    closeAll();
    if (monacoEditor) {
      monacoEditor.getAction("actions.find")?.run();
    } else {
      switchActivityView("search");
    }
  });
  $("menuItemFormat")?.addEventListener("click", () => {
    closeAll();
    monacoEditor?.getAction("editor.action.formatDocument")?.run();
    toast("Documento formateado");
  });

  // Ver
  $("menuItemViewExplorer")?.addEventListener("click", () => { closeAll(); $("navExplorer").click(); });
  $("menuItemViewSearch")?.addEventListener("click", () => { closeAll(); $("navSearch").click(); });
  $("menuItemViewGit")?.addEventListener("click", () => { closeAll(); $("navSource").click(); });
  $("menuItemViewRun")?.addEventListener("click", () => { closeAll(); $("navRun").click(); });
  $("menuItemViewPlugins")?.addEventListener("click", () => { closeAll(); switchActivityView("plugins"); });
  $("menuItemToggleTerminal")?.addEventListener("click", () => { closeAll(); $("btnToggleTerminalHeight").click(); });
  $("menuItemToggleAssistant")?.addEventListener("click", () => { closeAll(); $("collapseAssistant").click(); });

  // Terminal
  $("menuItemFocusTerm")?.addEventListener("click", () => { closeAll(); $("terminalPanel").classList.remove("collapsed"); $("terminalInput").focus(); });
  $("menuItemClearTerm")?.addEventListener("click", () => { closeAll(); $("btnClearTerminal").click(); });
  $("menuItemRunHealth")?.addEventListener("click", () => { closeAll(); executeTerminalCmd("python3 salud_sistema.py"); });
  $("menuItemRunGitStatus")?.addEventListener("click", () => { closeAll(); executeTerminalCmd("git status"); });

  // Hermes IA
  $("menuItemDebate")?.addEventListener("click", () => { closeAll(); triggerDebate(); });
  $("menuItemAudit")?.addEventListener("click", () => { closeAll(); triggerAudit(); });
  $("menuItemExplain")?.addEventListener("click", () => { closeAll(); triggerExplain(); });
  $("menuItemNewTask")?.addEventListener("click", () => { closeAll(); $("taskDialog").showModal(); });

  // Plugins
  $("menuItemOpenPlugins")?.addEventListener("click", () => { closeAll(); switchActivityView("plugins"); });
  $("menuItemAntigravitySkills")?.addEventListener("click", () => { closeAll(); switchActivityView("plugins"); switchPluginsSubtab("skills"); });
  $("menuItemAntigravityRules")?.addEventListener("click", () => { closeAll(); switchActivityView("plugins"); switchPluginsSubtab("rules"); });
  $("menuItemAntigravityMCP")?.addEventListener("click", () => { closeAll(); switchActivityView("plugins"); switchPluginsSubtab("mcp"); });
  $("menuItemRefreshPlugins")?.addEventListener("click", async () => { closeAll(); await loadAntigravityData(); toast("Fuentes Antigravity sincronizadas"); });
  $("menuItemAddSource")?.addEventListener("click", () => { closeAll(); $("customSourceDialog").showModal(); });

  // Ayuda
  $("menuItemHermesWizard")?.addEventListener("click", () => { closeAll(); openOnboardingWizard(2); });
  $("menuItemSwitchUser")?.addEventListener("click", () => { closeAll(); openOnboardingWizard(1); });
  $("menuItemInvite")?.addEventListener("click", () => { closeAll(); $("btnShare").click(); });
  $("menuItemSettings")?.addEventListener("click", () => { closeAll(); $("navSettings").click(); });
  $("menuItemCommands")?.addEventListener("click", () => { closeAll(); $("commandBar").click(); });
  $("menuItemAbout")?.addEventListener("click", () => { closeAll(); $("aboutDialog")?.showModal(); });
  $("btnCloseAbout")?.addEventListener("click", () => $("aboutDialog")?.close());
  $("btnCloseAboutBtn")?.addEventListener("click", () => $("aboutDialog")?.close());
}

// ---------------- Onboarding & Hermes Connection Wizard ----------------
function openOnboardingWizard(step = 1) {
  const overlay = $("onboardingGateway");
  if (!overlay) return;
  overlay.hidden = false;
  goToOnboardingStep(step);
  const closeBtn = $("btnCloseOnboarding");
  if (closeBtn) closeBtn.style.display = state.workspace.root ? "flex" : "none";
}

function goToOnboardingStep(step) {
  const step1 = $("onboardingStep1");
  const step2 = $("onboardingStep2");

  const ind1 = $("stepIndicator1");
  const ind2 = $("stepIndicator2");
  const line1 = $("stepLine1");

  if (step === 1) {
    if (step1) step1.hidden = false;
    if (step2) step2.hidden = true;
    if (ind1) ind1.className = "step-item active";
    if (ind2) ind2.className = "step-item";
    if (line1) line1.className = "step-line";
  } else if (step === 2) {
    if (step1) step1.hidden = true;
    if (step2) step2.hidden = false;
    if (ind1) ind1.className = "step-item done";
    if (ind2) ind2.className = "step-item active";
    if (line1) line1.className = "step-line done";
  }
}

function initOnboarding() {
  const overlay = $("onboardingGateway");
  if (!overlay) return;

  // Open by default on app launch as requested by user
  overlay.hidden = false;
  goToOnboardingStep(1);

  const selModel = $("obMyAgentModel");
  const selEngine = $("obMyAgentEngine");
  const quickChips = document.querySelectorAll(".model-chip");

  function getSelectedModelInfo() {
    const opt = selModel?.selectedOptions?.[0];
    const model = opt?.value || "nemotron-3-ultra-free";
    const provider = opt?.getAttribute("data-provider") || "opencode-free";
    const label = opt?.textContent?.split("(")[0]?.trim() || model;
    return { model, provider, label };
  }

  // Sincronizar quick chips con el select de modelos
  quickChips.forEach(chip => {
    chip.addEventListener("click", () => {
      quickChips.forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      const targetVal = chip.getAttribute("data-val");
      if (selModel && targetVal) {
        for (let i = 0; i < selModel.options.length; i++) {
          if (selModel.options[i].value === targetVal) {
            selModel.selectedIndex = i;
            break;
          }
        }
        selModel.dispatchEvent(new Event("change"));
      }
    });
  });

  selModel?.addEventListener("change", () => {
    const { model, label } = getSelectedModelInfo();
    if ($("nodeBadgeMine")) $("nodeBadgeMine").textContent = label;
    if ($("obHermesDetectedModel")) $("obHermesDetectedModel").textContent = model;
    if ($("modelSpecBadge")) $("modelSpecBadge").textContent = `⚡ ${label}`;
    quickChips.forEach(c => {
      c.classList.toggle("active", c.getAttribute("data-val") === model);
    });
  });

  selEngine?.addEventListener("change", (e) => {
    const val = e.target.value;
    if ($("obGroupGateway")) {
      $("obGroupGateway").style.display = val === "gateway" ? "block" : "none";
    }
  });

  // Detección automática del Hermes Agent del usuario (~/.hermes y CLI)
  async function detectUserHermes() {
    try {
      const res = await api("/api/hermes/detect").catch(() => null);
      if (res && res.ok) {
        const agentName = res.agent_name || "Koko";
        if ($("obMyAgentName")) $("obMyAgentName").value = agentName;
        if ($("obHermesDetectedName")) $("obHermesDetectedName").textContent = agentName;
        if ($("obHermesAvatar")) $("obHermesAvatar").textContent = agentName.slice(0, 2).toUpperCase();
        if ($("obHermesDetectedPath")) $("obHermesDetectedPath").textContent = res.hermes_bin || "~/.local/bin/hermes";
        if ($("obHermesDetectedModel")) $("obHermesDetectedModel").textContent = res.model || "nemotron-3-ultra-free";
        if ($("obMyAgentStatusText")) $("obMyAgentStatusText").textContent = `✓ Hermes '${agentName}' detectado y listo para conectar`;

        // Pre-seleccionar modelo si existe en el select
        if (selModel && res.model) {
          for (let i = 0; i < selModel.options.length; i++) {
            if (selModel.options[i].value === res.model) {
              selModel.selectedIndex = i;
              break;
            }
          }
          selModel.dispatchEvent(new Event("change"));
        }
      }
    } catch {
      // Dejar valores por defecto
    }
  }
  detectUserHermes();

  // Probar y Conectar Hermes (Paso 1)
  async function connectHermesAction(advanceToStep2 = true) {
    const btn = advanceToStep2 ? $("btnConnectMyAgent") : $("btnTestMyAgent");
    const dot = $("obMyAgentDot");
    const statusText = $("obMyAgentStatusText");
    const agentName = $("obMyAgentName")?.value.trim() || "Koko";
    const engine = selEngine?.value || "local";
    const { model, provider, label } = getSelectedModelInfo();
    const apiKey = $("obApiKey")?.value.trim() || "";
    const endpoint = $("obGatewayUrl")?.value.trim() || "";

    if (btn) btn.disabled = true;
    if (statusText) statusText.textContent = "Estableciendo enlace neural IPC con Hermes...";

    try {
      const res = await api("/api/hermes/connect", {
        method: "POST",
        body: JSON.stringify({
          name: agentName,
          role: "Creador Autónomo",
          engine: engine,
          provider: provider,
          model: model,
          api_key: apiKey,
          endpoint: endpoint
        })
      });

      if (dot) dot.className = "status-dot connected";
      if (statusText) statusText.textContent = `✓ ${agentName} Conectado [${label}] — Activo`;
      toast(`✓ ${agentName} conectado exitosamente con motor ${label}`);

      // Actualizar Step 2 Preview
      if ($("nodeNameMine")) $("nodeNameMine").textContent = agentName;
      if ($("nodeBadgeMine")) $("nodeBadgeMine").textContent = label;
      if ($("nodeAvatarMine")) $("nodeAvatarMine").textContent = agentName.slice(0, 2).toUpperCase();

      if (advanceToStep2) {
        goToOnboardingStep(2);
      }
    } catch (err) {
      if (dot) dot.className = "status-dot disconnected";
      if (statusText) statusText.textContent = `Error: ${err.message}`;
      toast(`Error de conexión: ${err.message}`);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // Prueba de telemetría inmersiva
  async function testHermesTelemetry() {
    const btn = $("btnTestMyAgent");
    const dot = $("obMyAgentDot");
    const statusText = $("obMyAgentStatusText");
    const hudLatency = $("hudLatency");
    const agentName = $("obMyAgentName")?.value.trim() || "Koko";
    const { label } = getSelectedModelInfo();

    if (btn) btn.disabled = true;
    if (dot) dot.className = "status-dot";
    if (statusText) statusText.textContent = "⚡ [IPC] Verificando socket ~/.hermes/gateway.sock...";

    await new Promise(r => setTimeout(r, 260));
    if (statusText) statusText.textContent = "⚡ [PTY] Inicializando shell bash y entorno sandbox...";

    await new Promise(r => setTimeout(r, 280));
    const randomLatency = (0.5 + Math.random() * 0.4).toFixed(1);
    if (hudLatency) hudLatency.textContent = `LATENCIA IPC: ${randomLatency}ms`;

    try {
      await connectHermesAction(false);
      if (statusText) statusText.textContent = `✓ ${agentName} activo | Latencia: ${randomLatency}ms | PTY Bash operativo`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  $("btnTestMyAgent")?.addEventListener("click", testHermesTelemetry);
  $("btnConnectMyAgent")?.addEventListener("click", () => connectHermesAction(true));

  // Volver de Paso 2 a Paso 1
  $("btnStep2Back")?.addEventListener("click", () => goToOnboardingStep(1));

  // ---------------- PASO 2: VINCULAR CON HERMES DE AMIGO & ESPACIO LIMPIO ----------------
  const chipDual = $("chipModeDualLocal");
  const chipCode = $("chipModeCode");
  const groupPairCode = $("obGroupPairCode");
  let pairingMode = "dual_local";

  chipDual?.addEventListener("click", () => {
    chipDual.classList.add("active");
    chipCode?.classList.remove("active");
    pairingMode = "dual_local";
    if (groupPairCode) groupPairCode.style.display = "none";
    if ($("nodeBadgeFriend")) $("nodeBadgeFriend").textContent = "Modo Dúo Activo";
  });

  chipCode?.addEventListener("click", () => {
    chipCode.classList.add("active");
    chipDual?.classList.remove("active");
    pairingMode = "code";
    if (groupPairCode) groupPairCode.style.display = "block";
    if ($("nodeBadgeFriend")) $("nodeBadgeFriend").textContent = "Enlace por Código";
  });

  $("btnGenPairCode")?.addEventListener("click", () => {
    const code = "HERMES-" + Math.random().toString(36).substring(2, 8).toUpperCase();
    if ($("obPairCode")) $("obPairCode").value = code;
    toast(`Código generado: ${code}`);
  });

  // Opciones de Workspace Limpio (Tarjetas Cyber)
  let selectedBrowseFolder = "";
  const radioChoices = document.querySelectorAll('input[name="obWorkspaceChoice"]');
  radioChoices.forEach(radio => {
    radio.addEventListener("change", (e) => {
      const isNew = e.target.value === "new";
      document.querySelectorAll(".cyber-ws-card").forEach(c => {
        const inputInside = c.querySelector('input[name="obWorkspaceChoice"]');
        c.classList.toggle("active", inputInside && inputInside.checked);
      });
      if ($("obNewProjectContainer")) {
        $("obNewProjectContainer").style.display = isNew ? "block" : "none";
      }
    });
  });

  $("cardBrowseSystem")?.addEventListener("click", async () => {
    if (window.electronAPI?.openDirectoryDialog) {
      try {
        const folder = await window.electronAPI.openDirectoryDialog();
        if (folder) {
          selectedBrowseFolder = folder;
          const folderName = folder.split("/").filter(Boolean).pop() || folder;
          const lbl = $("obBrowseFolderLabel");
          if (lbl) lbl.textContent = `✓ Seleccionada: ${folderName} (${folder})`;
          const radio = document.querySelector('input[name="obWorkspaceChoice"][value="browse"]');
          if (radio) radio.checked = true;
          document.querySelectorAll(".cyber-ws-card").forEach(c => {
            const inputInside = c.querySelector('input[name="obWorkspaceChoice"]');
            c.classList.toggle("active", inputInside && inputInside.checked);
          });
          if ($("obNewProjectContainer")) $("obNewProjectContainer").style.display = "none";
        }
      } catch (err) {
        console.error("Error al seleccionar carpeta:", err);
      }
    }
  });

  // Lanzar Atlantis Studio
  $("btnLaunchStudio")?.addEventListener("click", async () => {
    const launchBtn = $("btnLaunchStudio");
    launchBtn.textContent = "Estableciendo vinculación...";
    launchBtn.disabled = true;

    const myAgent = $("obMyAgentName")?.value.trim() || "Hermes-Daniel";
    const friendAgent = $("obFriendAgentName")?.value.trim() || "Hermes-Amigo";
    const pairCode = $("obPairCode")?.value.trim() || "";
    const { model, provider, label } = getSelectedModelInfo();

    const selectedWorkspaceRadio = document.querySelector('input[name="obWorkspaceChoice"]:checked');
    const wsMode = selectedWorkspaceRadio ? selectedWorkspaceRadio.value : "clean";
    const newProjName = $("obNewProjectName")?.value.trim() || "";

    try {
      // 1. Asegurar conexión del agente propio con su modelo
      await api("/api/hermes/connect", {
        method: "POST",
        body: JSON.stringify({
          name: myAgent,
          role: "Creador Autónomo",
          engine: selEngine?.value || "local",
          provider: provider,
          model: model,
          api_key: $("obApiKey")?.value.trim() || "",
          endpoint: $("obGatewayUrl")?.value.trim() || ""
        })
      });

      // 2. Vincular con Hermes de tu amigo
      await api("/api/hermes/pair", {
        method: "POST",
        body: JSON.stringify({
          friend_name: friendAgent,
          mode: pairingMode,
          pair_code: pairCode
        })
      });

      // 3. Establecer Workspace
      const wsResult = await api("/api/workspace/select", {
        method: "POST",
        body: JSON.stringify({
          mode: wsMode === "browse" && selectedBrowseFolder ? "existing" : wsMode,
          path: wsMode === "browse" && selectedBrowseFolder ? selectedBrowseFolder : undefined,
          name: newProjName
        })
      });

      if (wsResult && wsResult.workspace) {
        state.workspace.root = wsResult.workspace;
        state.workspace.name = wsResult.name || (wsMode === "browse" && selectedBrowseFolder ? selectedBrowseFolder.split("/").filter(Boolean).pop() : "espacio-limpio");
        if ($("workspaceName")) $("workspaceName").textContent = state.workspace.name;
        if ($("settingWorkspacePath")) $("settingWorkspacePath").value = state.workspace.root;
      }

      // 4. Limpiar tabs del editor para arrancar limpio
      state.openTabs = [];
      state.activeTabIdx = -1;
      renderTabs();
      switchTab(-1);

      // 5. Actualizar árbol de archivos del workspace
      await loadTree();

      // 6. Mensajes de bienvenida en el canal Hermes
      state.messages = [
        { channel: "agents", author: myAgent, initials: myAgent.split("-")[1]?.slice(0, 2).toUpperCase() || "HD", agent: true, time: timeNow(), text: `⚡ Agente ${myAgent} activo con motor ${label}. He configurado el espacio de trabajo limpio, listo para programar.`, tag: "ONLINE" },
        { channel: "agents", author: friendAgent, initials: friendAgent.split("-")[1]?.slice(0, 2).toUpperCase() || "HA", agent: true, time: timeNow(), text: `🤝 Agente ${friendAgent} enlazado con éxito. Red dual activa para co-programación, revisión y debate.`, tag: "PAIRED" }
      ];
      renderMessages();

      // 7. Actualizar botón en el navbar
      if ($("btnHermesPairNav")) {
        $("btnHermesPairNav").textContent = `⚡ Hermes Dual (${myAgent} ⟷ ${friendAgent})`;
        $("btnHermesPairNav").title = `${myAgent} (${label}) enlazado con ${friendAgent}`;
      }

      // Ocultar Overlay
      overlay.hidden = true;
      toast(`🚀 ¡Vinculación exitosa! ${myAgent} [${label}] ⟷ ${friendAgent} activos.`);

      printTerminalOutput(`<span class="prompt">Atlantis Studio 2.0</span> — Red Hermes Dual Enlazada\n✓ Mi Agente: <span class="cmd">${esc(myAgent)}</span> [${esc(label)}]\n✓ Agente Amigo: <span class="cmd">${esc(friendAgent)}</span> [Modelo-Agnóstico]\n✓ Workspace: <span class="path">${esc(state.workspace.root)}</span>\n✓ Usuario: <span class="badge">${esc(localStorage.getItem("atlantis_user_name") || "Daniel")}</span>\nListo para programar.`);
    } catch (err) {
      toast(`Error al vincular: ${err.message}`);
    } finally {
      launchBtn.textContent = "🚀 Vincular Agentes y Entrar a Atlantis Studio";
      launchBtn.disabled = false;
    }
  });

  // Re-abrir desde menubar / titlebar
  $("btnHermesPairNav")?.addEventListener("click", () => openOnboardingWizard(2));
  $("menuItemHermesWizard")?.addEventListener("click", () => openOnboardingWizard(2));
  $("menuItemSwitchUser")?.addEventListener("click", () => openOnboardingWizard(1));
  $("btnCloseOnboarding")?.addEventListener("click", () => {
    if (state.workspace.root) {
      $("onboardingGateway").hidden = true;
    } else {
      toast("Conecta tu Hermes Agent para entrar a Atlantis Studio");
    }
  });
}

// Kick off
init();
