// ==============================================================================
// Atlantis Studio 2.0 — Complete Collaborative IDE Runtime (build v0.6.1)
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
  hermesConnected: false,
  sharedProjects: [], // proyectos compartidos por carpeta: {proyecto, carpeta, rol, miembros}
  obTeamJoined: null, // {carpeta, code} si en onboarding se unió a un proyecto
  lastShare: null, // último código generado: {equipo_id, code, join_link, expira_en}
  preview: { activo: false, tipo: "", url: "", puerto: 0, entrada: "", carpeta: "", proyecto: "general" },
  presence: [],
  fileNotice: null,
  contextFile: null, // {title, content} — archivo adjunto al próximo mensaje del agente
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
function toast(message, opts = {}) {
  // opts: { duration, type: "ok"|"err"|"warn", retry: { label?, fn } }
  // Compat: toast("msg", 5000) y toast("msg", "err") siguen funcionando.
  if (typeof opts === "number") opts = { duration: opts };
  if (typeof opts === "string") opts = { type: opts };
  const { duration = 3000, type = "ok", retry = null } = opts;
  const node = $("toast");
  if (!node) return;
  node.className = "toast show" + (type !== "ok" ? " toast-" + type : "");
  node.innerHTML = "";
  const span = document.createElement("span");
  span.textContent = message;
  node.appendChild(span);
  if (retry && typeof retry.fn === "function") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-retry";
    btn.textContent = retry.label || "Reintentar";
    btn.addEventListener("click", () => {
      node.classList.remove("show");
      clearTimeout(toast.timer);
      retry.fn();
    });
    node.appendChild(btn);
  }
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
  if (!response.ok) {
    const err = new Error(data.error || data.detalle || `HTTP ${response.status}`);
    err.status = response.status;
    err.data = data;
    throw err;
  }
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

// ---------------- Autenticación real (/api/auth/*) ----------------
// La identidad SIEMPRE viene del usuario autenticado. Nada de "Daniel" quemado.
const Auth = {
  token: localStorage.getItem("atlantis_session_token") || "",
  user: null,
};

function currentUserName() {
  const u = Auth.user;
  return (u && (u.name || u.username)) || "";
}
function currentUserInitials() {
  const u = Auth.user;
  if (u && u.avatar) return u.avatar;
  const n = currentUserName();
  return n ? n.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase() : "–";
}

// fetch contra endpoints de auth usando el token de SESIÓN (no el token del hub)
async function authFetch(path, options = {}) {
  const joiner = path.includes("?") ? "&" : "?";
  const url = `${HUB_BASE}${path}${joiner}token=${encodeURIComponent(Auth.token)}`;
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = new Error(data.error || `HTTP ${response.status}`);
    err.status = response.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function checkSession() {
  if (!Auth.token) return null;
  try {
    const res = await authFetch("/api/auth/me");
    if (res && res.ok && res.user) {
      Auth.user = res.user;
      return res.user;
    }
  } catch (_) {}
  Auth.user = null;
  return null;
}

function saveSession(token, user) {
  Auth.token = token || "";
  Auth.user = user || null;
  if (token) localStorage.setItem("atlantis_session_token", token);
  else localStorage.removeItem("atlantis_session_token");
}

async function logout() {
  try { await authFetch("/api/auth/logout", { method: "POST" }).catch(() => null); } catch (_) {}
  saveSession("", null);
  showAuthScreen();
}

function applyUserProfile(user) {
  const u = user || Auth.user || {};
  const name = u.name || u.username || "";
  const uRole = u.role || "";
  const uEmail = u.email || "";
  const initials = u.avatar || (name ? name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase() : "–");

  const nameEl = $("profileName");
  if (nameEl) nameEl.textContent = name || "–";
  const metaEl = $("profileMeta");
  if (metaEl) metaEl.textContent = [uRole, uEmail].filter(Boolean).join(" · ") || "–";
  const unameEl = $("profileUsername");
  if (unameEl) unameEl.textContent = u.username ? "@" + u.username : "–";
  const emailEl = $("profileEmail");
  if (emailEl) emailEl.textContent = uEmail || "–";
  const bigAv = $("profileAvatarBig");
  if (bigAv) bigAv.textContent = initials;
  const avatarEl = $("btnAvatar");
  if (avatarEl) {
    avatarEl.textContent = initials;
    avatarEl.title = name ? `${name}${uRole ? " (" + uRole + ")" : ""} — ${uEmail}` : "Perfil";
  }
  const pName = $("presenceName");
  if (pName) pName.textContent = name || "–";
  const pRole = $("presenceRole");
  if (pRole) pRole.textContent = uRole ? `${uRole} · En línea` : "En línea";
  const pAv = $("presenceAvatar");
  if (pAv) pAv.textContent = initials;
}

// ---------------- Pantalla de acceso (login real) ----------------
function showAuthScreen() {
  const s = $("authScreen");
  if (s) s.hidden = false;
  document.querySelector(".app-shell")?.setAttribute("inert", "");
}

function hideAuthScreen() {
  const s = $("authScreen");
  if (s) s.hidden = true;
  document.querySelector(".app-shell")?.removeAttribute("inert");
}

function setAuthTab(which) {
  const login = which === "login";
  $("authTabLogin")?.classList.toggle("active", login);
  $("authTabRegister")?.classList.toggle("active", !login);
  if ($("authLoginForm")) $("authLoginForm").hidden = !login;
  if ($("authRegisterForm")) $("authRegisterForm").hidden = login;
  authError("login", "");
  authError("register", "");
}

function authError(which, msg) {
  const el = $(which === "login" ? "authLoginError" : "authRegError");
  if (!el) return;
  if (msg) { el.textContent = msg; el.hidden = false; }
  else { el.textContent = ""; el.hidden = true; }
}

async function initAuthScreen() {
  setAuthTab("login");
  $("authTabLogin")?.addEventListener("click", () => setAuthTab("login"));
  $("authTabRegister")?.addEventListener("click", () => setAuthTab("register"));

  // Primera ejecución (sin cuentas) -> enfatizar crear cuenta
  try {
    const st = await fetch(`${HUB_BASE}/api/auth/count`).then(r => r.json()).catch(() => null);
    if (st && st.ok && st.count === 0) {
      setAuthTab("register");
      const note = $("authFirstNote");
      if (note) note.hidden = false;
    }
  } catch (_) {}

  // Enlace de invitación: pre-rellenar el usuario sugerido en el registro
  const qUser = new URLSearchParams(location.search).get("user");
  if (qUser && qUser.trim()) {
    setAuthTab("register");
    const u = $("authRegUser");
    if (u && !u.value) u.value = qUser.trim().toLowerCase().replace(/\s+/g, "");
    const hint = $("authInviteHint");
    const qBy = new URLSearchParams(location.search).get("by");
    if (hint) {
      hint.textContent = qBy
        ? `Te invitaron como "${qUser.trim()}". Crea tu cuenta para unirte${qBy.trim() ? " (invitado por " + qBy.trim() + ")" : ""}.`
        : `Te invitaron como "${qUser.trim()}". Crea tu cuenta para unirte.`;
      hint.hidden = false;
    }
  }

  $("authLoginForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("authLoginId").value.trim();
    const pw = $("authLoginPass").value;
    const btn = $("authLoginBtn");
    authError("login", "");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`${HUB_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: id, password: pw })
      }).then(r => r.json().then(d => ({ status: r.status, data: d })).catch(() => ({ status: r.status, data: {} })));
      if (res.status === 200 && res.data.ok) {
        saveSession(res.data.token, res.data.user);
        onAuthSuccess();
      } else {
        authError("login", res.data.error || "No se pudo iniciar sesión.");
      }
    } catch (_) {
      authError("login", "No se pudo contactar al hub.");
    }
    if (btn) btn.disabled = false;
  });

  $("authRegisterForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("authRegBtn");
    authError("register", "");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`${HUB_BASE}/api/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("authRegUser").value.trim(),
          name: $("authRegName").value.trim(),
          email: $("authRegEmail").value.trim(),
          password: $("authRegPass").value,
          role: $("authRegRole").value.trim() || "Developer"
        })
      }).then(r => r.json().then(d => ({ status: r.status, data: d })).catch(() => ({ status: r.status, data: {} })));
      if (res.status === 200 && res.data.ok) {
        saveSession(res.data.token, res.data.user);
        onAuthSuccess();
      } else {
        authError("register", res.data.error || "No se pudo crear la cuenta.");
      }
    } catch (_) {
      authError("register", "No se pudo contactar al hub.");
    }
    if (btn) btn.disabled = false;
  });
}

// Llamado tras login/registro exitoso: entra al IDE con la identidad real
function onAuthSuccess() {
  hideAuthScreen();
  applyUserProfile(Auth.user);
  bootApp();
}

// Puerta de acceso: se ejecuta ANTES de init(). Devuelve true si hay sesión válida.
async function authGate() {
  await initAuthScreen();
  const user = await checkSession();
  if (user) {
    hideAuthScreen();
    applyUserProfile(user);
    return true;
  }
  showAuthScreen();
  return false;
}

// ---------------- Initialization & Connection ----------------
async function init() {
  applyTheme(localStorage.getItem("atlantis_theme") || "obsidian");
  // Puerta de acceso real: sin sesión válida no se entra al IDE.
  // Si hay sesión, bootApp() corre ya; si no, onAuthSuccess() la llama tras login/registro.
  if (await authGate()) bootApp();
}

let _booted = false;
async function bootApp() {
  if (_booted) return;
  _booted = true;

  try {
    // Enlace de invitación: ?token=<hub>&hub=<url>&by=<quien invita>
    // (el ?user= ya se usó como sugerencia de nombre en la pantalla de registro)
    const qToken = query.get("token");
    const qBy = query.get("by");
    // Banner de bienvenida: muestra quién te invitó
    if (qBy && qBy.trim()) {
      const banner = $("guestWelcomeBanner");
      const txt = $("guestWelcomeText");
      if (txt) txt.innerHTML = `<strong>${esc(qBy.trim())}</strong> te invitó a colaborar en Atlantis Studio`;
      if (banner) banner.hidden = false;
      localStorage.setItem("atlantis_invited_by", qBy.trim());
    }
    state.token = localStorage.getItem("atlantis_token") || "";
    if (qToken && qToken.trim()) {
      state.token = qToken.trim();
    }
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
  await refreshSharedProjects(); // proyectos compartidos por carpeta (presencia por proyecto)
  await refreshGit();
  await loadTasks();
  await loadAntigravityData();
  renderMessages();
  initMonaco();
  bindEvents();
  bindMenubar();
  initOnboarding();
  setupFallbackFileInputs();
  setupElectronIpc();
  switchTab(-1);
  refreshProjectRunBox().catch(() => {});
  await loadSlotSwitcher();
  await syncHermesLive();

  // Print welcome to terminal
  printTerminalOutput(`<span class="prompt">Atlantis Studio 2.0</span> — Entorno de Desarrollo y Red de Agentes Hermes\n✓ Workspace: <span class="path">${esc(state.workspace.root || "~/workspace")}</span>\n✓ Hub conectado: <span class="cmd">${esc(HUB_BASE || "http://127.0.0.1:8787")}</span>\nEscribe cualquier comando abajo o presiona <b>⌘ K</b> para buscar.`);
}

function setupSocket() {
  if (!HUB_BASE) return;
  const socketOrigin = HUB_BASE.replace(/^http/, "ws");
  const wsName = currentUserName();
  try {
    state.socket = new WebSocket(`${socketOrigin}/ws?token=${encodeURIComponent(state.token)}&nombre=${encodeURIComponent(wsName)}`);
    state.socket.onopen = () => {
      state.connected = true;
      state.socket.send(JSON.stringify({ tipo: "join", proyecto: "general" }));
      sendPresence();
    };
    state.socket.onmessage = (event) => handleSocketEvent(JSON.parse(event.data));
    state.socket.onclose = () => setTimeout(setupSocket, 4000);
  } catch (_) {}
}

function handleSocketEvent(event) {
  if (event.tipo === "chat" && event.de !== currentUserName()) {
    addMessage({ author: event.de || "Miembro del equipo", initials: "AM", time: event.fecha || timeNow(), text: event.texto, agente: event.agente || null }, "team");
  } else if (event.tipo === "ai_msg") {
    const m = event.mensaje || {};
    if (m.id && state.agentSeen.has(m.id)) return;
    if (m.id) state.agentSeen.add(m.id);
    // Quitar indicadores "pensando" al llegar la respuesta real
    for (let i = state.messages.length - 1; i >= 0; i--) {
      const mm = state.messages[i];
      if (mm.channel === "agents" && typeof mm.id === "string" && mm.id.startsWith("typing-")) {
        state.messages.splice(i, 1);
      }
    }
    addMessage({
      author: m.de || "Hermes",
      initials: String(m.de || "HA").slice(0, 2).toUpperCase(),
      agent: true,
      time: m.fecha || timeNow(),
      text: m.contenido || "",
      tag: (m.tipo || "HERMES").toUpperCase(),
      agente: m.agente || null
    }, "agents");
  } else if (event.tipo === "arbol") {
    state.tree = event.arbol || [];
    renderTree();
  } else if (event.tipo === "ai_task_update") {
    loadTasks();
  } else if (event.tipo === "agent_msg") {
    const m = event.mensaje || {};
    if (m.id && state.agentSeen.has(m.id)) return;
    if (m.id) state.agentSeen.add(m.id);
    addMessage({
      id: m.id,
      src: "bus",
      author: m.de || "Hermes",
      initials: String(m.de || "HA").slice(0, 2).toUpperCase(),
      agent: true,
      time: m.fecha || timeNow(),
      text: m.texto || "",
      tag: String(m.tipo || "mensaje").toUpperCase(),
      agente: m.agente || null
    }, "agents");
  } else if (event.tipo === "aprobacion_update") {
    handleAprobacionUpdate(event);
  } else if (event.tipo === "presence_update") {
    renderPresence(event.users || []);
  } else if (event.tipo === "sync_equipo_update") {
    // Un miembro entró/salió de un equipo de proyecto: refrescar la
    // lista de proyectos compartidos (y la presencia por proyecto).
    refreshSharedProjects().then(() => {
      renderSharedProjectsList();
      renderPresence(state.presence || []);
      refreshShareDialogMembers();
      refreshShareApprovals();
    });
  } else if (event.tipo === "file_changed") {
    handleFileChanged(event);
  } else if (event.tipo === "sync_unsupported") {
    // El sync entre servidores no pudo traer este archivo (binario,
    // demasiado grande o sin respuesta): no se toca nada local.
    toast(`⚠️ No se pudo sincronizar ${event.ruta || ""} (${event.motivo || "no soportado"})`);
  } else if (event.tipo === "sync_big_progreso") {
    // Progreso en vivo de transferencias de archivos grandes (file_big).
    const mb = event.total_bytes ? (event.total_bytes / 1048576).toFixed(1) + " MB" : "";
    const donde = event.direccion === "subida" ? "Enviando" : "Recibiendo";
    if (event.estado === "en_cola" || event.estado === "recibiendo" || event.estado === "verificando") {
      toast(`⏳ ${donde} ${event.ruta || ""} (${mb})…`);
    } else if (event.estado === "escrito") {
      toast(`✅ ${event.ruta || ""} sincronizado (${mb})${event.motivo ? " — " + event.motivo : ""}`);
    } else if (event.estado === "error") {
      toast(`⚠️ No se pudo sincronizar ${event.ruta || ""} (${event.motivo || "error"})`);
    }
    if (typeof updateActiveProjectSync === "function") updateActiveProjectSync();
  }
}

// ---------------- Presencia colaborativa ----------------
let lastPresenceSent = 0;

function currentCursorPos() {
  try {
    if (monacoEditor && typeof monacoEditor.getPosition === "function") {
      const p = monacoEditor.getPosition();
      if (p) return { line: p.lineNumber, col: p.column };
    }
  } catch (_) {}
  return null;
}

function sendPresence() {
  if (!state.socket || state.socket.readyState !== 1) return;
  const tab = state.openTabs[state.activeTabIdx];
  const cursor = tab ? currentCursorPos() : null;
  let selection = null;
  try {
    if (monacoEditor && typeof monacoEditor.getSelection === "function") {
      const s = monacoEditor.getSelection();
      if (s) selection = { startLineNumber: s.startLineNumber, startColumn: s.startColumn, endLineNumber: s.endLineNumber, endColumn: s.endColumn };
    }
  } catch (_) {}
  try {
    state.socket.send(JSON.stringify({
      tipo: "presence",
      file: tab ? tab.path : "",
      cursor: cursor || {},
      selection
    }));
  } catch (_) {}
}

function sendPresenceThrottled() {
  const now = Date.now();
  if (now - lastPresenceSent < 1000) return;
  lastPresenceSent = now;
  sendPresence();
}

function renderPresence(users) {
  state.presence = Array.isArray(users) ? users : [];
  const bar = $("presenceBar");
  const chips = $("presenceChips");
  if (!bar || !chips) return;

  const myName = currentUserName();
  let others = state.presence.filter(u => u && u.nombre && u.nombre !== myName);
  // Presencia por proyecto: si el archivo abierto está en un proyecto
  // compartido, mostrar solo a los colaboradores DE ESE proyecto.
  const tabActiva = state.openTabs[state.activeTabIdx];
  const compartido = tabActiva ? sharedProjectForPath(tabActiva.path, tabActiva.project) : null;
  if (compartido && (compartido.miembros || []).length) {
    const nombresEquipo = new Set((compartido.miembros || []).map(m => String(m.user_name || m.agent_name || "")));
    others = others.filter(u => nombresEquipo.has(String(u.nombre)));
  }

  if (!others.length) {
    bar.hidden = true;
  } else {
    bar.hidden = false;
    chips.innerHTML = others.map(u => {
      const line = u.cursor && u.cursor.line ? `:${u.cursor.line}` : "";
      const file = u.file ? String(u.file).split("/").pop() : "sin archivo";
      return `<span class="presence-chip"><span class="presence-dot-inline" style="background:${esc(u.color || "#22d3ee")};color:${esc(u.color || "#22d3ee")}"></span><strong>${esc(u.nombre)}</strong><span class="presence-file">${esc(file)}${esc(line)}</span></span>`;
    }).join("");
  }
  renderTree(); // marca los archivos que ve cada colaborador
  renderLiveCursors();
  updateActiveProjectSync();
}

function presenceDotFor(filePath) {
  const users = (state.presence || []).filter(u => {
    if (!u || !u.file) return false;
    const f = String(u.file);
    return f === filePath || f.endsWith("/" + filePath) || filePath.endsWith("/" + f);
  });
  if (!users.length) return "";
  const title = users.map(u => u.nombre).join(", ");
  return `<span class="tree-presence" title="${esc(title)} viendo este archivo">${users.map(u => `<span class="tree-presence-dot" style="background:${esc(u.color || "#22d3ee")};color:${esc(u.color || "#22d3ee")}"></span>`).join("")}</span>`;
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
    // Los archivos de la raíz del workspace se leen con el alcance especial __raiz__,
    // no con el proyecto colaborativo "general".
    const projKey = proj.es_raiz ? "__raiz__" : projName;
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
            html += `<button class="tree-row file ${isSelected ? "selected" : ""}" data-file="${esc(f.full)}" data-project="${esc(projKey)}" style="padding-left: 36px;">
              <span class="tree-icon ${getFileIconClass(f.name)}">${getFileBadge(f.name)}</span>
              <span>${esc(f.name)}${presenceDotFor(f.full)}</span>
            </button>`;
          });
        }
      });

      // Files at root
      rootFiles.forEach(f => {
        const isSelected = state.openTabs[state.activeTabIdx]?.path === f.full;
        html += `<button class="tree-row file ${isSelected ? "selected" : ""}" data-file="${esc(f.full)}" data-project="${esc(projKey)}" style="padding-left: 24px;">
          <span class="tree-icon ${getFileIconClass(f.name)}">${getFileBadge(f.name)}</span>
          <span>${esc(f.name)}${presenceDotFor(f.full)}</span>
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
// Cambios programáticos al editor (sync remota, cambio de tab): no marcan dirty.
function setEditorValueSilencioso(model, valor) {
  state.suppressDirty = true;
  try {
    model.setValue(valor);
  } finally {
    state.suppressDirty = false;
  }
}

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
        if (state.suppressDirty) return; // cambio programático, no del usuario
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
        sendPresenceThrottled();
      });

      monacoEditor.onDidChangeCursorSelection(() => {
        sendPresenceThrottled();
        renderLiveCursors();
      });
      monacoEditor.onDidScrollChange(() => renderLiveCursors());

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
    setEditorValueSilencioso(model, tab.content);
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
  let version = 0;
  try {
    const res = await api(`/api/archivo?ruta=${encodeURIComponent(filePath)}&proyecto=${encodeURIComponent(project)}`);
    content = res.contenido ?? "";
    version = typeof res.version === "number" ? res.version : 0;
  } catch (_) {
    // Sin contenido inventado: si el archivo no se pudo leer, se dice y no se abre.
    toast(`No se pudo abrir "${filePath}".`);
    return;
  }

  const tab = {
    path: filePath,
    project,
    title: filePath.split("/").pop(),
    content: content,
    version: version,
    dirty: false
  };

  state.openTabs.push(tab);
  switchTab(state.openTabs.length - 1);
  renderTree();
  sendPresence();
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
    renderLiveCursors();
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
    const lbl = prevBtn.querySelector("span");
    if (lbl) lbl.textContent = "Vista previa";
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
  sendPresence();
  renderFileNoticeBar();
  renderLiveCursors();
  updateActiveProjectSync();
}

function closeTab(index) {
  const isClosingActive = index === state.activeTabIdx;
  const tab = state.openTabs[index];
  if (tab && monacoModels.has(tab.path)) {
    const m = monacoModels.get(tab.path);
    m.dispose();
    monacoModels.delete(tab.path);
  }
  if (tab && state.fileNotice && state.fileNotice.tab === tab) hideFileNoticeBar();
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

async function saveCurrentFile(forceLatestVersion) {
  if (state.activeTabIdx < 0) return;
  const tab = state.openTabs[state.activeTabIdx];
  const newContent = monacoEditor ? monacoEditor.getValue() : $("codeTextarea").value;

  const payload = { contenido: newContent };
  if (typeof forceLatestVersion === "number") {
    payload.expected_version = forceLatestVersion;
  } else if (typeof tab.version === "number") {
    payload.expected_version = tab.version;
  }

  try {
    const res = await api(`/api/archivo?ruta=${encodeURIComponent(tab.path)}&proyecto=${encodeURIComponent(tab.project)}`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
    tab.content = newContent;
    if (typeof res.version === "number") tab.version = res.version;
    tab.dirty = false;
    if (state.fileNotice && state.fileNotice.tab === tab) hideFileNoticeBar();
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
    if (err && err.status === 409) {
      // Conflicto de versión: alguien guardó este archivo mientras se editaba
      const latest = (err.data && typeof err.data.version === "number") ? err.data.version : (tab.version ?? 0);
      const autor = (err.data && err.data.autor) ? err.data.autor : null;
      showFileConflictBar(tab, latest, autor);
      return;
    }
    toast(`Error al guardar: ${err.message}`);
  }
}

// ---------------- Sincronización colaborativa de archivos ----------------
function showFileConflictBar(tab, latestVersion, autor) {
  state.fileNotice = { type: "conflict", tab, latestVersion, autor: autor || "Un colaborador" };
  renderFileNoticeBar();
}

function showFileRemoteNotice(tab, ev) {
  state.fileNotice = { type: "remote", tab, version: ev.version, autor: ev.autor || "Un colaborador" };
  renderFileNoticeBar();
}

function hideFileNoticeBar() {
  state.fileNotice = null;
  const bar = $("fileNoticeBar");
  if (bar) bar.hidden = true;
}

function renderFileNoticeBar() {
  const bar = $("fileNoticeBar");
  const text = $("fileNoticeText");
  const actions = $("fileNoticeActions");
  const notice = state.fileNotice;
  if (!bar || !text || !actions) return;

  const activeTab = state.openTabs[state.activeTabIdx];
  if (!notice || notice.tab !== activeTab) {
    bar.hidden = true;
    return;
  }

  bar.hidden = false;
  actions.innerHTML = "";
  const mkBtn = (label, primary, onClick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    if (primary) b.className = "primary";
    b.addEventListener("click", onClick);
    actions.appendChild(b);
    return b;
  };

  if (notice.type === "conflicto_resolver") {
    bar.className = "file-notice-bar conflict";
    text.textContent = `⚠ Conflicto en ${notice.ruta}: se mantuvo tu versión. La otra copia está en ${notice.conflicto_archivo || "un backup .conflict."}`;
    mkBtn("Resolver conflicto", true, () => openConflictDialogForFile(notice.ruta, notice.conflicto_archivo, notice.proyecto));
    mkBtn("Ignorar", false, () => hideFileNoticeBar());
  } else if (notice.type === "conflict") {
    bar.className = "file-notice-bar conflict";
    text.textContent = `⚠ ${notice.autor} guardó una versión más nueva de ${notice.tab.title} (v${notice.latestVersion}). Tus cambios no se han perdido.`;
    mkBtn(`Usar la de ${notice.autor}`, false, () => reloadTabFromServer(notice.tab));
    mkBtn("Usar la mía", true, () => saveCurrentFile(notice.latestVersion));
  } else {
    bar.className = "file-notice-bar notice";
    text.textContent = `✎ ${notice.autor} actualizó ${notice.tab.title} mientras lo editabas. Tus cambios sin guardar están intactos.`;
    mkBtn("Recargar", true, () => reloadTabFromServer(notice.tab));
    mkBtn("Ignorar", false, () => hideFileNoticeBar());
  }
}

async function reloadTabFromServer(tab) {
  try {
    const res = await api(`/api/archivo?ruta=${encodeURIComponent(tab.path)}&proyecto=${encodeURIComponent(tab.project)}`);
    tab.content = res.contenido ?? "";
    if (typeof res.version === "number") tab.version = res.version;
    tab.dirty = false;
    const model = monacoModels.get(tab.path);
    if (model && !model.isDisposed()) setEditorValueSilencioso(model, tab.content);
    if (state.openTabs[state.activeTabIdx] === tab) {
      updateMonacoForTab(tab);
      $("unsavedBadge").hidden = true;
    }
    hideFileNoticeBar();
    renderTabs();
    toast(`✓ Recargado desde el servidor: ${tab.title}`);
  } catch (err) {
    toast(`No se pudo recargar: ${err.message}`);
  }
}

function findTabForFileChanged(ev, path) {
  // La misma ruta puede existir en varios espacios (general, __raiz__…):
  // el evento de sync trae proyecto; usarlo cuando la pestaña lo tenga.
  return state.openTabs.find(t =>
    t.path === (path || ev.ruta) &&
    (!ev.proyecto || !t.project || t.project === ev.proyecto));
}

function handleFileChanged(ev) {
  if (!ev || !ev.ruta) return;
  maybeReloadPreview(ev);

  // Conflicto entre servidores: jamás sobrescribir. El contenido local
  // queda intacto y la otra copia está en ev.conflicto_archivo.
  // La barra del archivo ofrece "Resolver conflicto" (diálogo con diff).
  if (ev.conflicto) {
    toast(`⚠️ Conflicto en ${ev.ruta}: se mantuvo tu versión. La otra copia está en ${ev.conflicto_archivo || "un backup .conflict."}`);
    const tabC = findTabForFileChanged(ev);
    if (tabC) {
      state.fileNotice = { type: "conflicto_resolver", tab: tabC, ruta: ev.ruta, conflicto_archivo: ev.conflicto_archivo || "", proyecto: ev.proyecto || tabC.project || "general", autor: ev.autor || "Un colaborador" };
      renderFileNoticeBar();
    }
    return;
  }

  // Un peer eliminó el archivo: cerrar la pestaña si estaba abierta.
  if (ev.operacion === "delete") {
    const idx = state.openTabs.findIndex(t => t.path === ev.ruta && (!ev.proyecto || !t.project || t.project === ev.proyecto));
    if (idx >= 0) closeTab(idx);
    toast(`🗑 ${ev.autor || "Un colaborador"} eliminó ${ev.ruta}`);
    return;
  }

  // Un peer renombró el archivo: seguir la pestaña a su nuevo nombre.
  if (ev.operacion === "rename" && ev.origen) {
    const tabR = findTabForFileChanged(ev, ev.origen);
    if (tabR) {
      tabR.path = ev.ruta;
      tabR.title = ev.ruta.split("/").pop();
      if (typeof ev.version === "number") tabR.version = ev.version;
      renderTabs();
      toast(`✏️ ${ev.autor || "Un colaborador"} renombró ${ev.origen} → ${ev.ruta}`);
    }
    return;
  }

  const tab = findTabForFileChanged(ev);
  if (!tab) return; // no está abierto: el árbol ya se actualiza vía 'arbol'
  if (typeof ev.version === "number" && typeof tab.version === "number" && ev.version <= tab.version) {
    return; // mi propio guardado o cambio ya aplicado
  }
  if (tab.dirty) {
    // Nunca perder trabajo sin guardar: aviso discreto con opción de recargar
    showFileRemoteNotice(tab, ev);
    toast(`✎ ${ev.autor || "Un colaborador"} actualizó ${ev.ruta}`);
    return;
  }
  applyRemoteContent(tab, ev);
}

async function applyRemoteContent(tab, ev) {
  let contenido = ev.contenido;
  if (contenido == null) {
    try {
      const res = await api(`/api/archivo?ruta=${encodeURIComponent(tab.path)}&proyecto=${encodeURIComponent(tab.project)}`);
      contenido = res.contenido ?? "";
    } catch {
      return;
    }
  }
  tab.content = contenido;
  if (typeof ev.version === "number") tab.version = ev.version;
  tab.dirty = false;
  if (state.fileNotice && state.fileNotice.tab === tab) hideFileNoticeBar();
  if (state.openTabs[state.activeTabIdx] === tab) {
    const model = monacoModels.get(tab.path);
    if (model && !model.isDisposed()) setEditorValueSilencioso(model, contenido);
    updateMonacoForTab(tab);
    $("unsavedBadge").hidden = true;
  }
  renderTabs();
  toast(`✎ ${ev.autor || "Un colaborador"} actualizó ${ev.ruta}`);
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
  // Abrevia el home del usuario actual (/home/<usuario> o /Users/<usuario>) como ~
  p = p.replace(/^\/(home|Users)\/[^/]+/, "~");
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
    "34": "color: #8b7cf6;",
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

async function executeTerminalCmd(cmd, targetCwd) {
  const trimmed = cmd.trim();
  if (!trimmed) return;

  const cwdObjetivo = targetCwd || state.terminalCwd;

  const user = (Auth.user && Auth.user.username) || "usuario";
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
  • <b>python3 salud_sistema.py</b>: Verifica el estado del hub y tu Hermes local.
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
      body: JSON.stringify({ cmd: trimmed, target_cwd: cwdObjetivo })
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
async function runHubRootCmd(cmd) {
  // Los scripts del hub (tunnel.sh, salud_sistema.py) viven en la raíz del proyecto:
  // apuntar el cwd ahí para que el comando funcione desde cualquier carpeta.
  try {
    const st = await api("/api/ai/status").catch(() => null);
    if (st && st.workspace) state.terminalCwd = st.workspace;
  } catch (_) {}
  executeTerminalCmd(cmd);
}
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
  if (!message.fullTime) {
    message.fullTime = new Date().toLocaleString("es", {
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"
    });
  }
  state.messages.push({ ...message, channel });
  if (state.messages.length > 100) state.messages.shift();
  renderMessages();
  // Entrada sutil solo para el mensaje nuevo (no se re-dispara en re-renders).
  const box = $("messages");
  const last = box.lastElementChild;
  if (last && last.classList && last.classList.contains("message")) {
    last.classList.add("fresh");
  }
}

function isNearBottom(box, pad = 90) {
  if (!box) return true;
  return box.scrollHeight - box.scrollTop - box.clientHeight < pad;
}

function getMarkdownRenderer() {
  if (typeof window !== "undefined" && typeof window.renderMarkdown === "function") {
    return window.renderMarkdown;
  }
  if (typeof renderMarkdown === "function") {
    return renderMarkdown;
  }
  if (typeof renderMarkdownHTML === "function") {
    return renderMarkdownHTML;
  }
  return (src) => `<p>${esc(src || "").replace(/\n/g, "<br>")}</p>`;
}

function thinkingHtml(m) {
  return `<div class="thinking-row"><span class="thinking-dots"><i></i><i></i><i></i></span>` +
    `<span class="thinking-text">${esc(m.author)} · ${esc(m.phase || "Pensando…")}</span></div>`;
}

function messageHtml(m) {
  const mdRender = getMarkdownRenderer();
  const body = m.thinking
    ? thinkingHtml(m)
    : m.agent
      ? mdRender(m.text || "")
      : `<p>${esc(m.text || "").replace(/\n/g, "<br>")}</p>`;
  return `<article class="message ${m.agent ? "agent" : ""} ${m.thinking ? "is-thinking" : ""} ${m.streaming ? "is-streaming" : ""}" data-msgid="${esc(m.id || "")}">
    <div class="message-avatar ${m.agent ? "agent" : ""}"${m.agente && m.agente.color ? ` style="background:${esc(m.agente.color)}"` : ""}>${esc(m.initials || (m.agent ? "HA" : "DS"))}</div>
    <div class="message-main">
      <div class="message-head">
        <strong>${esc(m.author)}</strong>
        <time title="${esc(m.fullTime || m.time || "")}">${esc(m.time || "ahora")}</time>
        ${m.tag ? `<span class="message-tag">${esc(m.tag)}</span>` : ""}
        ${m.agente && m.agente.nombre ? `<span class="agent-badge" style="--agent-color:${esc(m.agente.color || "#a78bfa")}"><i style="background:${esc(m.agente.color || "#a78bfa")}"></i>${esc(m.agente.nombre)}</span>` : ""}
        ${m.agent && !m.thinking ? `<button type="button" class="msg-copy" title="Copiar mensaje"><svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-2a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 3.5v5A1.5 1.5 0 0 0 4 10h1.5"/></svg></button>` : ""}
      </div>
      <div class="message-body">${body}</div>
    </div>
  </article>`;
}

function emptyStateHtml() {
  if (state.channel === "agents") {
    return `<div class="messages-empty">
      <div class="messages-empty-icon">◈</div>
      <div class="messages-empty-text"><strong>Habla con tu asistente</strong><br><span>Escríbele abajo o prueba una sugerencia:</span></div>
      <div class="suggest-row">
        <button type="button" class="suggest-card" data-suggest="audit"><span class="suggest-ico"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M9 1.5L3.5 9H7l-1 5.5L11.5 7H8z"/></svg></span>Audita el archivo actual</button>
        <button type="button" class="suggest-card" data-suggest="explain"><span class="suggest-ico"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M4 1.8h5.5L12.5 5v9.2h-8.5z"/><path d="M9.5 1.8V5h3"/></svg></span>Explícame este proyecto</button>
        <button type="button" class="suggest-card" data-suggest="next"><span class="suggest-ico"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M8 1.8a4.2 4.2 0 0 1 2.4 7.6c-.6.5-1 1-1 1.8H6.6c0-.8-.4-1.3-1-1.8A4.2 4.2 0 0 1 8 1.8z"/><path d="M6.8 13.5h2.4"/></svg></span>Propón el siguiente paso</button>
      </div>
    </div>`;
  }
  return `<div class="messages-empty">
    <div class="messages-empty-icon">💬</div>
    <div class="messages-empty-text">No hay mensajes aún en este canal.<br>Envía el primero para iniciar la conversación.</div>
  </div>`;
}

function renderMessages() {
  const box = $("messages");
  const wasNearBottom = isNearBottom(box);
  const visible = state.messages.filter(m => m.channel === state.channel);
  box.innerHTML = visible.length ? visible.map(messageHtml).join("") : emptyStateHtml();
  if (wasNearBottom) box.scrollTop = box.scrollHeight;
}

// Actualiza SOLO el cuerpo de un mensaje en streaming (sin re-render completo).
function updateStreamBody(msgId, full) {
  const el = document.querySelector(`#messages [data-msgid="${CSS.escape(msgId)}"] .message-body`);
  if (el) {
    const mdRender = getMarkdownRenderer();
    el.innerHTML = mdRender(full);
    const box = $("messages");
    if (isNearBottom(box)) box.scrollTop = box.scrollHeight;
  }
}

let thinkingPhaseTimer = null;
function showThinking(id, agentLabel) {
  addMessage({
    id, author: agentLabel,
    initials: agentLabel.slice(0, 2).toUpperCase(),
    agent: true, time: timeNow(), text: "",
    thinking: true, phase: "Conectando…"
  }, "agents");
  clearTimeout(thinkingPhaseTimer);
  // Fase honesta de UI: si aún no llegó contenido, el estado real es "generando".
  thinkingPhaseTimer = setTimeout(() => {
    const m = state.messages.find(x => x.id === id);
    if (m && m.thinking) {
      m.phase = "Generando respuesta…";
      renderMessages();
    }
  }, 2500);
}
function hideThinking(id) {
  clearTimeout(thinkingPhaseTimer);
  removeMessageById(id, "agents");
}

function finalizeStream(msgId, full, retcode, thinkId, agentLabel, tag, opts = {}) {
  if (thinkId) hideThinking(thinkId);
  const text = (full || "").trim();
  if (msgId) {
    const m = state.messages.find(x => x.id === msgId);
    if (m) {
      m.text = text || (opts.cancelled ? "" : "Sin respuesta del asistente.");
      m.streaming = false;
      m.thinking = false;
      if (opts.cancelled) m.tag = "DETENIDO";
      else if (retcode !== 0 && !m.tag) m.tag = "ERROR";
    }
    renderMessages();
  }
  if (opts.cancelled) toast("⏹ Respuesta detenida — se conserva el texto parcial.");
  if (opts.onDone) opts.onDone(retcode === 0, text);
}

// ---------------- Streaming real con el asistente (SSE) ----------------
let activeStream = null; // {controller, msgId}

function activeSlotName() {
  const sel = $("slotSwitcherSelect");
  return (sel && sel.value ? sel.value : "").trim();
}

function agentComposerPlaceholder() {
  const name = activeSlotName();
  return name ? `Pregúntale a ${name}…` : "Conecta un asistente para chatear…";
}

// "@Nombre ..." al inicio o entre palabras → ese slot responde (si existe).
function slotMentionIn(text) {
  const sel = $("slotSwitcherSelect");
  if (!sel) return "";
  const names = [...sel.options].map(o => o.value).filter(Boolean);
  for (const n of names) {
    const re = new RegExp(`@${n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\p{L}\\d_])`, "iu");
    if (re.test(text)) return n;
  }
  return "";
}

function updateComposerState() {
  const streaming = !!activeStream;
  const sendBtn = $("sendBtn"), stopBtn = $("stopBtn");
  if (sendBtn) sendBtn.hidden = streaming;
  if (stopBtn) stopBtn.hidden = !streaming;
  document.querySelectorAll(".quick-pill").forEach(b => { b.disabled = streaming; });
}

async function streamAgent({ prompt, slot = "", tag = "", onDone = null }) {
  const slotName = (slot || activeSlotName()).trim();
  const agentLabel = slotName || "Mi asistente";
  const initials = agentLabel.slice(0, 2).toUpperCase();
  const thinkId = "thinking-" + Date.now();
  showThinking(thinkId, agentLabel);
  const ctrl = new AbortController();
  activeStream = { controller: ctrl, msgId: null };
  updateComposerState();

  let full = "";
  let msgId = null;
  let gotChunk = false;
  let retcode = 0;
  try {
    const res = await fetch(`${HUB_BASE}/api/agents/stream?token=${encodeURIComponent(state.token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, slot: slotName, proyecto: "general" }),
      signal: ctrl.signal,
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.error || `HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let ev = "data", data = "";
        for (const ln of block.split("\n")) {
          if (ln.startsWith("event:")) ev = ln.slice(6).trim();
          else if (ln.startsWith("data:")) data = ln.slice(5).trim();
        }
        if (!data) continue;
        let payload = {};
        try { payload = JSON.parse(data); } catch (_) { continue; }
        if (ev === "data" && payload.chunk) {
          if (!gotChunk) {
            gotChunk = true;
            hideThinking(thinkId);
            msgId = "stream-" + Date.now();
            activeStream.msgId = msgId;
            addMessage({
              id: msgId, author: agentLabel, initials,
              agent: true, time: timeNow(), text: "",
              tag: tag || "", streaming: true
            }, "agents");
          }
          full += payload.chunk;
          updateStreamBody(msgId, full);
        } else if (ev === "done") {
          retcode = payload.retcode ?? 0;
        } else if (ev === "error") {
          throw new Error(payload.error || "Error del asistente.");
        }
      }
    }
    finalizeStream(msgId, full, retcode, thinkId, agentLabel, tag, { onDone });
  } catch (err) {
    if (err && err.name === "AbortError") {
      finalizeStream(msgId, full, 130, thinkId, agentLabel, tag, { cancelled: true, onDone });
    } else {
      hideThinking(thinkId);
      const msg = `No se pudo contactar al asistente: ${(err && err.message) || err}`;
      if (msgId) finalizeStream(msgId, full || msg, 1, null, agentLabel, "ERROR", { onDone });
      else addMessage({ author: "Sistema", initials: "✦", agent: true, time: timeNow(), text: msg, tag: "ERROR" }, "agents");
    }
  } finally {
    if (activeStream && activeStream.controller === ctrl) activeStream = null;
    updateComposerState();
  }
}

function stopActiveStream() {
  if (activeStream) activeStream.controller.abort();
}

function autosizeComposer() {
  const ta = $("messageInput");
  if (!ta) return;
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

function renderContextChip() {
  const row = $("contextChipRow"), name = $("contextChipName");
  if (!row || !name) return;
  if (state.contextFile) {
    name.textContent = state.contextFile.title;
    row.hidden = false;
  } else {
    row.hidden = true;
  }
}

function handleSuggestion(kind) {
  setChannel("agents");
  $("assistantPanel").classList.remove("collapsed");
  if (kind === "audit") { triggerAudit(); return; }
  if (kind === "explain") { triggerExplain(); return; }
  if (kind === "next") {
    const input = $("messageInput");
    input.value = "Propón el siguiente paso concreto para avanzar en este proyecto. Sé específico y accionable.";
    input.focus();
    autosizeComposer();
  }
}

function setChannel(channel) {
  state.channel = channel;
  document.querySelectorAll(".assistant-tab").forEach(b => b.classList.toggle("active", b.dataset.channel === channel));
  $("channelIcon").textContent = channel === "team" ? "#" : "◈";
  $("channelName").textContent = channel === "team" ? "team-chat" : "hermes-bridge";
  $("channelHint").textContent = channel === "team" ? `${currentUserName() || "Tú"} y tu equipo en tiempo real` : "Debate y orquestación entre agentes Hermes";
  $("messageInput").placeholder = channel === "team" ? "Escribe un mensaje al equipo…" : agentComposerPlaceholder();
  if (channel === "agents") loadAgentBus();
  renderMessages();
}

let busLoading = false;
async function loadAgentBus() {
  if (busLoading) return;
  busLoading = true;
  try {
    const res = await api("/api/agents/bus?proyecto=general");
    const mensajes = (res && res.mensajes) || [];
    // Reemplazar solo los mensajes del bus (los locales del debate se conservan)
    state.messages = state.messages.filter(m => !(m.channel === "agents" && m.src === "bus"));
    mensajes.slice(-80).forEach(m => {
      if (m.id) state.agentSeen.add(m.id);
      const de = m.de || "Hermes";
      state.messages.push({
        id: m.id,
        src: "bus",
        channel: "agents",
        author: de,
        initials: String(de).slice(0, 2).toUpperCase(),
        agent: true,
        time: m.fecha || timeNow(),
        text: m.texto || "",
        tag: String(m.tipo || "mensaje").toUpperCase(),
        agente: m.agente || null
      });
    });
    renderMessages();
  } catch (_) {
    // El canal funciona igual con mensajes locales si el bus no responde
  }
  busLoading = false;
}

async function sendMessage(event) {
  if (event) event.preventDefault();
  const input = $("messageInput");
  const text = input.value.trim();
  if (!text) return;
  if (activeStream) {
    toast("Espera a que termine la respuesta actual (o detenla con ■).");
    return;
  }
  input.value = "";
  autosizeComposer();

  const userName = currentUserName();
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
      const payload = { de: userName, texto: text };
      const sendOnce = () => api("/api/chat?proyecto=general", { method: "POST", body: JSON.stringify(payload) });
      sendOnce().catch(() =>
        toast("No se pudo enviar el mensaje al equipo.", {
          type: "err", duration: 6000,
          retry: {
            fn: () => sendOnce()
              .then(() => toast("✓ Mensaje enviado"))
              .catch(() => toast("Sigue fallando. Revisa la conexión con el hub.", "err"))
          }
        }));
    }
  } else {
    // Chat directo con el asistente: streaming real (slot activo o @mencionado).
    const slot = slotMentionIn(text);
    let prompt = text;
    if (state.contextFile) {
      const cf = state.contextFile;
      prompt += `\n\n--- Archivo adjunto: ${cf.title} ---\n${cf.content.slice(0, 12000)}\n--- Fin del archivo ---`;
      state.contextFile = null;
      renderContextChip();
    }
    streamAgent({ prompt, slot });
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
  const userName = currentUserName();
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
async function triggerDebate() {
  setChannel("agents");
  const currentTab = state.openTabs[state.activeTabIdx];
  const filename = currentTab ? currentTab.title : "workspace";
  const fileCtx = currentTab ? currentTab.content.slice(0, 6000) : "";
  const myAgent = localStorage.getItem("atlantis_agent_name") || "Hermes-Daniel";
  const friendAgent = localStorage.getItem("atlantis_friend_agent_name") || "Hermes-Amigo";
  const userName = currentUserName();

  addMessage({
    author: userName,
    initials: userName.slice(0, 2).toUpperCase(),
    time: timeNow(),
    text: `Iniciando debate dual sobre ${filename}: ${myAgent} propone, ${friendAgent} audita.`
  }, "agents");

  const typingId = "typing-" + Date.now();
  addMessage({
    id: typingId, author: myAgent, initials: myAgent.slice(0, 2).toUpperCase(),
    agent: true, time: timeNow(), text: "Generando propuesta técnica…", tag: "PENSANDO"
  }, "agents");

  try {
    const res = await api("/api/ai/dual/run", {
      method: "POST",
      body: JSON.stringify({
        objetivo: `Debate técnico dual sobre el archivo "${filename}". Propón optimizaciones concretas de arquitectura y código, y audita la propuesta señalando riesgos y mejoras.${fileCtx ? `\n\nContenido del archivo:\n${fileCtx}` : ""}`,
        proyecto: "general"
      })
    });
    removeMessageById(typingId, "agents");
    if (!res || res.ok === false) {
      const errMsg = (res && (res.error || res.detalle)) || "error desconocido";
      addMessage({
        author: "Sistema", initials: "✦", agent: true, time: timeNow(),
        text: errMsg === "conecta tu Hermes"
          ? "Conecta tu Hermes para usar el debate dual (asistente de inicio → paso 1)."
          : `El debate no pudo completarse: ${errMsg}`,
        tag: "AVISO"
      }, "agents");
      return;
    }
    const agentePropone = res.agente_propone || myAgent;
    const agenteAudita = res.agente_audita || friendAgent;
    const modoTag = res.modo === "dual" ? "DUAL" : "INDIVIDUAL";
    if (res.propuesta) {
      addMessage({
        author: agentePropone, initials: String(agentePropone).slice(0, 2).toUpperCase(), agent: true,
        time: timeNow(), text: String(res.propuesta).slice(0, 2000), tag: "PROPUESTA"
      }, "agents");
    }
    if (res.auditoria) {
      addMessage({
        author: agenteAudita, initials: String(agenteAudita).slice(0, 2).toUpperCase(), agent: true,
        time: timeNow(), text: String(res.auditoria).slice(0, 2000), tag: "AUDITORÍA"
      }, "agents");
    }
    if (!res.propuesta && !res.auditoria) {
      addMessage({
        author: "Sistema", initials: "✦", agent: true, time: timeNow(),
        text: "El debate no devolvió contenido.",
        tag: "AVISO"
      }, "agents");
    } else {
      addMessage({
        author: "Sistema", initials: "✦", agent: true, time: timeNow(),
        text: res.modo === "dual"
          ? `Modo DUAL: ${agentePropone} propuso y ${agenteAudita} auditó por el bus de agentes.`
          : `Modo INDIVIDUAL: ${agentePropone} propuso y auditó (sin equipo vinculado).`,
        tag: `MODO ${modoTag}`
      }, "agents");
    }
  } catch (err) {
    removeMessageById(typingId, "agents");
    addMessage({
      author: "Sistema", initials: "✦", agent: true, time: timeNow(),
      text: `No se pudo iniciar el debate: ${err.message}`, tag: "ERROR"
    }, "agents");
  }
}

function removeMessageById(id, channel) {
  const idx = state.messages.findIndex(m => m.id === id && m.channel === channel);
  if (idx >= 0) {
    state.messages.splice(idx, 1);
    renderMessages();
  }
}

// ---------------- Acciones rápidas con el asistente local (sin claves, sin nube) ----------------
async function hermesDisponible() {
  try {
    const st = await api("/api/ai/status");
    if (st && st.agente) return !!st.agente.disponible;
    return !!(st && st.hermes && st.hermes.instalado);
  } catch {
    return false;
  }
}

function mensajeHermesNoConectado() {
  addMessage({
    author: "Sistema", initials: "✦", agent: true, time: timeNow(),
    text: "Conecta tu asistente para usar esta función (asistente de inicio → paso 1: Conectar mi Asistente).",
    tag: "AVISO"
  }, "agents");
}

async function triggerAudit() {
  setChannel("agents");
  const currentTab = state.openTabs[state.activeTabIdx];
  if (!currentTab) {
    toast("Abre un archivo primero para auditar su código");
    return;
  }
  const myAgent = localStorage.getItem("atlantis_agent_name") || "Mi Hermes";
  const userName = currentUserName();

  addMessage({
    author: userName,
    initials: userName.slice(0, 2).toUpperCase(),
    time: timeNow(),
    text: `Solicito auditoría de seguridad y calidad para el archivo "${currentTab.title}".`
  }, "agents");

  try {
    if (!(await hermesDisponible())) {
      mensajeHermesNoConectado();
      return;
    }
    const prompt = `Actúa como auditor técnico senior. Audita el archivo "${currentTab.title}": detecta bugs, vulnerabilidades de seguridad, problemas de rendimiento y malas prácticas. Sé conciso y accionable, con severidad por hallazgo.\n\nContenido del archivo:\n${currentTab.content.slice(0, 12000)}`;
    streamAgent({ prompt, tag: "AUDITORÍA" });
  } catch (err) {
    addMessage({
      author: "Sistema", initials: "✦", agent: true, time: timeNow(),
      text: `No se pudo completar la auditoría: ${err.message}`, tag: "ERROR"
    }, "agents");
  }
}

async function triggerExplain() {
  setChannel("agents");
  const currentTab = state.openTabs[state.activeTabIdx];
  if (!currentTab) {
    toast("Abre un archivo primero para explicarlo");
    return;
  }
  const myAgent = localStorage.getItem("atlantis_agent_name") || "Mi Hermes";
  const userName = currentUserName();

  addMessage({
    author: userName,
    initials: userName.slice(0, 2).toUpperCase(),
    time: timeNow(),
    text: `¿Qué hace el archivo "${currentTab.title}"?`
  }, "agents");

  try {
    if (!(await hermesDisponible())) {
      mensajeHermesNoConectado();
      return;
    }
    const prompt = `Explica de forma clara y estructurada qué hace el archivo "${currentTab.title}": su propósito, componentes principales y cómo encajan. Usa español directo.\n\nContenido del archivo:\n${currentTab.content.slice(0, 12000)}`;
    streamAgent({ prompt, tag: "EXPLICACIÓN" });
  } catch (err) {
    addMessage({
      author: "Sistema", initials: "✦", agent: true, time: timeNow(),
      text: `No se pudo generar la explicación: ${err.message}`, tag: "ERROR"
    }, "agents");
  }
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
          <button class="skill-btn" data-viewskill="${esc(s.id)}"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M3 2.5h6.5a2 2 0 0 1 2 2v9H5a2 2 0 0 1-2-2z"/><path d="M3 11.5h8.5"/></svg> Ver</button>
          <button class="skill-btn" data-editskill="${esc(s.id)}" title="Abrir en Monaco Editor"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M11.5 2.8l1.7 1.7L5.5 12.2l-2.4.7.7-2.4z"/></svg> Editar</button>
          <button class="skill-btn primary" data-runskill="${esc(s.id)}"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M9 1.5L3.5 9H7l-1 5.5L11.5 7H8z"/></svg> Ejecutar</button>
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

async function refreshMyMcps() {
  const container = $("myMcpList");
  if (!container) return;
  let mcps = [], available = true, errMsg = "";
  try {
    const res = await api("/api/mcps").catch(() => null);
    if (res && res.ok) { mcps = res.mcps || []; available = !!res.mcp_available; errMsg = res.mcp_error || ""; }
  } catch (err) { errMsg = err.message; available = false; }
  if (!available) {
    container.innerHTML = `<div class="tree-empty">El paquete <span class="mono">mcp</span> no está instalado en el servidor.<br>Instálalo con <span class="mono">pip install "mcp&lt;2"</span>${errMsg ? `<br><small>${esc(errMsg)}</small>` : ""}</div>`;
    return;
  }
  if (!mcps.length) {
    container.innerHTML = `<div class="tree-empty">Aún no creaste ningún MCP.<br>Usa <b>＋ Nuevo MCP</b> para crear tu primer servidor.</div>`;
    return;
  }
  container.innerHTML = mcps.map(m => {
    const running = m.status === "corriendo";
    return `<div class="mcp-card ${running ? "mcp-card-live" : "mcp-card-stopped"}">
      <div class="skill-title-block">
        <strong style="color:var(--green); font-size:12px;">🔌 ${esc(m.name)}</strong>
        <small style="color:var(--muted); font-size:10px;">${esc(m.description || "")} · ${m.tools.length} herramienta(s) · ${running ? "🟢 corriendo" : "⚪ detenido"}</small>
      </div>
      <div class="mcp-card-actions" style="display:flex; gap:6px; margin-top:8px; flex-wrap:wrap;">
        ${running
          ? `<button type="button" class="action-btn sm" data-mcp-stop="${esc(m.name)}"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><rect x="3.5" y="3.5" width="9" height="9" rx="1.5"/></svg> Detener</button>`
          : `<button type="button" class="action-btn sm" data-mcp-start="${esc(m.name)}"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M5 3.2v9.6L12.5 8z"/></svg> Arrancar</button>`}
        <button type="button" class="action-btn sm" data-mcp-test="${esc(m.name)}"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M6.5 2h3M7 2v4.2L3.2 12a1.5 1.5 0 0 0 1.3 2.2h7a1.5 1.5 0 0 0 1.3-2.2L9 6.2V2"/></svg> Probar</button>
        <button type="button" class="action-btn sm danger" data-mcp-del="${esc(m.name)}"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px"><path d="M2.5 4h11M6.5 2.5h3M4 4l.7 9.2a1 1 0 0 0 1 .8h4.6a1 1 0 0 0 1-.8L12 4"/><path d="M6.5 7v4M9.5 7v4"/></svg> Eliminar</button>
      </div>
      <div class="mcp-test-panel" id="mcpTest-${esc(m.name)}" hidden>
        <div style="display:flex; gap:6px; margin-bottom:6px;">
          <select class="cyber-select mcp-test-tool" style="flex:1;"><option>Cargando herramientas…</option></select>
          <button type="button" class="action-btn sm" data-mcp-tools="${esc(m.name)}">↻ Listar</button>
        </div>
        <textarea class="text-input mcp-test-args" spellcheck="false" placeholder='Argumentos JSON, ej. {"nombre": "Daniel"}' style="min-height:40px;"></textarea>
        <button type="button" class="action-btn sm" data-mcp-call="${esc(m.name)}" style="margin-top:6px;">▶ Ejecutar herramienta</button>
        <div class="mcp-test-result" hidden></div>
      </div>
    </div>`;
  }).join("");

  container.querySelectorAll("[data-mcp-start]").forEach(b => b.addEventListener("click", async () => {
    const n = b.getAttribute("data-mcp-start");
    try { await api(`/api/mcps/${encodeURIComponent(n)}/start`, { method: "POST" }); toast(`✓ ${n} arrancado`); }
    catch (err) { toast(`Error: ${err.message}`); }
    refreshMyMcps();
  }));
  container.querySelectorAll("[data-mcp-stop]").forEach(b => b.addEventListener("click", async () => {
    const n = b.getAttribute("data-mcp-stop");
    try { await api(`/api/mcps/${encodeURIComponent(n)}/stop`, { method: "POST" }); toast(`■ ${n} detenido`); }
    catch (err) { toast(`Error: ${err.message}`); }
    refreshMyMcps();
  }));
  container.querySelectorAll("[data-mcp-del]").forEach(b => b.addEventListener("click", async () => {
    const n = b.getAttribute("data-mcp-del");
    if (!confirm(`¿Eliminar el MCP '${n}'? Se borrará su carpeta en mcps/.`)) return;
    try { await api(`/api/mcps/${encodeURIComponent(n)}`, { method: "DELETE" }); toast(`✓ ${n} eliminado`); }
    catch (err) { toast(`Error: ${err.message}`); }
    refreshMyMcps();
  }));
  container.querySelectorAll("[data-mcp-test]").forEach(b => b.addEventListener("click", async () => {
    const n = b.getAttribute("data-mcp-test");
    const panel = $(`mcpTest-${CSS.escape(n)}`);
    if (!panel) return;
    panel.hidden = !panel.hidden;
    if (!panel.hidden) await mcpLoadTools(n);
  }));
  container.querySelectorAll("[data-mcp-tools]").forEach(b => b.addEventListener("click", async () => {
    await mcpLoadTools(b.getAttribute("data-mcp-tools"));
  }));
  container.querySelectorAll("[data-mcp-call]").forEach(b => b.addEventListener("click", async () => {
    const n = b.getAttribute("data-mcp-call");
    const panel = $(`mcpTest-${CSS.escape(n)}`);
    const toolSel = panel?.querySelector(".mcp-test-tool");
    const argsRaw = panel?.querySelector(".mcp-test-args")?.value.trim() || "{}";
    const resultBox = panel?.querySelector(".mcp-test-result");
    let args;
    try { args = JSON.parse(argsRaw); }
    catch { toast("Argumentos JSON inválidos."); return; }
    if (resultBox) { resultBox.hidden = false; resultBox.textContent = "Ejecutando…"; }
    try {
      const res = await api(`/api/mcps/${encodeURIComponent(n)}/call`, {
        method: "POST", body: JSON.stringify({ tool: toolSel?.value, args })
      });
      if (!res.ok) throw new Error(res.error || "Falló la llamada");
      if (resultBox) resultBox.textContent = res.isError ? `⚠ Error del tool:\n${res.text}` : `✓ Resultado:\n${res.text}`;
    } catch (err) {
      if (resultBox) resultBox.textContent = `✗ Error: ${err.message}`;
    }
  }));
}

async function mcpLoadTools(name) {
  const panel = $(`mcpTest-${CSS.escape(name)}`);
  const sel = panel?.querySelector(".mcp-test-tool");
  if (!sel) return;
  sel.innerHTML = `<option>Cargando…</option>`;
  try {
    const res = await api(`/api/mcps/${encodeURIComponent(name)}/tools`, { method: "POST" });
    if (!res.ok) throw new Error(res.error || "No se pudieron listar");
    sel.innerHTML = (res.tools || []).map(t =>
      `<option value="${esc(t.name)}">${esc(t.name)} — ${esc((t.description || "").slice(0, 60))}</option>`
    ).join("") || `<option value="">(sin herramientas)</option>`;
  } catch (err) {
    sel.innerHTML = `<option value="">Error: ${esc(err.message)}</option>`;
  }
}

function renderMcp() {
  const container = $("mcpList");
  if (!container) return;
  const servers = state.antigravity.mcp || {};
  const names = Object.keys(servers);
  const mySection = `
    <div class="mcp-my-header" style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;">
      <strong style="font-size:12px;">🛠 Mis MCPs <span style="color:var(--muted); font-weight:normal;">(creados en Atlantis)</span></strong>
      <button type="button" class="action-btn sm" id="btnOpenMcpWizard2">＋ Nuevo MCP</button>
    </div>
    <div id="myMcpList"><div class="tree-empty">Cargando…</div></div>
    <div style="border-top:1px solid var(--border); margin:12px 0;"></div>
    <strong style="font-size:12px;">🔌 MCPs externos <span style="color:var(--muted); font-weight:normal;">(mcp_config.json)</span></strong>
    <div style="margin-top:8px;" id="extMcpList"></div>`;
  const extHtml = !names.length
    ? `<div class="tree-empty">No hay servidores MCP configurados en mcp_config.json.</div>`
    : names.map(k => {
      const s = servers[k];
      return `
      <div class="mcp-card">
        <div class="skill-title-block">
          <strong style="color:var(--green); font-size:12px;">🔌 ${esc(k)}</strong>
          <small style="color:var(--dim); font-family:var(--font-mono);">Comando: ${esc(s.command || "")} ${(s.args || []).join(" ")}</small>
        </div>
        <small style="color:var(--muted); font-size:10px;">Origen: ${esc(s.source || "")}</small>
      </div>`;
    }).join("");
  container.innerHTML = mySection;
  $("extMcpList").innerHTML = extHtml;
  $("btnOpenMcpWizard2")?.addEventListener("click", openMcpWizard);
  refreshMyMcps();
}

function renderSources() {
  const container = $("sourcesList");
  if (!container) return;
  const sources = state.antigravity.sources || [];
  container.innerHTML = sources.map(src => `
    <div class="source-item">
      <span>${esc(src.name)}</span>
      <code title="${esc(src.path)}">${esc(src.tipo)}</code>
      ${src.tipo === "custom" ? `<button type="button" class="source-remove-btn" data-path="${esc(src.path)}" title="Quitar esta fuente">×</button>` : ""}
    </div>
  `).join("");
  container.querySelectorAll(".source-remove-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const p = btn.getAttribute("data-path");
      if (!p || !confirm(`¿Quitar la fuente personalizada?\n${p}`)) return;
      try {
        await api("/api/antigravity/sources/remove", {
          method: "POST",
          body: JSON.stringify({ path: p })
        });
        await loadAntigravityData();
        toast("✓ Fuente eliminada");
      } catch (err) {
        toast(`Error: ${err.message}`);
      }
    });
  });
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
  toast(`Iniciando ejecución de habilidad: ${skillId}...`);

  // Switch to agents chat
  setChannel("agents");
  $("assistantPanel").classList.remove("collapsed");

  addMessage({
    author: currentUserName(),
    initials: currentUserInitials(),
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
  { label: "Abrir Archivo o Carpeta...", kbd: "⌘ O", action: () => toggleOpenChoiceMenu($("btnOpenSystem") || null) },
  { label: "Abrir Archivo Individual...", kbd: "Ctrl+O", action: () => promptOpenSystemFile() },
  { label: "Abrir Carpeta (Workspace)...", kbd: "⇧⌘O", action: () => promptOpenSystemFolder() },
  { label: "Guardar Archivo Actual", kbd: "⌘ S", action: () => saveCurrentFile() },
  { label: "Nuevo Archivo en Workspace", kbd: "⌘ N", action: () => $("newFileDialog").showModal() },
  { label: "Nuevo Proyecto (plantilla)...", kbd: "⇧⌘ N", action: () => openProjectWizard() },
  { label: "Nuevo MCP (servidor)...", kbd: "", action: () => openMcpWizard() },
  { label: "Diagnóstico de Salud del Sistema", kbd: "Alt+S", action: () => executeTerminalCmd("python3 salud_sistema.py") },
  { label: "Control de Versiones (Git Status)", kbd: "⇧⌘G", action: () => switchActivityView("source") },
  { label: "Buscar en Archivos", kbd: "⇧⌘F", action: () => switchActivityView("search") },
  { label: "Iniciar Debate Dual entre Agentes", kbd: "⌘ D", action: () => triggerDebate() },
  { label: "Auditar Código del Archivo Abierto", kbd: "", action: () => triggerAudit() },
  { label: "Compartir proyecto", kbd: "⌘ I", action: () => openShareForActiveTab() },
  { label: "Abrir Configuración de Atlantis", kbd: "⌘ ,", action: () => $("settingsDialog").showModal() },
  { label: "Limpiar Salida de la Terminal", kbd: "Ctrl+L", action: () => { $("terminalOutput").innerHTML = ""; toast("Terminal limpiada"); } },
  { label: "Recargar Árbol de Archivos", kbd: "⌘ R", action: () => { loadTree(); toast("Árbol recargado"); } }
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
          label: `${f}`,
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
    document.querySelectorAll(".activity").forEach(b => b.classList.toggle("active", b.dataset.view === view));
    openSettingsModal();
    return;
  }
  if (view === "collab") {
    document.querySelectorAll(".activity").forEach(b => b.classList.toggle("active", b.dataset.view === view));
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
    plugins: "PLUGINS"
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
  $("settingsDialog").showModal();
}

function openProfileModal() {
  applyUserProfile(Auth.user);
  $("profileDialog").showModal();
}

// ---------------- Invite & Share Modal ----------------
// ---------------- Nuevo Proyecto: plantillas ----------------
const PROJECT_TEMPLATES = {
  web: {
    nombre: "Web estático",
    archivos: {
      "index.html": `<!DOCTYPE html>\n<html lang="es">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>Mi Proyecto</title>\n<link rel="stylesheet" href="styles.css">\n</head>\n<body>\n<h1>Hola desde Atlantis Studio</h1>\n<p>Edita este archivo y mira los cambios en vivo.</p>\n<button id="btn">Haz clic</button>\n<script src="app.js"></script>\n</body>\n</html>\n`,
      "styles.css": `body { font-family: system-ui, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; background: #0f172a; color: #e2e8f0; }\nh1 { color: #60a5fa; }\nbutton { background: #2563eb; color: #fff; border: none; border-radius: 8px; padding: 10px 18px; cursor: pointer; font-size: 15px; }\nbutton:hover { background: #1d4ed8; }\n`,
      "app.js": `// app.js — lógica de tu proyecto web\ndocument.getElementById("btn").addEventListener("click", () => {\n  alert("¡Funciona! Proyecto creado con Atlantis Studio.");\n});\nconsole.log("Proyecto web listo.");\n`
    },
    abrir: "index.html"
  },
  node: {
    nombre: "Node.js",
    archivos: {
      "package.json": `{\n  "name": "mi-proyecto",\n  "version": "1.0.0",\n  "type": "module",\n  "scripts": { "start": "node index.js" }\n}\n`,
      "index.js": `// index.js — servidor Node.js creado con Atlantis Studio\nimport http from "node:http";\n\nconst PORT = process.env.PORT || 3000;\nhttp.createServer((req, res) => {\n  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });\n  res.end("<h1>Hola desde Node.js + Atlantis Studio</h1>");\n}).listen(PORT, () => console.log("Servidor en http://localhost:" + PORT));\n`
    },
    abrir: "index.js"
  },
  python: {
    nombre: "Python",
    archivos: {
      "main.py": `#!/usr/bin/env python3\n# main.py — proyecto Python creado con Atlantis Studio\n\ndef main():\n    print("Hola desde Atlantis Studio")\n\nif __name__ == "__main__":\n    main()\n`,
      "requirements.txt": `# Dependencias del proyecto (una por línea)\n`
    },
    abrir: "main.py"
  },
  empty: {
    nombre: "Vacío",
    archivos: {
      "README.md": `# Mi proyecto\n\nCreado con Atlantis Studio.\n`
    },
    abrir: "README.md"
  }
};

let selectedTemplate = "web";

function openProjectWizard() {
  const input = $("projectNameInput");
  if (input) { input.value = ""; }
  document.querySelectorAll(".template-card").forEach(c =>
    c.classList.toggle("selected", c.dataset.template === selectedTemplate));
  const hint = $("projectWizardHint");
  if (hint) hint.textContent = "Se creará la carpeta dentro del workspace actual.";
  $("projectWizardDialog").showModal();
  setTimeout(() => input && input.focus(), 100);
}

function sanitizarNombreProyecto(nombre) {
  return nombre.trim().toLowerCase().replace(/[^a-z0-9-_]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
}

async function apiGuardarArchivo(ruta, contenido) {
  return api(`/api/archivo?ruta=${encodeURIComponent(ruta)}`, {
    method: "POST",
    body: JSON.stringify({ contenido, autor: currentUserName() })
  });
}

async function createProject() {
  const nombre = sanitizarNombreProyecto($("projectNameInput").value || "");
  if (!nombre) { toast("Escribe un nombre para el proyecto."); return; }
  const tpl = PROJECT_TEMPLATES[selectedTemplate];
  if (!tpl) return;
  const btn = $("btnCreateProject");
  btn.disabled = true;
  try {
    for (const [rel, contenido] of Object.entries(tpl.archivos)) {
      const final = rel === "package.json"
        ? contenido.replace('"name": "mi-proyecto"', `"name": ${JSON.stringify(nombre)}`)
        : contenido;
      await apiGuardarArchivo(`${nombre}/${rel}`, final);
    }
    await loadTree().catch(() => {});
    $("projectWizardDialog").close();
    toast(`✓ Proyecto "${nombre}" creado (${tpl.nombre})`);
    try { await openFile(`${nombre}/${tpl.abrir}`); } catch (_) {}
    await refreshProjectRunBox();
  } catch (err) {
    toast("Error al crear el proyecto: " + err.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------------- Detección y ejecución de proyecto ----------------
let detectedProject = null;

async function listarRaiz() {
  try {
    const res = await api("/api/arbol").catch(() => null);
    const arbol = res && (res.arbol || res.tree || res);
    if (Array.isArray(arbol)) {
      const raiz = arbol.find(n => n && n.es_raiz) || arbol[0];
      if (raiz && Array.isArray(raiz.archivos)) return raiz.archivos;
      return arbol.map(n => typeof n === "string" ? n : (n.nombre || n.name || n.ruta || ""));
    }
    if (arbol && typeof arbol === "object") return Object.keys(arbol);
  } catch (_) {}
  return [];
}

async function leerArchivoProyecto(ruta) {
  try {
    // Alcance __raiz__: el detector de proyecto trabaja sobre la raíz del workspace.
    const res = await api(`/api/archivo?ruta=${encodeURIComponent(ruta)}&proyecto=__raiz__`).catch(() => null);
    return (res && (res.contenido ?? res.content ?? res.texto)) || "";
  } catch (_) { return ""; }
}

async function detectProjectType() {
  const raiz = await listarRaiz();
  // En raíz: coincidencia exacta (los archivos de subcarpetas los maneja el bucle de abajo)
  const enRaiz = (f) => raiz.some(n => n === f);
  const algunPyRaiz = raiz.some(n => n.endsWith(".py") && !n.includes("/"));
  if (enRaiz("package.json")) return { tipo: "node", etiqueta: "Node.js", dir: "" };
  if (enRaiz("requirements.txt") || algunPyRaiz) return { tipo: "python", etiqueta: "Python", dir: "" };
  if (enRaiz("index.html")) return { tipo: "web", etiqueta: "Web estático", dir: "" };
  // Buscar dentro de subcarpetas de primer nivel
  const tiene = (f) => raiz.some(n => n === f || n.endsWith("/" + f));
  const carpetas = [...new Set(raiz.map(n => n.split("/")[0]).filter(Boolean))].slice(0, 12);
  for (const c of carpetas) {
    if (tiene(`${c}/package.json`)) return { tipo: "node", etiqueta: "Node.js", dir: c };
    if (tiene(`${c}/requirements.txt`) || raiz.some(n => n.startsWith(c + "/") && n.endsWith(".py")))
      return { tipo: "python", etiqueta: "Python", dir: c };
    if (tiene(`${c}/index.html`)) return { tipo: "web", etiqueta: "Web estático", dir: c };
  }
  return { tipo: "ninguno", etiqueta: "Sin detectar", dir: "" };
}

async function buildRunCommand(det) {
  const cd = det.dir ? `cd ${det.dir} && ` : "";
  if (det.tipo === "node") {
    const pkgRaw = await leerArchivoProyecto(det.dir ? `${det.dir}/package.json` : "package.json");
    let cmd = "node index.js";
    try {
      const pkg = JSON.parse(pkgRaw);
      if (pkg.scripts && pkg.scripts.start) cmd = "npm start";
      else if (pkg.scripts && pkg.scripts.dev) cmd = "npm run dev";
      else if (pkg.main) cmd = `node ${pkg.main}`;
    } catch (_) {}
    const raiz = await listarRaiz();
    const hasModules = raiz.some(n => n === "node_modules" || n.startsWith("node_modules/") ||
      (det.dir && (n === `${det.dir}/node_modules` || n.startsWith(det.dir + "/node_modules/"))));
    const install = hasModules ? "" : "npm install --no-audit --no-fund && ";
    const esServidor = /start|dev|serve/.test(cmd);
    return { cmd: `${cd}${install}${cmd}`, bloqueante: esServidor,
             nota: esServidor ? "Servidor en ejecución (usa Limpiar terminal / reinicia el hub para detenerlo)." : "" };
  }
  if (det.tipo === "python") {
    const raiz = await listarRaiz();
    const enDir = (f) => det.dir ? `${det.dir}/${f}` : f;
    // No pedir archivos que no existen: evita 404s ruidosos en consola.
    const existe = (f) => raiz.some(n => n === enDir(f));
    let entry = "main.py";
    if (!existe("main.py") || !(await leerArchivoProyecto(enDir("main.py")))) {
      const py = raiz.find(n => (det.dir ? n.startsWith(det.dir + "/") : !n.includes("/")) && n.endsWith(".py"));
      entry = py ? py.split("/").pop() : "main.py";
    }
    const req = existe("requirements.txt") ? await leerArchivoProyecto(enDir("requirements.txt")) : "";
    const install = req.trim() ? "pip install -q -r requirements.txt && " : "";
    return { cmd: `${cd}${install}python3 ${entry}`, bloqueante: false, nota: "" };
  }
  if (det.tipo === "web") {
    const puerto = 8123;
    return { cmd: `${cd}nohup python3 -m http.server ${puerto} >/tmp/atlantis-web-${puerto}.log 2>&1 & sleep 1 && echo "🌐 Sirviendo en http://localhost:${puerto}"`,
             bloqueante: false, nota: `Abre http://localhost:${puerto} en tu navegador.` };
  }
  return { cmd: "", bloqueante: false, nota: "" };
}

async function refreshProjectRunBox() {
  const badge = $("projectTypeBadge");
  const text = $("projectDetectText");
  const preview = $("runCmdPreview");
  const btn = $("btnRunProject");
  if (badge) badge.textContent = "…";
  if (text) text.textContent = "Detectando tipo de proyecto…";
  if (preview) preview.hidden = true;
  if (btn) btn.disabled = true;
  try {
    detectedProject = await detectProjectType();
    if (badge) badge.textContent = detectedProject.etiqueta;
    if (detectedProject.tipo === "ninguno") {
      if (text) text.textContent = "No se detectó un proyecto ejecutable en el workspace.";
      if (btn) btn.disabled = true;
      return;
    }
    const run = await buildRunCommand(detectedProject);
    detectedProject.run = run;
    if (text) text.textContent = `Proyecto ${detectedProject.etiqueta}${detectedProject.dir ? ` en "${detectedProject.dir}"` : ""}.`;
    if (preview) { preview.textContent = "$ " + run.cmd; preview.hidden = false; }
    if (btn) btn.disabled = false;
  } catch (err) {
    if (text) text.textContent = "No se pudo detectar: " + err.message;
  }
}

// ---------------- Equipo por proyecto: sync, códigos, aprobaciones, conflictos, preview, cursores ----------------
let projectShareTarget = null; // {proyecto, carpeta}
let projectShareState = { equipo_id: "", code: "", join_link: "", expira_en: null };
let sharedPollTimer = null;
let previewPollTimer = null;
let liveCursorDecorations = new Map(); // nombre -> decoration ids de Monaco
let liveCursorStyleEl = null;
let conflictState = { lista: [], actual: null, proyecto: "general" };

// ---- Sync (ítem 5) ----
function syncStatusHtml(sync) {
  const s = sync || {};
  const estado = s.estado || (s.conectado ? "sincronizado" : "sin_conexion");
  const cls = estado === "sincronizado" ? "sync-ok" : estado === "pendiente" ? "sync-pending" : "sync-off";
  let label = estado === "sincronizado" ? "Sincronizado" : estado === "pendiente" ? `Pendiente (${s.pendientes ?? 0})` : "Sin conexión";
  // Transferencias de archivos grandes en vivo (progreso real del backend).
  const activas = (s.transferencias || []).filter(t => ["en_cola", "recibiendo", "verificando"].includes(t.estado));
  if (activas.length) {
    const t = activas[0];
    const mb = t.total_bytes ? (t.total_bytes / 1048576).toFixed(1) + " MB" : "";
    const donde = t.direccion === "subida" ? "Subiendo" : "Recibiendo";
    const nombre = String(t.ruta || "").split("/").pop();
    label += ` · ${donde} ${nombre} (${mb})`;
    if (activas.length > 1) label += ` +${activas.length - 1}`;
  }
  return `<span class="sync-status ${cls}" title="Sincronización: ${esc(label)}"><i class="sync-dot"></i>${esc(label)}</span>`;
}

function syncEstadoOf(p) {
  return (p && p.sync_estado) || null;
}

function updateActiveProjectSync() {
  const el = $("activeProjectSync");
  if (!el) return;
  const tab = state.openTabs[state.activeTabIdx];
  const p = tab ? sharedProjectForPath(tab.path, tab.project) : null;
  const sync = p ? syncEstadoOf(p) : null;
  if (!p || !sync) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = syncStatusHtml(sync);
}

function startSharedPolling() {
  stopSharedPolling();
  sharedPollTimer = setInterval(async () => {
    const shareOpen = $("projectShareDialog")?.open;
    const joinOpen = $("projectJoinDialog")?.open;
    if (!shareOpen && !joinOpen) { stopSharedPolling(); return; }
    await refreshSharedProjects();
    renderSharedProjectsList();
    renderPresence(state.presence || []);
    refreshShareDialogMembers();
    refreshShareApprovals();
  }, 5000);
}

function stopSharedPolling() {
  if (sharedPollTimer) { clearInterval(sharedPollTimer); sharedPollTimer = null; }
}

// ---- Identidad del agente (ítem 6) ----
function equipoIdOf(p) {
  return (p && (p.equipo_id || p.equipoId)) || projectShareState.equipo_id || "";
}

function mySessionOf(p) {
  return (p && p.yo) || null;
}

function isMyMember(m, yo) {
  if (!yo) return false;
  // Solo user_name: el backend entrega yo={user_name} (el agent_name
  // queda desactualizado tras un renombre y no sirve para identificar).
  return String(m.user_name || "") === String(yo.user_name || "");
}

function memberAgentHtml(m) {
  const a = m.agente || null;
  if (a && a.nombre) {
    return `<span class="member-agent"><i class="agent-dot" style="background:${esc(a.color || "#a78bfa")}"></i>${esc(a.nombre)}</span>`;
  }
  return "";
}

async function renameMyAgent(carpeta, proyecto) {
  const p = (state.sharedProjects || []).find(x => x.carpeta === carpeta && (!x.proyecto || x.proyecto === proyecto));
  const yo = mySessionOf(p);
  const actual = (yo && yo.agent_name) || localStorage.getItem("atlantis_agent_name") || "";
  const nuevo = prompt("Nuevo nombre de tu agente:", actual);
  if (nuevo === null) return;
  const nombre = nuevo.trim();
  if (!nombre) { toast("El nombre no puede quedar vacío."); return; }
  try {
    const res = await api(`/api/proyectos/${encodeURIComponent(carpeta)}/agente/renombrar`, {
      method: "POST",
      body: JSON.stringify({ equipo_id: equipoIdOf(p), nuevo_nombre: nombre, proyecto })
    });
    if (!res.ok) throw new Error(res.error || "No se pudo renombrar");
    const nuevoNombre = (res.agente && res.agente.nombre) || nombre;
    localStorage.setItem("atlantis_agent_name", nuevoNombre);
    toast(`✓ Tu agente ahora se llama ${nuevoNombre}`);
    await refreshSharedProjects();
    renderSharedProjectsList();
    refreshShareDialogMembers();
  } catch (err) {
    toast(`Error al renombrar: ${err.message}`);
  }
}

// ---- Aprobación dual (ítem 8) ----
function approvalStatusText(data) {
  if (!data) return "Sin aprobaciones todavía.";
  const n = (data.aprobaciones || []).length;
  if (data.estado === "terminado") return `Terminado ✓${data.resumen ? ` — ${data.resumen}` : ""}`;
  if (data.estado === "terminando" || data.ambos) return "Ambos aprobaron → los agentes están terminando.";
  if (n) return `Esperando al otro (${n} de 2).${data.falta && data.falta.length ? ` Falta: ${data.falta.join(", ")}` : ""}`;
  return "Esperando aprobaciones.";
}

async function loadApprovals(carpeta, proyecto) {
  try {
    return await api(`/api/proyectos/${encodeURIComponent(carpeta)}/aprobaciones?proyecto=${encodeURIComponent(proyecto || "general")}`);
  } catch (_) {
    return null;
  }
}

async function refreshShareApprovals() {
  if (!projectShareTarget || !$("projectShareDialog")?.open) return;
  const { carpeta, proyecto } = projectShareTarget;
  const data = await loadApprovals(carpeta, proyecto);
  const statusEl = $("approvalStatusText");
  if (statusEl) statusEl.textContent = approvalStatusText(data);
  const listEl = $("approvalList");
  if (listEl) {
    const aps = (data && data.aprobaciones) || [];
    listEl.innerHTML = aps.length ? aps.map(a => `<div class="approval-item">👍 <strong>${esc(a.usuario || "?")}</strong>${a.nota ? ` — ${esc(a.nota)}` : ""}<span class="muted">${a.fecha ? ` · ${esc(a.fecha)}` : ""}</span></div>`).join("") : `<div class="muted">Nadie aprobó todavía.</div>`;
  }
}

async function approveSharedProject(carpeta, proyecto, nota) {
  try {
    const res = await api(`/api/proyectos/${encodeURIComponent(carpeta)}/aprobar`, { method: "POST", body: JSON.stringify({ proyecto: proyecto || "general", nota: nota || "" }) });
    if (!res.ok) throw new Error(res.error || "No se pudo aprobar");
    toast("👍 ¡Te gusta! " + approvalStatusText(res));
    await refreshSharedProjects();
    renderSharedProjectsList();
    refreshShareApprovals();
  } catch (err) { toast(`Error al aprobar: ${err.message}`); }
}

async function withdrawApproval(carpeta, proyecto) {
  try {
    const res = await api(`/api/proyectos/${encodeURIComponent(carpeta)}/retirar-aprobacion`, { method: "POST", body: JSON.stringify({ proyecto: proyecto || "general" }) });
    if (!res.ok) throw new Error(res.error || "No se pudo retirar");
    toast("Aprobación retirada.");
    await refreshSharedProjects();
    renderSharedProjectsList();
    refreshShareApprovals();
  } catch (err) { toast(`Error al retirar: ${err.message}`); }
}

function handleAprobacionUpdate(event) {
  if ($("projectShareDialog")?.open && projectShareTarget && projectShareTarget.carpeta === event.carpeta) {
    refreshShareApprovals();
  }
  if ($("projectJoinDialog")?.open) {
    refreshSharedProjects().then(renderSharedProjectsList);
  }
}

// ---- Conflictos (ítem 2) ----
function conflictProyecto() {
  if (projectShareTarget) return projectShareTarget.proyecto || "general";
  const tab = state.openTabs[state.activeTabIdx];
  if (tab && tab.project && tab.project !== "__raiz__") return tab.project;
  return "general";
}

function openConflictDialogForFile(ruta, conflicto_archivo, proyecto) {
  openConflictDialog(ruta, conflicto_archivo, proyecto);
}

async function openConflictDialog(ruta, conflicto_archivo, proyecto) {
  conflictState.proyecto = proyecto || conflictProyecto();
  conflictState.actual = null;
  if ($("conflictDiffView")) $("conflictDiffView").hidden = true;
  if ($("conflictList")) $("conflictList").innerHTML = `<div class="tree-empty">Cargando conflictos…</div>`;
  $("conflictDialog").showModal();
  await loadConflicts();
  if (ruta) {
    const item = (conflictState.lista || []).find(c => c.ruta === ruta && (!conflicto_archivo || c.conflicto_archivo === conflicto_archivo));
    if (item) openConflictDiff(item);
  }
}

async function loadConflicts() {
  try {
    const res = await api(`/api/conflictos?proyecto=${encodeURIComponent(conflictState.proyecto)}`);
    conflictState.lista = (res && res.conflictos) || [];
  } catch (err) {
    conflictState.lista = [];
    if ($("conflictList")) $("conflictList").innerHTML = `<div class="tree-empty">Error: ${esc(err.message)}</div>`;
    return;
  }
  renderConflictList();
}

function renderConflictList() {
  const box = $("conflictList");
  if (!box) return;
  const list = conflictState.lista || [];
  if (!list.length) { box.innerHTML = `<div class="tree-empty">Sin conflictos. 🎉</div>`; return; }
  box.innerHTML = list.map((c, i) => `<div class="conflict-row">
    <div><strong>${esc(c.ruta)}</strong><div class="muted">Copia en conflicto: ${esc(c.conflicto_archivo || "")}${c.autor ? ` · ${esc(c.autor)}` : ""}</div></div>
    <button type="button" class="primary-btn sm" data-conflict-idx="${i}">Resolver</button>
  </div>`).join("");
}

function diffLineHtml(lines, otherSet) {
  return lines.map(l => {
    const diff = !otherSet.has(l);
    return `<div class="diff-line${diff ? " diff-changed" : ""}">${esc(l) || " "}</div>`;
  }).join("");
}

async function openConflictDiff(item) {
  conflictState.actual = item;
  try {
    const res = await api(`/api/conflictos/contenido?proyecto=${encodeURIComponent(conflictState.proyecto)}&ruta=${encodeURIComponent(item.ruta)}&conflicto_archivo=${encodeURIComponent(item.conflicto_archivo || "")}`);
    const mio = res.mio ?? "";
    const suyo = res.suyo ?? "";
    if ($("conflictFileName")) $("conflictFileName").textContent = item.ruta;
    const mioLines = String(mio).split("\n");
    const suyoLines = String(suyo).split("\n");
    if ($("conflictMioPane")) $("conflictMioPane").innerHTML = diffLineHtml(mioLines, new Set(suyoLines));
    if ($("conflictSuyoPane")) $("conflictSuyoPane").innerHTML = diffLineHtml(suyoLines, new Set(mioLines));
    if ($("conflictMezclaTextarea")) $("conflictMezclaTextarea").value = mio;
    if ($("conflictDiffView")) $("conflictDiffView").hidden = false;
  } catch (err) {
    toast(`No se pudo cargar el conflicto: ${err.message}`);
  }
}

async function resolveConflict(eleccion) {
  const item = conflictState.actual;
  if (!item) { toast("Elige un conflicto primero."); return; }
  let contenido;
  if (eleccion === "mezcla") contenido = $("conflictMezclaTextarea").value;
  try {
    const res = await api("/api/conflictos/resolver", {
      method: "POST",
      body: JSON.stringify({ proyecto: conflictState.proyecto, ruta: item.ruta, conflicto_archivo: item.conflicto_archivo || "", eleccion, ...(contenido !== undefined ? { contenido } : {}) })
    });
    if (!res.ok) throw new Error(res.error || "No se pudo resolver");
    toast(`✓ Conflicto resuelto en ${item.ruta}`);
    const tab = state.openTabs.find(t => t.path === item.ruta);
    if (tab) await reloadTabFromServer(tab);
    if ($("conflictDiffView")) $("conflictDiffView").hidden = true;
    conflictState.actual = null;
    await loadConflicts();
    if (state.fileNotice && state.fileNotice.ruta === item.ruta) hideFileNoticeBar();
  } catch (err) {
    toast(`Error al resolver: ${err.message}`);
  }
}

// ---- Preview (ítem 7) ----
function previewFolderDefault() {
  const tab = state.openTabs[state.activeTabIdx];
  if (tab && tab.path) return tab.path.split("/")[0] || "";
  return "";
}

function previewProyectoDefault() {
  const tab = state.openTabs[state.activeTabIdx];
  if (tab && tab.project && tab.project !== "__raiz__") return tab.project;
  return "general";
}

function previewIframeSrc() {
  const p = state.preview;
  if (!p.activo) return "";
  const base = p.tipo === "script" ? `/api/preview/app/${encodeURIComponent(p.carpeta)}/` : `/api/preview/sitio/${encodeURIComponent(p.carpeta)}/`;
  // El iframe usa EL MISMO proyecto con el que se inició el preview
  // (si difiere, el backend no encuentra el preview activo).
  return `${HUB_BASE}${base}?proyecto=${encodeURIComponent(p.proyecto || "general")}`;
}

function renderPreviewState() {
  const st = $("previewStatusLine");
  const frame = $("previewFrame");
  if (st) {
    st.textContent = state.preview.activo
      ? `Activo · ${state.preview.tipo || ""} · ${state.preview.url || ""}${state.preview.puerto ? ` (puerto ${state.preview.puerto})` : ""}`
      : "Preview detenido.";
  }
  if (frame) {
    const src = previewIframeSrc();
    if (src && frame.getAttribute("src") !== src) frame.setAttribute("src", src);
    if (!src) frame.removeAttribute("src");
  }
}

async function previewStart() {
  const carpeta = ($("previewFolderInput").value || "").trim() || previewFolderDefault();
  const proyecto = previewProyectoDefault();
  try {
    const res = await api("/api/preview/iniciar", { method: "POST", body: JSON.stringify({ proyecto, carpeta }) });
    if (!res.ok) throw new Error(res.error || "No se pudo iniciar el preview");
    state.preview = { activo: !!res.activo, tipo: res.tipo || "", url: res.url || "", puerto: res.puerto || 0, entrada: res.entrada || "", carpeta, proyecto };
    renderPreviewState();
    startPreviewPolling();
    toast("▶ Preview iniciado");
  } catch (err) { toast(`Error al iniciar preview: ${err.message}`); }
}

async function previewStop() {
  const { proyecto, carpeta } = state.preview;
  try {
    await api("/api/preview/detener", { method: "POST", body: JSON.stringify({ proyecto: proyecto || "general", carpeta: carpeta || "" }) });
  } catch (err) { toast(`Error al detener: ${err.message}`); }
  state.preview.activo = false;
  renderPreviewState();
  stopPreviewPolling();
}

async function previewReload() {
  const frame = $("previewFrame");
  if (frame) { const src = previewIframeSrc(); if (src) frame.setAttribute("src", src); }
  await pollPreviewEstado();
}

async function previewShare() {
  const { proyecto, carpeta } = state.preview;
  try {
    const res = await api("/api/preview/compartir", { method: "POST", body: JSON.stringify({ proyecto: proyecto || "general", carpeta: carpeta || "" }) });
    if (!res.ok || !res.url) throw new Error(res.error || "No se pudo compartir el preview");
    await navigator.clipboard.writeText(res.url).catch(() => {});
    toast(`✓ Enlace de preview copiado: ${res.url}`);
  } catch (err) { toast(`Error al compartir preview: ${err.message}`); }
}

async function pollPreviewEstado() {
  const { proyecto, carpeta } = state.preview;
  if (!carpeta) return;
  try {
    const res = await api(`/api/preview/estado?proyecto=${encodeURIComponent(proyecto || "general")}&carpeta=${encodeURIComponent(carpeta)}`);
    state.preview.activo = !!res.activo;
    if (res.tipo) state.preview.tipo = res.tipo;
    if (res.url) state.preview.url = res.url;
    if (res.puerto) state.preview.puerto = res.puerto;
    if (res.entrada) state.preview.entrada = res.entrada;
    renderPreviewState();
    const out = $("previewOutput");
    if (out) out.textContent = (res.salida || []).join("\n");
  } catch (_) {}
}

function startPreviewPolling() {
  stopPreviewPolling();
  previewPollTimer = setInterval(() => {
    if ($("previewPanel")?.hidden) { stopPreviewPolling(); return; }
    pollPreviewEstado();
  }, 3000);
}

function stopPreviewPolling() {
  if (previewPollTimer) { clearInterval(previewPollTimer); previewPollTimer = null; }
}

function maybeReloadPreview(ev) {
  if (!state.preview.activo || state.preview.tipo !== "estatico") return;
  if ($("previewPanel")?.hidden) return;
  const carpetaEv = String(ev.ruta || "").split("/")[0];
  if (carpetaEv && carpetaEv === state.preview.carpeta) {
    const frame = $("previewFrame");
    if (frame) { const src = previewIframeSrc(); if (src) frame.setAttribute("src", src); }
  }
}

function openPreviewPanel() {
  const input = $("previewFolderInput");
  if (input && !input.value) input.value = previewFolderDefault();
  renderPreviewState();
  if (state.preview.activo) startPreviewPolling();
}

// ---- Cursores en vivo (ítem 3) ----
function ensureLiveCursorLayer() {
  const container = $("monacoEditorContainer");
  if (!container) return null;
  if (!container.style.position) container.style.position = "relative";
  let layer = $("liveCursorLayer");
  if (!layer) {
    layer = document.createElement("div");
    layer.id = "liveCursorLayer";
    layer.className = "live-cursor-layer";
    container.appendChild(layer);
  }
  return layer;
}

function liveCursorColor(name, fallback) {
  if (fallback) return fallback;
  let h = 0;
  for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const palette = ["#22d3ee", "#a78bfa", "#f472b6", "#fbbf24", "#34d399", "#60a5fa"];
  return palette[h % palette.length];
}

function ensureLiveCursorStyle(name, color) {
  if (!liveCursorStyleEl) {
    liveCursorStyleEl = document.createElement("style");
    liveCursorStyleEl.id = "liveCursorStyles";
    document.head.appendChild(liveCursorStyleEl);
  }
  const cls = "live-sel-" + [...String(name)].map(c => c.charCodeAt(0).toString(36)).join("");
  if (!liveCursorStyleEl.textContent.includes(cls)) {
    liveCursorStyleEl.textContent += `\n.${cls} { background: ${color}33; }\n`;
  }
  return cls;
}

function clearLiveCursors() {
  const layer = $("liveCursorLayer");
  if (layer) layer.innerHTML = "";
  if (monacoEditor && liveCursorDecorations.size) {
    try { monacoEditor.deltaDecorations([...liveCursorDecorations.values()].flat(), []); } catch (_) {}
  }
  liveCursorDecorations.clear();
}

function renderLiveCursors() {
  const tab = state.openTabs[state.activeTabIdx];
  const layer = ensureLiveCursorLayer();
  if (!layer) return;
  if (!monacoEditor || !tab) { clearLiveCursors(); return; }
  const myName = currentUserName();
  const users = (state.presence || []).filter(u => u && u.nombre && u.nombre !== myName && u.file && (u.file === tab.path || String(u.file).endsWith("/" + tab.path) || tab.path.endsWith("/" + String(u.file))));
  const seen = new Set(users.map(u => u.nombre));
  for (const [name, ids] of [...liveCursorDecorations.entries()]) {
    if (!seen.has(name)) {
      try { monacoEditor.deltaDecorations(ids, []); } catch (_) {}
      liveCursorDecorations.delete(name);
    }
  }
  layer.innerHTML = "";
  for (const u of users) {
    const color = u.color || liveCursorColor(u.nombre);
    const cur = u.cursor || {};
    const line = cur.line ?? cur.lineNumber;
    const col = cur.col ?? cur.column;
    if (typeof line === "number" && typeof col === "number") {
      try {
        const pos = monacoEditor.getScrolledVisiblePosition({ lineNumber: line, column: col });
        if (pos) {
          const div = document.createElement("div");
          div.className = "live-cursor-label";
          div.style.background = color;
          div.style.left = pos.left + "px";
          div.style.top = pos.top + "px";
          div.textContent = u.nombre;
          layer.appendChild(div);
        }
      } catch (_) {}
    }
    const sel = u.selection;
    if (sel && typeof monaco !== "undefined" && typeof sel.startLineNumber === "number") {
      try {
        const cls = ensureLiveCursorStyle(u.nombre, color);
        const range = new monaco.Range(sel.startLineNumber, sel.startColumn, sel.endLineNumber, sel.endColumn);
        const prev = liveCursorDecorations.get(u.nombre) || [];
        const next = monacoEditor.deltaDecorations(prev, [{ range, options: { inlineClassName: cls, stickiness: 1 } }]);
        liveCursorDecorations.set(u.nombre, next);
      } catch (_) {}
    } else {
      const prev = liveCursorDecorations.get(u.nombre);
      if (prev) {
        try { monacoEditor.deltaDecorations(prev, []); } catch (_) {}
        liveCursorDecorations.delete(u.nombre);
      }
    }
  }
}

// Abrir Compartir del proyecto activo, o Unirse si no hay archivo de proyecto abierto.
function openShareForActiveTab() {
  const tab = state.openTabs[state.activeTabIdx];
  if (tab && tab.project && tab.project !== "__raiz__" && String(tab.path || "").includes("/")) {
    openProjectShare(tab.project, String(tab.path).split("/")[0]);
  } else {
    openProjectJoin();
  }
}


// ---------------- Compartir por proyecto (co-edición por carpeta) ----------------
// Cada proyecto (carpeta) tiene SU equipo: compartir X no comparte Y.
// El equipo es abierto: generar un código nuevo para el mismo proyecto
// une al equipo existente, sin crear otro equipo.

async function refreshSharedProjects() {
  try {
    const res = await api("/api/proyectos/compartidos");
    state.sharedProjects = Array.isArray(res.proyectos) ? res.proyectos : [];
  } catch (_) {
    state.sharedProjects = state.sharedProjects || [];
  }
  return state.sharedProjects;
}

function sharedProjectForPath(path, proyecto) {
  const carpeta = String(path || "").split("/")[0];
  if (!carpeta) return null;
  return (state.sharedProjects || []).find(p =>
    p.carpeta === carpeta && (!proyecto || !p.proyecto || p.proyecto === proyecto)) || null;
}

function renderShareProjectMembers(miembros, ctx) {
  const box = $("shareProjectMembers");
  if (!box) return;
  const list = Array.isArray(miembros) ? miembros : [];
  if (!list.length) { box.hidden = true; box.innerHTML = ""; return; }
  const yo = (ctx && ctx.yo) || null;
  box.hidden = false;
  box.innerHTML = list.map(m => {
    const mine = isMyMember(m, yo);
    return `<div class="team-member-row"><span class="member-dot" style="background:${esc((m.agente && m.agente.color) || "#22d3ee")}"></span><span>${esc(m.user_name || m.agent_name || "?")}</span>${memberAgentHtml(m)}${m.es_host ? '<span class="badge">Anfitrión</span>' : ""}${mine ? `<button type="button" class="ghost-btn sm" data-rename-agent="${esc(ctx.carpeta)}" data-rename-proyecto="${esc(ctx.proyecto || "general")}">Renombrar</button>` : ""}</div>`;
  }).join("");
}

function refreshShareDialogMembers() {
  if (!projectShareTarget || !$("projectShareDialog")?.open) return;
  const p = (state.sharedProjects || []).find(x => x.carpeta === projectShareTarget.carpeta && (!x.proyecto || x.proyecto === projectShareTarget.proyecto));
  const syncEl = $("shareSyncStatus");
  if (syncEl) {
    const sync = p ? syncEstadoOf(p) : null;
    syncEl.innerHTML = sync ? syncStatusHtml(sync) : `<span class="muted">Sin datos de sincronización todavía.</span>`;
  }
  renderShareProjectMembers(p ? (p.miembros || []) : [], { carpeta: projectShareTarget.carpeta, proyecto: projectShareTarget.proyecto, yo: mySessionOf(p) });
}

function renderSharedProjectsList() {
  const box = $("sharedProjectsList");
  if (!box) return;
  const list = state.sharedProjects || [];
  if (!list.length) {
    box.innerHTML = `<p class="muted">Ningún proyecto compartido todavía.</p>`;
    return;
  }
  box.innerHTML = list.map(p => {
    const yo = mySessionOf(p);
    const miembros = (p.miembros || []).map(m => `${esc(m.user_name || m.agent_name || "?")}${m.agente && m.agente.nombre ? ` <span class="member-agent"><i class="agent-dot" style="background:${esc(m.agente.color || "#a78bfa")}"></i>${esc(m.agente.nombre)}</span>` : ""}${isMyMember(m, yo) ? ` <button type="button" class="ghost-btn sm" data-rename-agent="${esc(p.carpeta)}" data-rename-proyecto="${esc(p.proyecto || "general")}">Renombrar</button>` : ""}`).join(", ") || "—";
    const sync = syncEstadoOf(p);
    return `<div class="shared-project-row">
      <strong>📁 ${esc(p.carpeta)}</strong>
      <span class="muted">${esc(p.rol || "")} · ${miembros}</span>
      ${sync ? syncStatusHtml(sync) : ""}
      <div class="shared-project-approval">
        <span class="muted">¿Te gusta cómo va?</span>
        <input type="text" class="approval-note-input" placeholder="Nota opcional…">
        <button type="button" class="ghost-btn sm" data-approve-project="${esc(p.carpeta)}" data-approve-proyecto="${esc(p.proyecto || "general")}">👍 Me gusta</button>
        <button type="button" class="ghost-btn sm" data-withdraw-approval="${esc(p.carpeta)}" data-withdraw-proyecto="${esc(p.proyecto || "general")}">Retirar aprobación</button>
        <span class="approval-status muted" data-approval-status="1">Cargando…</span>
      </div>
      <div class="shared-project-actions">
        <button type="button" class="ghost-btn sm" data-open-conflicts="${esc(p.proyecto || "general")}">Conflictos</button>
        <button type="button" class="ghost-btn sm" data-leave-project="${esc(p.carpeta)}" data-leave-proyecto="${esc(p.proyecto || "general")}">Salir</button>
      </div>
    </div>`;
  }).join("");
  // Estado de aprobación por fila (async, no bloquea el listado).
  const statusEls = box.querySelectorAll("[data-approval-status]");
  list.forEach(async (p, i) => {
    const el = statusEls[i];
    if (!el) return;
    const data = await loadApprovals(p.carpeta, p.proyecto || "general");
    el.textContent = approvalStatusText(data);
  });
}

function openProjectShare(proyecto, carpeta) {
  projectShareTarget = { proyecto: proyecto || "general", carpeta };
  projectShareState = { equipo_id: "", code: "", join_link: "", expira_en: null };
  $("shareDialogTitle").textContent = `Compartir proyecto «${carpeta}»`;
  $("shareProjectCode").textContent = "— — —";
  $("shareProjectStatus").textContent = "";
  $("shareProjectMembers").hidden = true;
  $("shareProjectMembers").innerHTML = "";
  $("shareLeaveStep").hidden = true;
  if ($("shareJoinLinkRow")) $("shareJoinLinkRow").hidden = true;
  if ($("shareJoinLinkInput")) $("shareJoinLinkInput").value = "";
  if ($("shareExpiryText")) $("shareExpiryText").textContent = "";
  if ($("shareCodesList")) $("shareCodesList").innerHTML = "";
  const ya = (state.sharedProjects || []).find(p => p.carpeta === carpeta && (!p.proyecto || p.proyecto === (proyecto || "general")));
  if (ya) {
    if (ya.equipo_id) projectShareState.equipo_id = ya.equipo_id;
    $("shareProjectStatus").textContent = `Ya compartido con ${(ya.miembros || []).length} miembro(s). Genera un código nuevo cuando quieras invitar a más gente: se une al mismo equipo.`;
    renderShareProjectMembers(ya.miembros || [], { carpeta, proyecto: proyecto || "general", yo: mySessionOf(ya) });
    $("shareLeaveStep").hidden = false;
  }
  $("projectShareDialog").showModal();
  refreshShareCodes();
  refreshShareApprovals();
  refreshShareDialogMembers();
  startSharedPolling();
}

async function generateProjectCode() {
  if (!projectShareTarget) return;
  const btn = $("btnGenProjectCode");
  btn.disabled = true;
  try {
    const expira = parseInt($("shareExpirySelect").value || "1440", 10);
    const res = await api(`/api/proyectos/${encodeURIComponent(projectShareTarget.carpeta)}/compartir`, {
      method: "POST",
      body: JSON.stringify({
        proyecto: projectShareTarget.proyecto,
        agent_name: `Hermes-${currentUserName()}`,
        user_name: currentUserName(),
        expira_minutos: expira,
      }),
    });
    if (!res.ok) throw new Error(res.error || "No se pudo compartir");
    projectShareState.equipo_id = res.equipo_id || projectShareState.equipo_id;
    projectShareState.code = res.code || "";
    projectShareState.join_link = res.join_link || "";
    projectShareState.expira_en = res.expira_en ?? res.expires_at ?? null;
    state.lastShare = { ...projectShareState };
    $("shareProjectCode").textContent = res.code || "— — —";
    $("shareProjectStatus").textContent = res.nuevo
      ? "Equipo creado. Comparte este código: quien lo use entra a ESTE proyecto."
      : "Código nuevo para el mismo equipo. Comparte este código: quien lo use entra a ESTE proyecto.";
    if ($("shareExpiryText")) $("shareExpiryText").textContent = projectShareState.expira_en ? `Expira: ${new Date(projectShareState.expira_en).toLocaleString("es")}` : "Sin expiración";
    if ($("shareJoinLinkRow")) $("shareJoinLinkRow").hidden = !projectShareState.join_link;
    if ($("shareJoinLinkInput")) $("shareJoinLinkInput").value = projectShareState.join_link || "";
    renderShareProjectMembers(res.miembros || [], { carpeta: projectShareTarget.carpeta, proyecto: projectShareTarget.proyecto, yo: null });
    $("shareLeaveStep").hidden = false;
    await refreshSharedProjects();
    refreshShareDialogMembers();
    await refreshShareCodes();
  } catch (err) {
    $("shareProjectStatus").textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function refreshShareCodes() {
  const box = $("shareCodesList");
  if (!box || !projectShareTarget) return;
  box.innerHTML = `<div class="muted">Cargando códigos…</div>`;
  try {
    const res = await api(`/api/proyectos/${encodeURIComponent(projectShareTarget.carpeta)}/codigos?proyecto=${encodeURIComponent(projectShareTarget.proyecto)}`);
    const codigos = (res && res.codigos) || [];
    if (!codigos.length) { box.innerHTML = `<div class="muted">Sin códigos todavía. Genera uno arriba.</div>`; return; }
    box.innerHTML = codigos.map(c => {
      const estado = c.estado || (c.revocado ? "revocado" : "vigente");
      return `<div class="code-row"><code>${esc(c.code)}</code><span class="muted">${esc(estado)}${c.expira_en ? ` · expira ${new Date(c.expira_en).toLocaleString("es")}` : " · sin expiración"}</span>${estado === "vigente" ? `<button type="button" class="danger-btn sm" data-revoke-code="${esc(c.code)}">Revocar</button>` : ""}</div>`;
    }).join("");
  } catch (err) {
    box.innerHTML = `<div class="muted">No se pudieron cargar los códigos: ${esc(err.message)}</div>`;
  }
}

async function revokeShareCode(code) {
  if (!projectShareTarget) return;
  if (!confirm(`¿Revocar el código ${code}?`)) return;
  try {
    const shared = (state.sharedProjects || []).find(p => p.carpeta === projectShareTarget.carpeta);
    const res = await api(`/api/proyectos/${encodeURIComponent(projectShareTarget.carpeta)}/codigos/revocar`, {
      method: "POST",
      body: JSON.stringify({ equipo_id: projectShareState.equipo_id || equipoIdOf(shared), code, proyecto: projectShareTarget.proyecto || "general" })
    });
    if (!res.ok) throw new Error(res.error || "No se pudo revocar");
    toast(`Código ${code} revocado.`);
    await refreshShareCodes();
  } catch (err) {
    toast(`Error al revocar: ${err.message}`);
  }
}

async function openProjectJoin() {
  $("joinProjectStatus").textContent = "";
  await refreshSharedProjects();
  renderSharedProjectsList();
  $("projectJoinDialog").showModal();
  startSharedPolling();
}

function extractJoinCode(raw) {
  const v = String(raw || "").trim();
  if (!v) return "";
  try {
    if (/^https?:\/\//i.test(v)) {
      const u = new URL(v);
      const fromParam = u.searchParams.get("code") || u.searchParams.get("codigo") || "";
      if (fromParam) return fromParam.toUpperCase();
      const m = u.pathname.match(/(ATL-[A-Z0-9]+)/i);
      if (m) return m[1].toUpperCase();
      const tail = u.pathname.split("/").filter(Boolean).pop() || "";
      if (tail) return tail.toUpperCase();
    }
  } catch (_) {}
  const m = v.match(/(ATL-[A-Z0-9]+)/i);
  if (m) return m[1].toUpperCase();
  return v.toUpperCase();
}

async function doJoinProject() {
  const btn = $("btnDoJoinProject");
  btn.disabled = true;
  try {
    const code = extractJoinCode($("joinProjectCodeInput").value || "");
    const carpeta = ($("joinProjectFolderInput").value || "").trim();
    if (!code || !carpeta) throw new Error("Escribe el código (o pega el enlace de unión) y la carpeta del proyecto.");
    const res = await api("/api/proyectos/unirse", {
      method: "POST",
      body: JSON.stringify({
        code, carpeta, proyecto: "general",
        agent_name: `Hermes-${currentUserName()}`,
        user_name: currentUserName(),
      }),
    });
    if (!res.ok) throw new Error(res.error || "No se pudo unir");
    $("joinProjectStatus").textContent = `✓ Te uniste al proyecto «${carpeta}». Los archivos se sincronizan en vivo.`;
    toast(`✓ Te uniste al proyecto «${carpeta}»`);
    await refreshSharedProjects();
    renderSharedProjectsList();
    await loadTree().catch(() => {});
  } catch (err) {
    $("joinProjectStatus").textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function leaveSharedProject(carpeta, proyecto) {
  try {
    const res = await api(`/api/proyectos/${encodeURIComponent(carpeta)}/salir`, {
      method: "POST",
      body: JSON.stringify({ proyecto: proyecto || "general" }),
    });
    if (!res.ok) throw new Error(res.error || "No se pudo salir");
    toast(`Saliste del proyecto «${carpeta}»`);
    await refreshSharedProjects();
    renderSharedProjectsList();
    if ($("projectShareDialog")?.open) $("projectShareDialog").close();
  } catch (err) {
    toast(`Error al salir: ${err.message}`);
  }
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
  html = html.replace(/^&gt;\s*\[!NOTE\]\s*(.*$)/gim, "<blockquote style='border-left-color: #8b7cf6;'><b>NOTE:</b> $1</blockquote>");
  html = html.replace(/^&gt;\s*\[!TIP\]\s*(.*$)/gim, "<blockquote style='border-left-color: #10b981;'><b>TIP:</b> $1</blockquote>");
  html = html.replace(/^&gt;\s*\[!WARNING\]\s*(.*$)/gim, "<blockquote style='border-left-color: #f59e0b;'><b>ADVERTENCIA:</b> $1</blockquote>");
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

// ---------------- MCP Builder: crear servidores MCP reales (top-level) ----------------
// ---------------- MCP Builder: crear servidores MCP reales ----------------
const MCP_EXAMPLE_TOOL = () => ({
  name: "saludar",
  description: "Saluda a alguien por su nombre",
  params: JSON.stringify({ nombre: { type: "string", description: "A quién saludar" } }, null, 2),
  handler: 'return f"Hola, {nombre}!"'
});

function mcpToolCardHTML(tool, idx) {
  return `<div class="mcp-tool-card" data-idx="${idx}">
    <div class="mcp-tool-head">
      <input type="text" class="text-input mcp-tool-name" placeholder="nombre_herramienta" value="${esc(tool.name || "")}" maxlength="40">
      <button type="button" class="icon-btn sm mcp-tool-del" title="Quitar herramienta">×</button>
    </div>
    <input type="text" class="text-input mcp-tool-desc" placeholder="Descripción de la herramienta" value="${esc(tool.description || "")}" maxlength="120" style="margin-bottom:6px;">
    <label class="field-label">Parámetros (JSON Schema)</label>
    <textarea class="text-input mcp-params-json" spellcheck="false">${esc(tool.params || "")}</textarea>
    <label class="field-label" style="margin-top:6px;">Código Python (usa los parámetros, termina con return)</label>
    <textarea class="text-input mcp-handler-code" spellcheck="false">${esc(tool.handler || "")}</textarea>
  </div>`;
}

function renderMcpWizardTools(tools) {
  const box = $("mcpWizardTools");
  if (!box) return;
  box.innerHTML = tools.map((t, i) => mcpToolCardHTML(t, i)).join("");
  box.querySelectorAll(".mcp-tool-del").forEach(btn => {
    btn.addEventListener("click", () => {
      btn.closest(".mcp-tool-card")?.remove();
    });
  });
}

function collectMcpWizardTools() {
  const tools = [];
  document.querySelectorAll("#mcpWizardTools .mcp-tool-card").forEach(card => {
    const paramsRaw = card.querySelector(".mcp-params-json")?.value.trim() || "{}";
    let params;
    try { params = JSON.parse(paramsRaw); }
    catch { throw new Error(`Parámetros JSON inválidos en '${card.querySelector(".mcp-tool-name")?.value || "?"}'`); }
    tools.push({
      name: card.querySelector(".mcp-tool-name")?.value.trim() || "",
      description: card.querySelector(".mcp-tool-desc")?.value.trim() || "",
      params_schema: params,
      handler: card.querySelector(".mcp-handler-code")?.value || ""
    });
  });
  return tools;
}

async function openMcpWizard() {
  const nameInput = $("mcpNameInput");
  const descInput = $("mcpDescInput");
  if (nameInput) nameInput.value = "";
  if (descInput) descInput.value = "";
  renderMcpWizardTools([MCP_EXAMPLE_TOOL()]);
  // Disponibilidad del paquete mcp: honesto, no botón muerto.
  try {
    const res = await api("/api/mcps").catch(() => null);
    const ok = !!(res && res.mcp_available);
    if ($("mcpWizardNoPkg")) $("mcpWizardNoPkg").hidden = ok;
    if ($("btnCreateMcp")) $("btnCreateMcp").disabled = !ok;
  } catch { /* si falla la consulta, el backend dirá la verdad al crear */ }
  $("mcpWizardDialog").showModal();
  setTimeout(() => nameInput && nameInput.focus(), 100);
}

async function createMcp() {
  const name = $("mcpNameInput")?.value.trim() || "";
  const description = $("mcpDescInput")?.value.trim() || "";
  if (!name) { toast("Ponle un nombre al MCP."); return; }
  let tools;
  try { tools = collectMcpWizardTools(); }
  catch (err) { toast(err.message); return; }
  if (!tools.length) { toast("Define al menos una herramienta."); return; }
  const btn = $("btnCreateMcp");
  if (btn) btn.disabled = true;
  try {
    const res = await api("/api/mcps", {
      method: "POST",
      body: JSON.stringify({ name, description, tools })
    });
    if (!res.ok) throw new Error(res.error || "No se pudo crear");
    $("mcpWizardDialog").close();
    toast(`✓ MCP '${res.mcp.name}' creado con ${res.mcp.tools.length} herramienta(s)`);
    switchActivityView("plugins");
    switchPluginsSubtab("mcp");
    await loadAntigravityData();
    await refreshMyMcps();
  } catch (err) {
    toast(`Error: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function bindEvents() {
  // Activity Bar clicks
  document.querySelectorAll(".activity").forEach(btn => {
    btn.addEventListener("click", () => switchActivityView(btn.dataset.view));
  });

  $("commandBar").addEventListener("click", openCommandPalette);
  $("btnGit").addEventListener("click", () => switchActivityView("source"));
  $("btnShare").addEventListener("click", openShareForActiveTab);
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
      setItemVisibility("cmShareProject", false);
    } else if (folderEl) {
      contextTarget = { type: "folder", path: folderEl.dataset.folder, project: "" };
      setItemVisibility("cmOpen", false);
      setItemVisibility("cmOpenSystem", true);
      setItemVisibility("cmRename", true);
      setItemVisibility("cmDelete", true);
      // "Compartir proyecto" solo en carpetas de proyecto: <sala>/<carpeta>
      // (2 partes, sala real — no la raíz del workspace).
      const partes = String(folderEl.dataset.folder || "").split("/");
      const sala = partes.length === 2
        ? (state.tree || []).find(p => (p.nombre || "") === partes[0]) : null;
      setItemVisibility("cmShareProject", !!(sala && !sala.es_raiz));
    } else {
      contextTarget = { type: "root", path: "", project: "" };
      setItemVisibility("cmOpen", false);
      setItemVisibility("cmOpenSystem", true);
      setItemVisibility("cmRename", false);
      setItemVisibility("cmDelete", false);
      setItemVisibility("cmShareProject", false);
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
  $("cmShareProject")?.addEventListener("click", () => {
    if (contextTarget.type !== "folder" || !contextTarget.path) return;
    const partes = String(contextTarget.path).split("/");
    if (partes.length !== 2) return;
    openProjectShare(partes[0], partes[1]);
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

  textarea.addEventListener("click", () => { updateLineNumbers(); sendPresenceThrottled(); });
  textarea.addEventListener("keyup", () => { updateLineNumbers(); sendPresenceThrottled(); });

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
      const previewPanelEl = $("previewPanel");
      const termContentEl = document.querySelector(".terminal-content");
      if (previewPanelEl && termContentEl) {
        const showPreview = which === "preview";
        previewPanelEl.hidden = !showPreview;
        termContentEl.hidden = showPreview;
        if (showPreview) openPreviewPanel();
        else stopPreviewPolling();
      }
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
        const myAgentOut = localStorage.getItem("atlantis_agent_name") || "Hermes";
        const sharedCount = (state.sharedProjects || []).length;
        const pairedTxt = sharedCount ? `${sharedCount} proyecto(s) compartido(s)` : "sin proyectos compartidos";
        printTerminalOutput(`<div><span class="path">[Salida de Hub Atlantis Studio]</span></div>
<div>· Puerto PTY & HTTP: ${HUB_BASE || "http://127.0.0.1:8787"}</div>
<div>· Proveedor IA: Tu Hermes local (sin claves, sin nube)</div>
<div>· Antigravity Engine: ${skillsCount} skills · ${pluginsCount} plugins cargados</div>
<div>· Equipo por proyecto: ${pairedTxt}</div>`);
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

  $("inviteBtn").addEventListener("click", openShareForActiveTab);
  $("messageForm").addEventListener("submit", sendMessage);
  $("messageInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  $("messageInput").addEventListener("input", autosizeComposer);
  $("stopBtn").addEventListener("click", stopActiveStream);

  // Copiar código y tarjetas de sugerencia (delegación: sobrevive re-renders).
  $("messages").addEventListener("click", (e) => {
    const copyBtn = e.target.closest(".md-copy");
    if (copyBtn) {
      const idx = parseInt(copyBtn.dataset.mdcopy || "-1", 10);
      const code = (typeof mdGetCodeBlock === "function") ? mdGetCodeBlock(idx) : "";
      const done = () => {
        copyBtn.textContent = "✓ Copiado";
        setTimeout(() => { copyBtn.textContent = "Copiar"; }, 1500);
      };
      const legacyCopy = () => {
        const ta = document.createElement("textarea");
        ta.value = code;
        ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(); }
        catch (_) { toast("No se pudo copiar"); }
        ta.remove();
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(code).then(done).catch(legacyCopy);
      } else {
        legacyCopy();
      }
      return;
    }
    // Copiar mensaje completo del asistente
    const msgCopy = e.target.closest(".msg-copy");
    if (msgCopy) {
      const article = msgCopy.closest("[data-msgid]");
      const msgId = article ? article.dataset.msgid : "";
      const msg = state.messages.find(m => String(m.id || "") === String(msgId));
      const text = msg ? String(msg.text || "") : "";
      if (!text) { toast("Nada que copiar"); return; }
      const done = () => toast("✓ Mensaje copiado");
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => toast("No se pudo copiar", "err"));
      } else {
        toast("No se pudo copiar", "err");
      }
      return;
    }
    const sug = e.target.closest("[data-suggest]");
    if (sug) handleSuggestion(sug.dataset.suggest);
  });

  // Composer tools
  $("btnAttachContext").addEventListener("click", () => {
    if (state.activeTabIdx >= 0) {
      const tab = state.openTabs[state.activeTabIdx];
      state.contextFile = { title: tab.title, content: tab.content || "" };
      renderContextChip();
      toast(`📎 "${tab.title}" se adjuntará al próximo mensaje`);
      $("messageInput").focus();
    } else {
      toast("No hay ningún archivo abierto para adjuntar");
    }
  });
  $("contextChipX").addEventListener("click", () => {
    state.contextFile = null;
    renderContextChip();
  });

  $("btnMentionAgent").addEventListener("click", () => {
    const name = activeSlotName();
    $("messageInput").value += name ? ` @${name} ` : " @";
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
          author: currentUserName(),
          initials: currentUserInitials(),
          time: timeNow(),
          text: `Iniciando tarea: "${t.titulo || t.title}" asignada a ${t.asignado || "Hermes"}.`
        }, "agents");
        toast(`Ejecutando "${t.titulo || t.title}"...`);
        // Ejecución REAL con el asistente (streaming): nada simulado.
        const slot = (() => {
          const a = String(t.asignado || t.assignee || "").trim();
          const sel = $("slotSwitcherSelect");
          const names = sel ? [...sel.options].map(o => o.value) : [];
          return names.includes(a) ? a : "";
        })();
        const prompt = `Ejecuta esta tarea del proyecto:\n\nTÍTULO: ${t.titulo || t.title}\n` +
          `DESCRIPCIÓN: ${t.descripcion || t.description || "(sin descripción)"}\n\n` +
          `Trabaja sobre el workspace actual. Explica brevemente qué hiciste al terminar.`;
        streamAgent({
          prompt, slot, tag: "TAREA",
          onDone: (ok) => {
            if (ok) { t.estado = "completada"; renderTasks(); }
            toast(ok ? `✓ Tarea completada: "${t.titulo || t.title}"` : `La tarea terminó con errores.`);
          }
        });
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

  // (Diálogo Compartir a nivel app eliminado: compartir es por proyecto.)

  // Asistente Nuevo Proyecto
  document.querySelectorAll(".template-card").forEach(card => {
    card.addEventListener("click", () => {
      selectedTemplate = card.dataset.template;
      document.querySelectorAll(".template-card").forEach(c =>
        c.classList.toggle("selected", c === card));
    });
  });
  // Compartir por proyecto — diálogos (cada botón hace algo real)
  $("btnCloseProjectShare")?.addEventListener("click", () => $("projectShareDialog").close());
  $("btnCloseProjectShare2")?.addEventListener("click", () => $("projectShareDialog").close());
  $("projectShareDialog")?.addEventListener("close", () => { if (!$("projectJoinDialog")?.open) stopSharedPolling(); });
  $("btnGenProjectCode")?.addEventListener("click", generateProjectCode);
  $("btnCopyProjectCode")?.addEventListener("click", async () => {
    const code = ($("shareProjectCode").textContent || "").trim();
    if (!code || code.startsWith("—")) return;
    await navigator.clipboard.writeText(code).catch(() => {});
    toast(`Código copiado: ${code}`);
  });
  $("btnCopyJoinLink")?.addEventListener("click", async () => {
    const link = projectShareState.join_link || "";
    if (!link) { toast("Genera un código primero para tener enlace de unión."); return; }
    await navigator.clipboard.writeText(link).catch(() => {});
    toast("✓ Enlace de unión copiado");
  });
  $("shareCodesList")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-revoke-code]");
    if (btn) revokeShareCode(btn.dataset.revokeCode);
  });
  $("btnLeaveProjectShare")?.addEventListener("click", () => {
    if (projectShareTarget) leaveSharedProject(projectShareTarget.carpeta, projectShareTarget.proyecto);
  });
  $("btnCloseProjectJoin")?.addEventListener("click", () => $("projectJoinDialog").close());
  $("btnCloseProjectJoin2")?.addEventListener("click", () => $("projectJoinDialog").close());
  $("projectJoinDialog")?.addEventListener("close", () => { if (!$("projectShareDialog")?.open) stopSharedPolling(); });
  $("btnDoJoinProject")?.addEventListener("click", doJoinProject);
  $("sharedProjectsList")?.addEventListener("click", (e) => {
    const leaveBtn = e.target.closest("[data-leave-project]");
    if (leaveBtn) { leaveSharedProject(leaveBtn.dataset.leaveProject, leaveBtn.dataset.leaveProyecto); return; }
    const renameBtn = e.target.closest("[data-rename-agent]");
    if (renameBtn) { renameMyAgent(renameBtn.dataset.renameAgent, renameBtn.dataset.renameProyecto); return; }
    const approveBtn = e.target.closest("[data-approve-project]");
    if (approveBtn) {
      const row = approveBtn.closest(".shared-project-row");
      const nota = row?.querySelector(".approval-note-input")?.value.trim() || "";
      approveSharedProject(approveBtn.dataset.approveProject, approveBtn.dataset.approveProyecto, nota);
      return;
    }
    const withdrawBtn = e.target.closest("[data-withdraw-approval]");
    if (withdrawBtn) { withdrawApproval(withdrawBtn.dataset.withdrawApproval, withdrawBtn.dataset.withdrawProyecto); return; }
    const conflictBtn = e.target.closest("[data-open-conflicts]");
    if (conflictBtn) { openConflictDialog(null, null, conflictBtn.dataset.openConflicts); return; }
  });
  $("shareProjectMembers")?.addEventListener("click", (e) => {
    const renameBtn = e.target.closest("[data-rename-agent]");
    if (renameBtn) renameMyAgent(renameBtn.dataset.renameAgent, renameBtn.dataset.renameProyecto);
  });
  // Aprobación en el diálogo Compartir
  $("btnApproveProject")?.addEventListener("click", () => {
    if (!projectShareTarget) return;
    approveSharedProject(projectShareTarget.carpeta, projectShareTarget.proyecto, ($("approvalNoteInput").value || "").trim());
  });
  $("btnWithdrawApproval")?.addEventListener("click", () => {
    if (!projectShareTarget) return;
    withdrawApproval(projectShareTarget.carpeta, projectShareTarget.proyecto);
  });
  // Conflictos (desde Compartir / Unirse)
  $("btnOpenConflictsShare")?.addEventListener("click", () => openConflictDialog(null, null, projectShareTarget ? projectShareTarget.proyecto : "general"));
  $("btnOpenConflictsJoin")?.addEventListener("click", () => openConflictDialog());
  // Diálogo de conflictos
  $("btnCloseConflict")?.addEventListener("click", () => $("conflictDialog").close());
  $("btnCloseConflict2")?.addEventListener("click", () => $("conflictDialog").close());
  $("conflictList")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-conflict-idx]");
    if (!btn) return;
    const item = conflictState.lista[parseInt(btn.dataset.conflictIdx, 10)];
    if (item) openConflictDiff(item);
  });
  $("btnConflictMio")?.addEventListener("click", () => resolveConflict("mio"));
  $("btnConflictSuyo")?.addEventListener("click", () => resolveConflict("suyo"));
  $("btnConflictMezcla")?.addEventListener("click", () => resolveConflict("mezcla"));
  // Preview
  $("btnPreviewStart")?.addEventListener("click", previewStart);
  $("btnPreviewReload")?.addEventListener("click", previewReload);
  $("btnPreviewStop")?.addEventListener("click", previewStop);
  $("btnPreviewShare")?.addEventListener("click", previewShare);

  $("btnCloseProjectWizard").addEventListener("click", () => $("projectWizardDialog").close());
  $("btnCancelProjectWizard").addEventListener("click", () => $("projectWizardDialog").close());
  $("btnCreateProject").addEventListener("click", createProject);
  $("projectNameInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); createProject(); }
  });


  $("btnAddMcpTool")?.addEventListener("click", () => {
    const box = $("mcpWizardTools");
    if (!box) return;
    const tmp = document.createElement("div");
    tmp.innerHTML = mcpToolCardHTML({ name: "", description: "", params: '{\n  "arg": { "type": "string", "description": "" }\n}', handler: "return str(arg)" }, box.children.length);
    const card = tmp.firstElementChild;
    box.appendChild(card);
    card.querySelector(".mcp-tool-del")?.addEventListener("click", () => card.remove());
    card.querySelector(".mcp-tool-name")?.focus();
  });
  $("btnCloseMcpWizard")?.addEventListener("click", () => $("mcpWizardDialog").close());
  $("btnCancelMcpWizard")?.addEventListener("click", () => $("mcpWizardDialog").close());
  $("btnCreateMcp")?.addEventListener("click", createMcp);

  // Ejecutar proyecto: detección + botón
  $("btnDetectProject")?.addEventListener("click", refreshProjectRunBox);
  $("btnRunProject")?.addEventListener("click", async () => {
    if (!detectedProject || !detectedProject.run || !detectedProject.run.cmd) {
      toast("No hay un proyecto ejecutable detectado.");
      return;
    }
    const run = detectedProject.run;
    $("tabTerminal").click();
    printTerminalOutput(`<div><span class="success">▶ Ejecutando proyecto ${esc(detectedProject.etiqueta)}…</span></div>`);
    // El comando ya incluye `cd <dir> &&` cuando aplica; no pasar dir relativo como cwd.
    await executeTerminalCmd(run.cmd);
    if (run.nota) printTerminalOutput(`<div><span class="term-hint">${esc(run.nota)}</span></div>`);
    if (run.bloqueante) toast("Servidor en ejecución en segundo plano.");
  });

  // Preset dinámico: estado del hub con la base actual
  const hubBtn = $("runHubStatusBtn");
  if (hubBtn) hubBtn.dataset.cmd = `curl -s ${HUB_BASE}/api/status`;

  // Settings Modal
  $("btnCloseSettings").addEventListener("click", () => $("settingsDialog").close());
  $("btnCancelSettings")?.addEventListener("click", () => $("settingsDialog").close());
  $("btnSaveSettings").addEventListener("click", () => {
    const theme = $("settingTheme").value;
    applyTheme(theme);
    $("settingsDialog").close();
    toast("✓ Preferencias guardadas y aplicadas");
  });

  // Profile Modal
  $("btnCloseProfile").addEventListener("click", () => $("profileDialog").close());
  $("btnLogout")?.addEventListener("click", async () => {
    $("profileDialog").close();
    await logout();
    toast("Sesión cerrada.");
  });

  // Universal Backdrop Click Dismiss for all Dialogs
  document.querySelectorAll("dialog").forEach(dlg => {
    dlg.addEventListener("click", (e) => {
      // Ignora clics sintéticos (coordenadas 0,0 de .click() programático)
      if (e.clientX === 0 && e.clientY === 0) return;
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
    } else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "d") {
      const tagD = (document.activeElement?.tagName || "").toLowerCase();
      const inEditor = document.activeElement?.closest?.(".monaco-editor");
      if (!["input", "textarea"].includes(tagD) && !inEditor && !document.activeElement?.isContentEditable) {
        e.preventDefault();
        if (typeof triggerDebate === "function") triggerDebate();
      }
    } else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "i") {
      e.preventDefault();
      openShareForActiveTab();
    } else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key === ",") {
      e.preventDefault();
      $("settingsDialog")?.showModal();
    } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "n") {
      e.preventDefault();
      if (typeof openProjectWizard === "function") openProjectWizard();
    } else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "l") {
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      if (!["input", "textarea"].includes(tag) && !document.activeElement?.isContentEditable) {
        e.preventDefault();
        if ($("terminalOutput")) $("terminalOutput").innerHTML = "";
        toast("Terminal limpiada");
      }
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
  $("menuItemNewProject")?.addEventListener("click", () => { closeAll(); openProjectWizard(); });
  $("menuItemNewMcp")?.addEventListener("click", () => { closeAll(); openMcpWizard(); });
  $("menuItemOpen")?.addEventListener("click", (e) => {
    closeAll();
    toggleOpenChoiceMenu($("menuItemOpen"));
  });
  $("menuItemOpenFile")?.addEventListener("click", () => { closeAll(); promptOpenSystemFile(); });
  $("menuItemOpenFolder")?.addEventListener("click", () => { closeAll(); promptOpenSystemFolder(); });
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
  $("menuItemRunHealth")?.addEventListener("click", () => { closeAll(); runHubRootCmd("python3 salud_sistema.py"); });
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
  $("menuItemJoinProject")?.addEventListener("click", () => { closeAll(); openProjectJoin(); });
  $("menuItemSettings")?.addEventListener("click", () => { closeAll(); $("navSettings").click(); });
  $("menuItemCommands")?.addEventListener("click", () => { closeAll(); $("commandBar").click(); });
  $("menuItemAbout")?.addEventListener("click", () => { closeAll(); $("aboutDialog")?.showModal(); });
  $("btnCloseAbout")?.addEventListener("click", () => $("aboutDialog")?.close());
  $("btnCloseAboutBtn")?.addEventListener("click", () => $("aboutDialog")?.close());
}

// ---------------- Equipo por proyecto (ver la sección Compartir por proyecto) ----------------

// ---------------- Onboarding & Hermes Connection Wizard ----------------
function openOnboardingWizard(step = 1) {
  const overlay = $("onboardingGateway");
  if (!overlay) return;
  overlay.hidden = false;
  goToOnboardingStep(step);
  const closeBtn = $("btnCloseOnboarding");
  if (closeBtn) { closeBtn.style.display = "flex"; closeBtn.title = "Omitir por ahora"; }
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

  // Solo se abre automáticamente la primera vez; después respeta la decisión del usuario.
  if (localStorage.getItem("atlantis_onboarding_done")) {
    overlay.hidden = true;
    return;
  }
  overlay.hidden = false;
  goToOnboardingStep(1);

  const selModel = $("obMyAgentModel");
  const selEngine = $("obMyAgentEngine");

  function getSelectedModelInfo() {
    const opt = selModel?.selectedOptions?.[0];
    const model = opt?.value || "nemotron-3-ultra-free";
    const provider = opt?.getAttribute("data-provider") || "hermes";
    const label = opt?.textContent?.split("(")[0]?.trim() || model;
    return { model, provider, label };
  }

  selModel?.addEventListener("change", () => {
    const { model, label } = getSelectedModelInfo();
    if ($("nodeBadgeMine")) $("nodeBadgeMine").textContent = label;
    if ($("obHermesDetectedModel")) $("obHermesDetectedModel").textContent = model;
    if ($("modelSpecBadge")) $("modelSpecBadge").textContent = `⚡ ${label}`;
  });

  selEngine?.addEventListener("change", (e) => {
    const val = e.target.value;
    if ($("obGroupGateway")) {
      $("obGroupGateway").style.display = val === "gateway" ? "block" : "none";
    }
  });

  // Selector de asistente (cualquier marca): Hermes, Claude Code, Ollama o CLI propio.
  // Solo los realmente detectados en ESTA máquina se pueden elegir.
  state.availableAdapters = [];
  state.selectedAdapter = "";

  async function loadAdapterPicker() {
    const box = $("obAdapterCards");
    const badge = $("adapterCountBadge");
    try {
      const res = await api("/api/agents/available").catch(() => null);
      const adapters = (res && res.adapters) || [];
      state.availableAdapters = adapters;
      const detected = adapters.filter(a => a.detected);
      if (badge) badge.textContent = detected.length
        ? `${detected.length} detectado${detected.length > 1 ? "s" : ""}`
        : "ninguno detectado";
      // Preselección: la guardada, si no Hermes detectado, si no el primero detectado.
      let sel = (res && res.selected) || "";
      if (!detected.some(a => a.id === sel)) {
        sel = (detected.find(a => a.id === "hermes") || detected[0] || {}).id || "";
      }
      state.selectedAdapter = sel;
      renderAdapterCards();
      refreshDetectCard();
    } catch {
      if (box) box.innerHTML = `<div class="tree-empty">No se pudo consultar los asistentes.</div>`;
    }
  }

  function renderAdapterCards() {
    const box = $("obAdapterCards");
    if (!box) return;
    // Solo adaptadores integrados aquí: los CLI guardados viven como slots.
    const adapters = (state.availableAdapters || []).filter(a => a.id !== "custom");
    const cards = adapters.map(a => {
      const sel = state.selectedAdapter === a.id ? " selected" : "";
      const dis = a.detected ? "" : " disabled";
      const badge = a.detected
        ? `<span class="adapter-badge ok">✓ Detectado</span>`
        : `<span class="adapter-badge no">No instalado</span>`;
      return `<button type="button" class="adapter-card${sel}${dis}" data-adapter="${esc(a.id)}" ${a.detected ? "" : "disabled"}>
        <span class="adapter-radio">${state.selectedAdapter === a.id ? "●" : "○"}</span>
        <span class="adapter-info"><strong>${esc(a.label)}</strong>
        <small class="mono">${esc(a.detail || (a.detected ? "disponible" : "no encontrado en esta máquina"))}</small></span>
        ${badge}
      </button>`;
    }).join("");
    box.innerHTML = cards + `
      <button type="button" class="adapter-card${state.selectedAdapter === "custom" ? " selected" : ""}" data-adapter="custom">
        <span class="adapter-radio">${state.selectedAdapter === "custom" ? "●" : "○"}</span>
        <span class="adapter-info"><strong>＋ CLI personalizado</strong>
        <small class="mono">Cualquier asistente con CLI: configura tu comando</small></span>
        <span class="adapter-badge custom">Manual</span>
      </button>`;
    box.querySelectorAll("[data-adapter]").forEach(btn => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-adapter");
        if (id !== "custom") {
          const a = state.availableAdapters.find(x => x.id === id);
          if (!a || !a.detected) {
            toast("Ese asistente no está instalado en esta máquina.");
            return;
          }
        }
        state.selectedAdapter = id;
        renderAdapterCards();
        refreshDetectCard();
        const customBox = $("obCustomCliBox");
        if (customBox) customBox.hidden = id !== "custom";
      });
    });
  }

  function selectedAdapterInfo() {
    if (state.selectedAdapter === "custom") {
      const name = $("obCustomCliName")?.value.trim() || "Mi CLI";
      return { id: "custom", label: name, detected: !!$("obCustomCliCmd")?.value.trim(), detail: $("obCustomCliCmd")?.value.trim() || "" };
    }
    return state.availableAdapters.find(a => a.id === state.selectedAdapter) || null;
  }

  function refreshDetectCard() {
    // La tarjeta de detección refleja el adaptador ELEGIDO (no siempre Hermes).
    const info = selectedAdapterInfo();
    const label = info ? info.label : "—";
    if ($("obHermesDetectedName")) $("obHermesDetectedName").textContent = label;
    if ($("obHermesAvatar")) $("obHermesAvatar").textContent = (label || "?").slice(0, 2).toUpperCase();
    if ($("obHermesDetectedBadge")) {
      $("obHermesDetectedBadge").textContent = info && info.detected ? "✓ Detectado" : "No detectado";
    }
    if ($("obHermesDetectedPath")) $("obHermesDetectedPath").textContent = (info && info.detail) || "—";
    if ($("obHermesDetectedModel")) $("obHermesDetectedModel").textContent = (info && info.default_model) || "—";
    if ($("obMyAgentStatusText")) $("obMyAgentStatusText").textContent = info && info.detected
      ? `✓ ${label} detectado y listo para conectar`
      : "Elige un asistente detectado o configura tu CLI";
  }

  // Probar comando personalizado (ejecución REAL con prompt de eco)
  $("btnTestCustomCli")?.addEventListener("click", async () => {
    const cmd = $("obCustomCliCmd")?.value.trim() || "";
    const out = $("customCliTestResult");
    if (!cmd) {
      if (out) out.textContent = "Escribe un comando primero.";
      return;
    }
    if (out) out.textContent = "⏳ ejecutando…";
    try {
      const res = await api("/api/agents/custom/test", {
        method: "POST",
        body: JSON.stringify({ command: cmd })
      });
      const txt = String(res.salida || "").trim().slice(0, 300);
      if (out) out.textContent = res.ok ? `✓ OK (código ${res.retcode}): ${txt}` : `✗ Falló (código ${res.retcode}): ${txt}`;
    } catch (err) {
      if (out) out.textContent = `Error: ${err.message}`;
    }
  });

  // Nombre del agente local (de ~/.hermes): solo sugiere el valor del campo,
  // la tarjeta visual la gobierna refreshDetectCard() según el adaptador elegido.
  async function detectUserHermes() {
    try {
      const res = await api("/api/hermes/detect").catch(() => null);
      if (res && res.ok) {
        const agentName = (res.agent_name || "").trim();
        if (agentName && $("obMyAgentName") && !$("obMyAgentName").value) {
          $("obMyAgentName").value = agentName;
        }
        // El modelo lo define tu asistente: reflejar lo detectado en el select
        if (selModel && res.model) {
          const opt = selModel.options[0];
          if (opt) {
            opt.value = res.model;
            opt.textContent = `⚡ ${res.model} (config actual de tu asistente)`;
            selModel.selectedIndex = 0;
            selModel.dispatchEvent(new Event("change"));
          }
        }
      }
    } catch {
      // Dejar valores por defecto
    }
  }
  detectUserHermes();
  loadAdapterPicker();

  // Probar y Conectar Asistente (Paso 1) — sin claves: el modelo lo aporta tu asistente local
  async function connectHermesAction(advanceToStep2 = true) {
    const btn = advanceToStep2 ? $("btnConnectMyAgent") : $("btnTestMyAgent");
    const dot = $("obMyAgentDot");
    const statusText = $("obMyAgentStatusText");
    const agentName = $("obMyAgentName")?.value.trim() || "Mi Asistente";
    const engine = selEngine?.value || "local";
    const endpoint = $("obGatewayUrl")?.value.trim() || "";
    let adapterId = state.selectedAdapter || "";
    let adapterLabel = "";

    if (btn) btn.disabled = true;
    if (statusText) statusText.textContent = "Estableciendo enlace con tu asistente...";

    try {
      // CLI personalizado: guardarlo primero (por máquina), luego conectar.
      let customCmd = "";
      if (adapterId === "custom") {
        const cmd = $("obCustomCliCmd")?.value.trim() || "";
        const cname = $("obCustomCliName")?.value.trim() || agentName;
        if (!cmd) throw new Error("Configura el comando de tu CLI personalizado primero.");
        customCmd = cmd;
        await api("/api/agents/custom", {
          method: "POST",
          body: JSON.stringify({ name: cname, command: cmd })
        });
        adapterLabel = cname;
      } else {
        const info = selectedAdapterInfo();
        if (!info || !info.detected) throw new Error("Elige un asistente detectado en esta máquina.");
        adapterLabel = info.label;
      }

      const { model } = getSelectedModelInfo();
      const res = await api("/api/agents/connect", {
        method: "POST",
        body: JSON.stringify({
          name: agentName,
          adapter: adapterId,
          command: customCmd,
          role: "Creador Autónomo",
          model: model || "auto",
          endpoint: endpoint
        })
      });
      if (!res.ok) throw new Error(res.error || "No se pudo conectar");

      state.hermesConnected = true;
      localStorage.setItem("atlantis_agent_name", agentName);
      if (dot) dot.className = "status-dot connected";
      if (statusText) statusText.textContent = `✓ ${agentName} Conectado [${adapterLabel}] — Activo`;
      toast(`✓ ${agentName} conectado con ${adapterLabel}`);

      // Actualizar Step 2 Preview
      if ($("nodeNameMine")) $("nodeNameMine").textContent = agentName;
      if ($("nodeBadgeMine")) $("nodeBadgeMine").textContent = adapterLabel;
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
    const agentName = $("obMyAgentName")?.value.trim() || "Mi Hermes";

    if (btn) btn.disabled = true;
    if (dot) dot.className = "status-dot";
    if (statusText) statusText.textContent = "⚡ Probando enlace con el hub…";

    const t0 = performance.now();
    try {
      await connectHermesAction(false);
      const ms = (performance.now() - t0).toFixed(0);
      if (hudLatency) hudLatency.textContent = `LATENCIA: ${ms}ms`;
      if (statusText) statusText.textContent = `✓ ${agentName} activo | Respuesta del hub en ${ms}ms`;
    } catch (err) {
      if (statusText) statusText.textContent = `Error: ${err.message}`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  $("btnTestMyAgent")?.addEventListener("click", testHermesTelemetry);
  $("btnConnectMyAgent")?.addEventListener("click", () => connectHermesAction(true));

  // Volver de Paso 2 a Paso 1
  $("btnStep2Back")?.addEventListener("click", () => goToOnboardingStep(1));

  // ---------------- PASO 2: EQUIPO (OPCIONAL) ----------------
  // Unirse a un proyecto con código o enlace de unión. Sin equipo, se entra solo.
  $("btnObJoinProject")?.addEventListener("click", async () => {
    const btn = $("btnObJoinProject");
    const code = extractJoinCode($("obTeamJoinCode")?.value || "");
    const carpeta = ($("obTeamJoinFolder")?.value || "").trim();
    const statusEl = $("obTeamJoinStatus");
    if (!code || !carpeta) {
      if (statusEl) statusEl.textContent = "Escribe el código (o el enlace de unión) y la carpeta del proyecto.";
      return;
    }
    btn.disabled = true;
    if (statusEl) statusEl.textContent = "Uniéndose al proyecto…";
    try {
      const res = await api("/api/proyectos/unirse", {
        method: "POST",
        body: JSON.stringify({ code, carpeta, proyecto: "general", agent_name: `Hermes-${currentUserName()}`, user_name: currentUserName() })
      });
      if (!res.ok) throw new Error(res.error || "No se pudo unir");
      state.obTeamJoined = { carpeta, code };
      if (statusEl) statusEl.textContent = `✓ Te uniste al proyecto «${carpeta}».`;
      toast(`✓ Te uniste al proyecto «${carpeta}»`);
      await refreshSharedProjects();
    } catch (err) {
      if (statusEl) statusEl.textContent = `Error: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
  document.querySelectorAll('input[name="obTeamMode"]').forEach(radio => {
    radio.addEventListener("change", () => {
      const join = document.querySelector('input[name="obTeamMode"][value="join"]')?.checked;
      const box = $("obTeamJoinBox");
      if (box) box.hidden = !join;
    });
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

  // Lanzar Atlantis Studio — exige agente propio conectado. Sin requisito de equipo:
  // entrar solo es válido; unirse a un proyecto es opcional (paso 2).
  $("btnLaunchStudio")?.addEventListener("click", async () => {
    const launchBtn = $("btnLaunchStudio");
    launchBtn.textContent = "Entrando a Atlantis Studio...";
    launchBtn.disabled = true;

    const myAgent = $("obMyAgentName")?.value.trim() || "Mi Hermes";
    const { model, provider, label } = getSelectedModelInfo();

    const selectedWorkspaceRadio = document.querySelector('input[name="obWorkspaceChoice"]:checked');
    const wsMode = selectedWorkspaceRadio ? selectedWorkspaceRadio.value : "clean";
    const newProjName = $("obNewProjectName")?.value.trim() || "";

    try {
      // 1. Conectar mi agente (real, sin claves)
      await api("/api/hermes/connect", {
        method: "POST",
        body: JSON.stringify({
          name: myAgent,
          role: "Creador Autónomo",
          engine: selEngine?.value || "local",
          provider: provider,
          model: model,
          endpoint: $("obGatewayUrl")?.value.trim() || ""
        })
      });
      state.hermesConnected = true;
      localStorage.setItem("atlantis_agent_name", myAgent);

      // 2. Establecer Workspace
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

      // 3. Limpiar tabs del editor para arrancar limpio
      state.openTabs = [];
      state.activeTabIdx = -1;
      renderTabs();
      switchTab(-1);

      // 4. Actualizar árbol de archivos del workspace
      await loadTree();
      await refreshSharedProjects();

      // 5. Mensaje de sistema honesto
      const joined = state.obTeamJoined;
      state.messages = [
        { channel: "agents", author: "Sistema", initials: "✦", agent: true, time: timeNow(),
          text: joined
            ? `${myAgent} [${label}] activo. Te uniste al proyecto «${joined.carpeta}».`
            : `${myAgent} [${label}] activo en modo individual (sin equipo de proyecto).`,
          tag: "SISTEMA" }
      ];
      renderMessages();

      // Ocultar Overlay
      overlay.hidden = true;
      try { localStorage.setItem("atlantis_onboarding_done", "1"); } catch (_) {}
      toast(joined
        ? `🚀 ${myAgent} [${label}] activo · proyecto «${joined.carpeta}».`
        : `🚀 ${myAgent} [${label}] activo en modo individual.`);

      printTerminalOutput(`<span class="prompt">Atlantis Studio 2.0</span> — ${joined ? `Proyecto «${joined.carpeta}»` : "Modo Individual"}\n✓ Mi Agente: <span class="cmd">${esc(myAgent)}</span> [${esc(label)}]\n✓ Workspace: <span class="path">${esc(state.workspace.root)}</span>\n✓ Usuario: <span class="badge">${esc(currentUserName())}</span>\nListo para programar.`);
    } catch (err) {
      toast(`Error al entrar: ${err.message}`);
    } finally {
      launchBtn.textContent = "🚀 Entrar a Atlantis Studio";
      launchBtn.disabled = false;
    }
  });

  // Re-abrir desde el botón del navbar (los items del menú ya se enlazan en bindMenubar)
  $("btnHermesPairNav")?.addEventListener("click", () => openOnboardingWizard(2));
  $("btnCloseOnboarding")?.addEventListener("click", () => {
    localStorage.setItem("atlantis_onboarding_done", "1");
    $("onboardingGateway").hidden = true;
  });
}

/* ================= Multi-assistant slots + switcher ================= */

let slotState = { slots: [], active: "" };

async function loadSlotSwitcher() {
  const bar = $("slotSwitcherBar"), sel = $("slotSwitcherSelect");
  if (!bar || !sel) return;
  try {
    const res = await api("/api/agents/slots");
    slotState = { slots: res.slots || [], active: res.active || "" };
    if (!slotState.slots.length) { bar.hidden = true; return; }
    bar.hidden = false;
    // Select oculto: se mantiene sincronizado por compatibilidad (activeSlotName, menciones).
    sel.innerHTML = slotState.slots.map((s) =>
      `<option value="${esc(s.name)}"${s.active ? " selected" : ""}>${esc(s.name)}</option>`
    ).join("");
    renderAgentBar();
    if (state.channel === "agents") $("messageInput").placeholder = agentComposerPlaceholder();
  } catch (_) { bar.hidden = true; }
}

function slotInitials(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  return (parts[0][0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

function renderAgentBar() {
  const active = slotState.slots.find((s) => s.active) || slotState.slots[0];
  if (!active) return;
  $("agentBarAvatar").textContent = slotInitials(active.name);
  $("agentBarName").textContent = active.name;
  const st = $("agentBarStatus");
  const ok = !!active.detected;
  st.innerHTML = `<i class="agent-dot ${ok ? "on" : "off"}"></i><span>${ok ? "En línea" : "No detectado"}</span>`;
  $("agentBarMain").title = `Asistente activo: ${active.name} — clic para cambiar`;
  const menu = $("agentBarMenu");
  menu.innerHTML = slotState.slots.map((s) => `
    <button type="button" class="agent-bar-item${s.active ? " active" : ""}" data-slot="${esc(s.name)}">
      <span class="agent-bar-item-avatar">${esc(slotInitials(s.name))}</span>
      <span class="agent-bar-item-meta"><strong>${esc(s.name)}</strong><span>${esc(s.adapter || "")}</span></span>
      ${s.active ? '<span class="agent-bar-check">✓</span>' : ""}
      <i class="agent-dot ${s.detected ? "on" : "off"}"></i>
    </button>`).join("");
}

function toggleAgentBarMenu(force) {
  const menu = $("agentBarMenu");
  if (!menu) return;
  const show = force !== undefined ? force : menu.hidden;
  menu.hidden = !show;
}

async function switchActiveSlot(name) {
  try {
    const res = await api("/api/agents/slots/active", {
      method: "POST", body: JSON.stringify({ name })
    });
    slotState = { slots: res.slots || [], active: res.active || "" };
    toast(`✓ Asistente activo: ${name}`);
    await loadSlotSwitcher();
  } catch (e) { toast("✕ " + (e.message || "No se pudo cambiar"), "err"); }
}

async function openSlotManager() {
  const dlg = $("slotManagerDialog");
  if (!dlg) return;
  dlg.showModal();
  await refreshSlotManager();
}

async function refreshSlotManager() {
  const list = $("slotManagerList"), adapterSel = $("slotAdapterSelect");
  if (!list) return;
  try {
    const [slotsRes, av, health] = await Promise.all([
      api("/api/agents/slots"),
      api("/api/agents/available").catch(() => ({ adapters: [] })),
      api("/api/hermes/health").catch(() => null)
    ]);
    slotState = { slots: slotsRes.slots || [], active: slotsRes.active || "" };

    if (health && $("hermesDiagMeta")) {
      const ok = !!health.installed;
      if ($("hermesDialogDot")) $("hermesDialogDot").className = `hermes-dot-pulse ${ok ? "" : "off"}`;
      if ($("hermesDialogTitle")) $("hermesDialogTitle").textContent = `Hermes Agent · ${health.agent_name || "Koko"}`;
      $("hermesDiagMeta").innerHTML = ok
        ? `<strong>Estado:</strong> En línea · <strong>Modelo:</strong> ${esc(health.model || "auto")} (${esc(health.provider || "gemini")})<br>
           <strong>Binario:</strong> <span class="mono">${esc(health.hermes_bin || "hermes")}</span><br>
           <strong>Worker de tareas:</strong> ${health.worker?.active ? "Activo (en segundo plano)" : "En reposo"} · <strong>Versión:</strong> ${esc(health.version || "0.22+")}`
        : `<strong>Estado:</strong> No detectado en esta máquina (~/.local/bin/hermes).`;
    }

    list.innerHTML = slotState.slots.length ? slotState.slots.map((s) => `
      <div class="slot-row${s.active ? " active" : ""}">
        <span class="slot-dot ${s.detected ? "ok" : "no"}" title="${esc(s.detail || "")}"></span>
        <div style="flex:1;">
          <div class="slot-name">${esc(s.name)}${s.active ? " ✓ activo" : ""}</div>
          <div class="slot-meta">${esc(s.adapter_label)}${s.default_model ? " · " + esc(s.default_model) : ""}${s.detected ? "" : " · no detectado ahora"}</div>
        </div>
        ${s.active ? "" : `<button type="button" class="action-btn sm" data-slot-activate="${esc(s.name)}">Activar</button>`}
        <button type="button" class="ghost-btn sm" data-slot-delete="${esc(s.name)}">✕</button>
      </div>`).join("")
      : `<div class="wizard-hint">Aún no hay asistentes. Añade el primero abajo.</div>`;
    if (adapterSel) {
      const builtins = (av.adapters || []).filter((a) => a.id !== "custom");
      adapterSel.innerHTML = builtins.map((a) => {
        // openai_compat se configura aquí mismo: siempre seleccionable.
        const selectable = a.detected || a.id === "openai_compat";
        const dot = a.detected ? "🟢" : (a.id === "openai_compat" ? "🔧" : "⚪");
        return `<option value="${esc(a.id)}"${selectable ? "" : " disabled"}>${dot} ${esc(a.label)}</option>`;
      }).join("") + `<option value="custom">⌨ CLI personalizado (p. ej. Antigravity)</option>`;
      syncSlotAdapterWraps();
    }
  } catch (e) { list.innerHTML = `<div class="wizard-hint">✕ ${esc(e.message || "Error")}</div>`; }
}

function syncSlotAdapterWraps() {
  const v = $("slotAdapterSelect")?.value || "";
  if ($("slotCmdWrap")) $("slotCmdWrap").hidden = v !== "custom";
  if ($("slotOaiWrap")) $("slotOaiWrap").hidden = v !== "openai_compat";
}

async function addSlotFromManager() {
  const name = ($("slotNameInput") || {}).value?.trim() || "";
  const adapter = $("slotAdapterSelect")?.value || "hermes";
  const command = adapter === "custom" ? ($("slotCmdInput") || {}).value?.trim() || "" : "";
  const base_url = adapter === "openai_compat" ? ($("slotOaiUrlInput") || {}).value?.trim() || "" : "";
  const model = adapter === "openai_compat" ? ($("slotOaiModelInput") || {}).value?.trim() || "" : "";
  const api_key = adapter === "openai_compat" ? ($("slotOaiKeyInput") || {}).value || "" : "";
  if (!name) { toast("Ponle un nombre al asistente", "err"); return; }
  if (adapter === "custom" && !command) { toast("El CLI personalizado necesita un comando", "err"); return; }
  if (adapter === "openai_compat" && !base_url) { toast("El endpoint necesita su URL base", "err"); return; }
  try {
    const res = await api("/api/agents/slots", {
      method: "POST",
      body: JSON.stringify({ name, adapter, command, base_url, model, api_key, make_active: !slotState.slots.length })
    });
    toast(`✓ Asistente "${name}" añadido`);
    $("slotNameInput").value = ""; $("slotCmdInput").value = "";
    if ($("slotOaiUrlInput")) $("slotOaiUrlInput").value = "";
    if ($("slotOaiModelInput")) $("slotOaiModelInput").value = "";
    if ($("slotOaiKeyInput")) $("slotOaiKeyInput").value = "";
    if ($("slotOaiTestResult")) $("slotOaiTestResult").textContent = "";
    await refreshSlotManager();
    await loadSlotSwitcher();
  } catch (e) { toast("✕ " + (e.message || "No se pudo añadir"), "err"); }
}

async function testOaiConnection() {
  const out = $("slotOaiTestResult");
  const base_url = ($("slotOaiUrlInput") || {}).value?.trim() || "";
  const api_key = ($("slotOaiKeyInput") || {}).value || "";
  if (!base_url) { if (out) out.textContent = "✕ Falta la URL base"; return; }
  if (out) out.textContent = "⏳ probando…";
  try {
    const res = await api("/api/agents/openai/test", {
      method: "POST", body: JSON.stringify({ base_url, api_key })
    });
    if (out) {
      out.textContent = res.ok
        ? `✓ OK · modelos: ${(res.models || []).slice(0, 5).join(", ")}${(res.models || []).length > 5 ? "…" : ""}`
        : "✕ " + (res.error || "falló").slice(0, 120);
    }
  } catch (e) { if (out) out.textContent = "✕ " + (e.message || "error"); }
}

async function deleteSlot(name) {
  if (!confirm(`¿Eliminar el asistente "${name}"?`)) return;
  try {
    const res = await api(`/api/agents/slots/${encodeURIComponent(name)}`, { method: "DELETE" });
    slotState = { slots: res.slots || [], active: res.active || "" };
    toast(`Asistente "${name}" eliminado`);
    await refreshSlotManager();
    await loadSlotSwitcher();
  } catch (e) { toast("✕ " + (e.message || "No se pudo eliminar"), "err"); }
}

async function testSlotCommand() {
  const out = $("slotCmdTestResult");
  const cmd = ($("slotCmdInput") || {}).value?.trim() || "";
  if (out) out.textContent = "⏳ probando…";
  try {
    const res = await api("/api/agents/custom/test", {
      method: "POST", body: JSON.stringify({ command: cmd })
    });
    if (out) out.textContent = res.ok ? "✓ OK" : "✕ " + (res.salida || "falló").slice(0, 80);
  } catch (e) { if (out) out.textContent = "✕ " + (e.message || "error"); }
}

// Asignar tarea: opciones = slots conectados (+ debate dual)
async function populateTaskAssignees() {
  const sel = $("taskAssignee");
  if (!sel) return;
  const base = `<option value="ambos">Ambos agentes · Debate Dual</option>`;
  const opts = slotState.slots.map((s) =>
    `<option value="${esc(s.name)}">${esc(s.name)}${s.active ? " · activo" : ""}</option>`
  ).join("");
  sel.innerHTML = opts + base;
  if (slotState.active) sel.value = slotState.active;
}

function initSlotSwitcher() {
  $("agentBarMain")?.addEventListener("click", (e) => { e.stopPropagation(); toggleAgentBarMenu(); });
  $("agentBarMenu")?.addEventListener("click", (e) => {
    const item = e.target.closest("[data-slot]");
    if (!item) return;
    toggleAgentBarMenu(false);
    if (item.dataset.slot !== slotState.active) switchActiveSlot(item.dataset.slot);
  });
  document.addEventListener("click", (e) => {
    const menu = $("agentBarMenu");
    if (menu && !menu.hidden && !e.target.closest("#slotSwitcherBar")) toggleAgentBarMenu(false);
  });
  // Escape global: cierra menús personalizados (los <dialog> nativos ya lo hacen solos).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    toggleAgentBarMenu(false);
    closeAllMenus();
    const om = $("openChoiceMenu");
    if (om && !om.hidden) hideOpenChoiceMenu();
  });
  $("btnManageSlots")?.addEventListener("click", openSlotManager);
  $("btnCloseSlotManager")?.addEventListener("click", () => $("slotManagerDialog").close());
  $("btnCancelSlotManager")?.addEventListener("click", () => $("slotManagerDialog").close());
  $("btnAddSlot")?.addEventListener("click", addSlotFromManager);
  $("slotAdapterSelect")?.addEventListener("change", syncSlotAdapterWraps);
  $("btnTestSlotCmd")?.addEventListener("click", testSlotCommand);
  $("btnTestOai")?.addEventListener("click", testOaiConnection);
  $("slotManagerList")?.addEventListener("click", (e) => {
    const act = e.target.closest("[data-slot-activate]");
    const del = e.target.closest("[data-slot-delete]");
    if (act) switchActiveSlot(act.dataset.slotActivate).then(refreshSlotManager);
    if (del) deleteSlot(del.dataset.slotDelete);
  });
  $("btnNewTaskHeader")?.addEventListener("click", populateTaskAssignees);
  $("btnHermesStatusPill")?.addEventListener("click", () => pingHermesAction());
  $("btnPingHermes")?.addEventListener("click", () => pingHermesAction());
  $("btnReconnectHermes")?.addEventListener("click", () => reconnectHermesAction());
  loadSlotSwitcher();
}

async function syncHermesLive() {
  try {
    const health = await api("/api/hermes/health").catch(() => null);
    const pill = $("btnHermesStatusPill");
    const txt = $("hermesStatusPillText");
    const dot = $("hermesPulseDot");
    if (!pill || !txt) return;

    if (health && health.installed) {
      state.hermesConnected = true;
      const agent = health.agent_name || "Koko";
      const model = health.model || "gemini-3-flash-preview";
      txt.textContent = `⚡ ${agent}`;
      pill.title = `Hermes Agent: ${agent}\nModelo: ${model}\nCLI: ${health.hermes_bin}\nClic para probar latencia (Ping)`;
      if (dot) dot.className = "hermes-dot-pulse";
    } else {
      state.hermesConnected = false;
      txt.textContent = "⚡ Hermes";
      pill.title = "Hermes desconectado. Clic para conectar.";
      if (dot) dot.className = "hermes-dot-pulse off";
    }
  } catch (err) {
    console.warn("Hermes sync error:", err);
  }
}

async function pingHermesAction() {
  const txt = $("hermesStatusPillText");
  const dot = $("hermesPulseDot");
  const origTxt = txt ? txt.textContent : "⚡ Hermes";
  if (txt) txt.textContent = "⚡ Ping…";
  try {
    const res = await api("/api/hermes/ping", { method: "POST" });
    if (res && res.ok) {
      toast(`⚡ Hermes [${res.agent || "Koko"}] respondió en ${res.latency_ms}ms · OK`);
      if (txt) txt.textContent = `⚡ ${res.agent || "Koko"} · ${res.latency_ms}ms`;
      if (dot) dot.className = "hermes-dot-pulse";
      setTimeout(() => { if (txt) txt.textContent = origTxt; }, 5000);
    } else {
      toast(`✕ Error al conectar con Hermes: ${res?.error || "Desconocido"}`, "err");
      if (txt) txt.textContent = "⚡ Offline";
      if (dot) dot.className = "hermes-dot-pulse off";
    }
  } catch (err) {
    toast(`✕ Fallo de enlace con Hermes: ${err.message}`, "err");
    if (txt) txt.textContent = "⚡ Offline";
    if (dot) dot.className = "hermes-dot-pulse off";
  }
}

async function reconnectHermesAction() {
  toast("Reconectando con Hermes Agent local…");
  try {
    const res = await api("/api/hermes/connect", {
      method: "POST",
      body: JSON.stringify({ adapter: "hermes" })
    });
    if (res && res.ok) {
      toast(`✓ Conexión con Hermes restablecida: ${res.agent?.name || "Koko"}`);
      await syncHermesLive();
      await loadSlotSwitcher();
      await refreshSlotManager();
    } else {
      toast(`✕ No se pudo conectar: ${res?.error || "Desconocido"}`, "err");
    }
  } catch (err) {
    toast(`✕ Error reconectando: ${err.message}`, "err");
  }
}

// Kick off
initSlotSwitcher();
init();
