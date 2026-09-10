const files = {
  "src/orchestrator.ts": `import { AgentBus, AgentMessage } from "./agents/protocol";
import { HermesAgent } from "./agents/hermes";

export class AtlantisOrchestrator {
  private readonly bus = new AgentBus();
  private readonly creator = new HermesAgent("Hermes-Daniel", "creator");
  private readonly auditor = new HermesAgent("Hermes-Amigo", "auditor");

  async delegate(objective: string): Promise<void> {
    const plan = await this.creator.plan(objective);
    await this.bus.publish({ type: "plan.ready", payload: plan });
    await this.auditor.review(plan);
    await this.creator.execute(plan);
  }
}
`,
  "src/agents/hermes.ts": `export type HermesRole = "creator" | "auditor";

export class HermesAgent {
  constructor(readonly name: string, readonly role: HermesRole) {}

  async plan(objective: string) {
    return { owner: this.name, objective, steps: ["inspect", "implement", "verify"] };
  }

  async review(plan: unknown) { return { owner: this.name, approved: true, plan }; }
  async execute(plan: unknown) { return { owner: this.name, status: "completed", plan }; }
}
`,
  "src/agents/protocol.ts": `export type AgentMessage = {
  type: "plan.ready" | "review.done" | "task.completed";
  payload: unknown;
};

export class AgentBus {
  private listeners = new Set<(message: AgentMessage) => void>();
  subscribe(listener: (message: AgentMessage) => void) { this.listeners.add(listener); }
  publish(message: AgentMessage) { this.listeners.forEach(listener => listener(message)); }
}
`,
  "README.md": `# Atlantis Studio

Trabajo simultáneo para dos personas y sus agentes Hermes.

## Flujo
1. Daniel y su compañero conversan en #team-chat.
2. Una tarea se divide entre Hermes-Daniel y Hermes-Amigo.
3. El agente auditor valida el plan antes de aplicar cambios.
`,
  "package.json": `{"name":"atlantis-studio","private":true,"scripts":{"test":"npm test"}}`,
  ".env.example": `ATLANTIS_HUB_URL=http://127.0.0.1:8790\nATLANTIS_OWNER_TOKEN=\n`
};

const state = { token: "", socket: null, channel: "team", connected: false, messages: [], tasks: [] };
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[char]));
const timeNow = () => new Date().toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });

function toast(message) {
  const node = $("toast"); node.textContent = message; node.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.remove("show"), 2800);
}

function addMessage(message, channel = state.channel) {
  state.messages.push({ ...message, channel });
  if (state.messages.length > 80) state.messages.shift();
  renderMessages();
}

function renderMessages() {
  const visible = state.messages.filter(message => message.channel === state.channel);
  $("messages").innerHTML = visible.length ? visible.map(message => `<article class="message ${message.agent ? "agent" : ""}">
    <div class="message-avatar ${message.agent ? "agent" : ""}">${esc(message.initials || (message.agent ? "HA" : "DS"))}</div>
    <div><div class="message-head"><strong>${esc(message.author)}</strong><time>${esc(message.time || "ahora")}</time></div>
    <p>${esc(message.text)}</p>${message.tag ? `<span class="message-tag">${esc(message.tag)}</span>` : ""}</div></article>`).join("") : `<div class="empty-state">Todavía no hay mensajes en este canal.</div>`;
  const box = $("messages"); box.scrollTop = box.scrollHeight;
}

function seedMessages() {
  state.messages = [
    { channel: "team", author: "Daniel", initials: "DS", time: "09:41", text: "He abierto el espacio. Dividamos la sincronización y la auditoría entre nuestros Hermes." },
    { channel: "team", author: "Amigo", initials: "AM", time: "09:43", text: "Perfecto. Yo validaré los cambios y probaré los casos de reconexión." },
    { channel: "agents", author: "Hermes-Daniel", initials: "HD", agent: true, time: "09:44", text: "Plan listo: inspección → implementación → verificación. Enviando a Hermes-Amigo para revisión.", tag: "PLAN READY" },
    { channel: "agents", author: "Hermes-Amigo", initials: "HA", agent: true, time: "09:45", text: "Recibido. Revisaré el protocolo y devolveré observaciones en este mismo hilo.", tag: "AUDIT QUEUED" }
  ];
  renderMessages();
}

function renderCode(path = "src/orchestrator.ts") {
  const source = files[path] || "// Archivo aún no sincronizado\n";
  const lines = source.split("\n"); $("lineNumbers").innerHTML = lines.map((_, index) => `<div>${index + 1}</div>`).join("");
  const highlight = line => esc(line).replace(/(import|from|export|class|private|readonly|async|await|return|new|const|type|true)/g, '<span class="kw">$1</span>').replace(/(&quot;[^&]*?&quot;)/g, '<span class="str">$1</span>').replace(/(\b\d+\b)/g, '<span class="num">$1</span>').replace(/(\/\/.*)$/g, '<span class="comment">$1</span>');
  $("codeContent").innerHTML = lines.map(highlight).join("\n");
  document.querySelector(".breadcrumbs strong").textContent = path.split("/").pop();
  document.querySelector(".breadcrumbs span:first-child").textContent = path.split("/")[0] || "workspace";
}

function setChannel(channel) {
  state.channel = channel;
  document.querySelectorAll(".assistant-tab").forEach(button => button.classList.toggle("active", button.dataset.channel === channel));
  $("channelIcon").textContent = channel === "team" ? "#" : "◈";
  $("channelName").textContent = channel === "team" ? "team-chat" : "hermes-bridge";
  $("channelHint").textContent = channel === "team" ? "Daniel y tu compañero" : "Conversación continua entre agentes";
  $("messageInput").placeholder = channel === "team" ? "Escribe un mensaje al equipo…" : "Envía una instrucción al puente Hermes…";
  renderMessages();
}

async function api(path, options = {}) {
  const joiner = path.includes("?") ? "&" : "?";
  const response = await fetch(`${path}${joiner}token=${encodeURIComponent(state.token)}`, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.detail || `HTTP ${response.status}`);
  return data;
}

async function connect() {
  try {
    if (!state.token) state.token = localStorage.getItem("atlantis_token") || (await fetch("/api/token_local").then(response => response.json())).token || "";
    localStorage.setItem("atlantis_token", state.token);
    await api("/api/status");
    state.connected = true; $("connectionStatus").textContent = "● En línea"; $("connectionStatus").className = "status-online"; toast("Atlantis conectado al workspace");
  } catch (error) { state.connected = false; $("connectionStatus").textContent = "● Modo demo"; $("connectionStatus").className = "status-online"; toast("Modo demo activo · configura el hub para sincronizar"); }
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  try {
    state.socket = new WebSocket(`${protocol}://${location.host}/ws?token=${encodeURIComponent(state.token)}&nombre=Daniel`);
    state.socket.onopen = () => { state.connected = true; $("connectionStatus").textContent = "● En línea"; state.socket.send(JSON.stringify({ tipo: "join", proyecto: "general" })); };
    state.socket.onmessage = event => handleEvent(JSON.parse(event.data));
    state.socket.onclose = () => { state.connected = false; $("connectionStatus").textContent = "● Reconectando"; setTimeout(connectSocket, 3000); };
  } catch (_) { /* fallback local */ }
}

function connectSocket() {
  if (state.socket && state.socket.readyState < 2) return;
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  try { state.socket = new WebSocket(`${protocol}://${location.host}/ws?token=${encodeURIComponent(state.token)}&nombre=Daniel`); state.socket.onopen = () => { state.connected = true; $("connectionStatus").textContent = "● En línea"; }; state.socket.onmessage = event => handleEvent(JSON.parse(event.data)); state.socket.onclose = () => setTimeout(connectSocket, 4000); } catch (_) {}
}

function handleEvent(event) {
  if (event.tipo === "chat" && event.de !== "Daniel") addMessage({ author: event.de || "Compañero", initials: "AM", time: event.fecha || timeNow(), text: event.texto }, "team");
  if (event.tipo === "ai_msg") { const message = event.mensaje || {}; addMessage({ author: message.de || "Hermes", initials: String(message.de || "HA").slice(0, 2).toUpperCase(), agent: true, time: message.fecha || timeNow(), text: message.contenido || "", tag: message.tipo?.toUpperCase() }, "agents"); }
  if (event.tipo === "ai_task_update") { state.tasks.push(event.tarea); renderTasks(); }
  if (event.tipo === "presencia") $("connectionStatus").textContent = `● ${event.conectados || 1} conectado(s)`;
}

async function sendMessage(event) {
  event.preventDefault(); const input = $("messageInput"); const text = input.value.trim(); if (!text) return; input.value = "";
  const channel = state.channel; addMessage({ author: "Daniel", initials: "DS", time: timeNow(), text }, channel);
  if (channel === "team") {
    if (state.socket?.readyState === 1) state.socket.send(JSON.stringify({ tipo: "chat", proyecto: "general", texto }));
  } else {
    try { await api("/api/ai/msg", { method: "POST", body: JSON.stringify({ de: "Hermes-Daniel", para: "Hermes-Amigo", proyecto: "general", tipo: "tarea", titulo: "Coordinación Hermes", contenido: text }) }); }
    catch (_) { setTimeout(() => addMessage({ author: "Hermes-Amigo", initials: "HA", agent: true, time: timeNow(), text: "Mensaje recibido. Lo revisaré y devolveré una validación en este hilo.", tag: "ACK" }, "agents"), 650); }
  }
}

async function createTask(event) {
  event.preventDefault(); const title = $("taskTitle").value.trim(); if (!title) return; const description = $("taskDescription").value.trim(); const assignee = $("taskAssignee").value;
  try { const result = await api("/api/ai/task", { method: "POST", body: JSON.stringify({ titulo: title, descripcion: description, asignado: assignee, creador: "Daniel", proyecto: "general", prioridad: "alta" }) }); state.tasks.push(result.tarea); toast("Tarea asignada al puente Hermes"); }
  catch (_) { state.tasks.push({ titulo: title, descripcion, asignado: assignee, estado: "todo" }); toast("Tarea guardada en modo demo"); }
  $("taskDialog").close(); renderTasks();
}

function renderTasks() {
  const existing = document.querySelector("#taskStackItems"); if (!existing) return;
  const count = document.querySelector(".task-count"); if (count) count.textContent = state.tasks.length;
  existing.innerHTML = state.tasks.slice(-5).map(task => `<div class="task-card"><span class="task-status"></span><div><strong>${esc(task.titulo || task.title)}</strong><small>${esc(task.asignado || task.assignee || "Hermes")}</small></div></div>`).join("") || `<div class="task-empty">Sin tareas activas. Crea una desde el canal de agentes.</div>`;
}

function bind() {
  document.querySelectorAll(".activity").forEach(button => button.addEventListener("click", () => { document.querySelectorAll(".activity").forEach(item => item.classList.remove("active")); button.classList.add("active"); const view = button.dataset.view; $("sidebarTitle").textContent = view === "explorer" ? "EXPLORADOR" : view.toUpperCase(); if (view === "collab") setChannel("agents"); toast(`${button.title} · panel listo`); }));
  document.querySelectorAll(".assistant-tab").forEach(button => button.addEventListener("click", () => setChannel(button.dataset.channel)));
  document.querySelectorAll(".tree-row.file").forEach(row => row.addEventListener("click", () => { document.querySelectorAll(".tree-row.file").forEach(item => item.classList.remove("selected")); row.classList.add("selected"); renderCode(row.dataset.file); }));
  $("messageForm").addEventListener("submit", sendMessage); $("messageInput").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("messageForm").requestSubmit(); } });
  $("taskForm").addEventListener("submit", createTask); $("inviteBtn").addEventListener("click", async () => { const link = `${location.origin}/?user=amigo&token=${encodeURIComponent(state.token)}`; try { await navigator.clipboard.writeText(link); toast("Enlace de invitación copiado"); } catch (_) { toast("Comparte este espacio con tu compañero"); } });
  $("commandBar").addEventListener("click", () => toast("Paleta rápida: ⌘P archivos · ⌘Enter ejecutar · ⌘Shift+P comandos")); $("collapseAssistant").addEventListener("click", () => document.querySelector(".assistant-panel").classList.toggle("collapsed"));
  const taskButton = document.createElement("button"); taskButton.className = "new-task-inline"; taskButton.textContent = "+ Nueva tarea"; taskButton.addEventListener("click", () => $("taskDialog").showModal()); $("channelName").parentElement.append(taskButton);
}

seedMessages(); renderCode(); bind(); connect();
