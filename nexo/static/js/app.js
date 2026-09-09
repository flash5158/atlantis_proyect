/**
 * NEXO Studio 2.0 — Controlador Principal, Router y WebSockets
 */

// ---------------------------------------------------- ESTADO GLOBAL
const urlParams = new URLSearchParams(location.search);
let TOKEN = urlParams.get('token') || localStorage.getItem('nexo_token') || '';
if (urlParams.get('token')) {
  localStorage.setItem('nexo_token', urlParams.get('token'));
}

async function ensureToken() {
  if (TOKEN) return;
  try {
    const res = await fetch('/api/token_local');
    const d = await res.json();
    if (d && d.token) {
      TOKEN = d.token;
      localStorage.setItem('nexo_token', TOKEN);
      fetchInitialFeed();
      connectMainWebSocket();
    }
  } catch(e) {}
}

const initialUser = (urlParams.get('user') || '').toLowerCase();
let CURRENT_USER = (initialUser === 'amigo' || initialUser === 'daniel') ? initialUser : (localStorage.getItem('nexo_user') || 'daniel');

let activeView = 'social'; // 'social' | 'editor' | 'agents'
let WS = null;

// ---------------------------------------------------- ROUTER DE VISTAS
function switchView(viewName) {
  activeView = viewName;

  // Actualizar botones de navegación
  document.querySelectorAll('.nav-mode-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === viewName);
  });

  // Mostrar el panel de vista correspondiente
  document.querySelectorAll('.view-panel').forEach(panel => {
    panel.classList.remove('active');
  });

  const targetPanel = document.getElementById(`view${capitalize(viewName)}`);
  if (targetPanel) {
    targetPanel.classList.add('active');
  }

  // Refrescar Monaco layout si se cambia al editor
  if (viewName === 'editor' && monacoEditor) {
    setTimeout(() => { monacoEditor.layout(); }, 50);
  }
  // Refrescar xterm fit si se cambia al editor
  if (viewName === 'editor' && ptyFitAddon) {
    setTimeout(() => { try { ptyFitAddon.fit(); } catch(e){} }, 50);
  }
}

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---------------------------------------------------- IDENTIDAD DE USUARIO (DANIEL / AMIGO)
function switchIdentity(newUser) {
  if (newUser) {
    CURRENT_USER = newUser;
  } else {
    CURRENT_USER = (CURRENT_USER === 'daniel') ? 'amigo' : 'daniel';
  }
  localStorage.setItem('nexo_user', CURRENT_USER);
  updateIdentityUI();

  wsSend({
    tipo: 'social_presence',
    entidad: CURRENT_USER,
    estado: 'online',
    actividad: 'En vivo en NEXO Studio',
  });

  showToast(`Identidad cambiada a: ${CURRENT_USER === 'daniel' ? 'Daniel (Dev 1)' : 'Amigo (Dev 2)'}`);
}

function updateIdentityUI() {
  const isDaniel = (CURRENT_USER === 'daniel');
  const name = isDaniel ? 'Daniel' : 'Amigo';
  const role = isDaniel ? 'Dev 1 (Lead)' : 'Dev 2 (Colaborador)';
  const color = isDaniel ? 'var(--accent-daniel)' : 'var(--accent-amigo)';

  const label = document.getElementById('labelCurrentIdentity');
  if (label) label.innerText = `Tú: ${name} (${role})`;

  const pill = document.getElementById('pillCurrentIdentity');
  if (pill) pill.style.borderColor = color;

  const composerLabel = document.getElementById('composerAuthorName');
  if (composerLabel) {
    composerLabel.innerText = name;
    composerLabel.style.color = color;
  }

  const composerAvatar = document.getElementById('composerAuthorAvatar');
  if (composerAvatar) {
    composerAvatar.innerText = '👤';
    composerAvatar.style.borderColor = color;
  }
}

// ---------------------------------------------------- WEBSOCKET Y POLLING SERVERLESS
let isPollingActive = false;
let pollingInterval = null;

async function fetchInitialFeed() {
  try {
    const res = await fetch(`/api/social/feed?token=${encodeURIComponent(TOKEN)}`);
    const d = await res.json();
    if (d.ok && d.feed) {
      renderSocialFeed(d.feed, d.entidades, d.presencia);
    }
  } catch(e) {}
}

async function pollFeedUpdate() {
  try {
    const res = await fetch(`/api/social/feed?token=${encodeURIComponent(TOKEN)}`);
    const d = await res.json();
    if (d.ok && d.feed) {
      syncFeedState(d.feed, d.presencia);
    }
  } catch(e) {}
}

function syncFeedState(newFeed, newPresencia) {
  if (newPresencia) updatePresenceUI(newPresencia);
  if (!newFeed) return;
  // Comparar longitud o timestamps para actualizar solo si hay cambios
  const currentCount = SOCIAL_FEED.length;
  const newCount = newFeed.length;
  const currentReplies = SOCIAL_FEED.reduce((acc, p) => acc + (p.respuestas ? p.respuestas.length : 0), 0);
  const newReplies = newFeed.reduce((acc, p) => acc + (p.respuestas ? p.respuestas.length : 0), 0);

  if (currentCount !== newCount || currentReplies !== newReplies) {
    renderSocialFeed(newFeed, SOCIAL_ENTIDADES, newPresencia);
  } else {
    // Actualizar reacciones existentes
    newFeed.forEach(np => {
      const existing = SOCIAL_FEED.find(p => p.id === np.id);
      if (existing) {
        existing.reacciones = np.reacciones;
        const bar = document.getElementById(`reactions-${np.id}`);
        if (bar) bar.innerHTML = renderReactionsHtml(np.id, np.reacciones);
      }
    });
  }
}

function startHttpPolling() {
  if (isPollingActive) return;
  isPollingActive = true;
  fetchInitialFeed();
  if (pollingInterval) clearInterval(pollingInterval);
  pollingInterval = setInterval(pollFeedUpdate, 3500);
}

function connectMainWebSocket() {
  if (WS) {
    try { WS.close(); } catch(e){}
    WS = null;
  }

  // Si estamos en Vercel, iniciar polling directamente como respaldo
  if (location.hostname.includes('vercel.app')) {
    startHttpPolling();
  }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws?token=${encodeURIComponent(TOKEN)}&nombre=${encodeURIComponent(CURRENT_USER)}`;
  
  try {
    WS = new WebSocket(url);

    WS.onopen = () => {
      if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
      }
      isPollingActive = false;
      wsSend({ tipo: 'join', proyecto: 'atlantis_proyect' });
      showToast(`NEXO Conectado en vivo como ${CURRENT_USER === 'daniel' ? 'Daniel' : 'Amigo'}`);
    };

    WS.onmessage = ev => {
      try {
        handleServerMessage(JSON.parse(ev.data));
      } catch(e) {}
    };

    WS.onerror = () => {
      startHttpPolling();
    };

    WS.onclose = () => {
      startHttpPolling();
      setTimeout(connectMainWebSocket, 5000);
    };
  } catch(err) {
    startHttpPolling();
  }
}

function wsSend(obj) {
  if (WS && WS.readyState === WebSocket.OPEN) {
    WS.send(JSON.stringify(obj));
  }
}

function handleServerMessage(d) {
  if (d.tipo === 'social_init') {
    renderSocialFeed(d.feed, d.entidades, d.presencia);
  } else if (d.tipo === 'social_new_post') {
    onSocialNewPost(d.post);
  } else if (d.tipo === 'social_new_reply') {
    onSocialNewReply(d.post_id, d.respuesta);
  } else if (d.tipo === 'social_reaction_update') {
    onSocialReactionUpdate(d.post_id, d.reply_id, d.reacciones);
  } else if (d.tipo === 'social_presence_update') {
    onSocialPresenceUpdate(d.presencia);
  } else if (d.tipo === 'social_ia_typing') {
    onSocialIaTyping(d.post_id, d.ia);
  } else if (d.tipo === 'arbol') {
    renderFileTree(d.arbol);
  } else if (d.tipo === 'hermes_stream_chunk') {
    appendHermesConsoleChunk(d.chunk);
  } else if (d.tipo === 'hermes_stream_end') {
    appendHermesConsoleChunk(`\n✔ [HERMES TERMINADO] Código: ${d.retcode}\n`);
    fetchFileTree();
  } else if (d.tipo === 'sistema') {
    showToast(d.texto);
  }
}

function appendHermesConsoleChunk(chunk) {
  const c = document.getElementById('hermesLiveConsoleOutput');
  if (c) {
    c.innerText += chunk;
    c.scrollTop = c.scrollHeight;
  }
}

// ---------------------------------------------------- VERIFICACIÓN DE ESTADO IA
async function checkAiStatus() {
  try {
    const res = await fetch(`/api/ai/status?token=${encodeURIComponent(TOKEN)}`);
    const st = await res.json();

    const dotH = document.getElementById('dotHermesStatus');
    const labelH = document.getElementById('labelHermesStatus');
    if (dotH && labelH) {
      if (st.hermes && st.hermes.instalado) {
        dotH.className = 'status-dot hermes';
        labelH.innerText = `Hermes: Online`;
      } else {
        dotH.className = 'status-dot';
        labelH.innerText = `Hermes: Offline`;
      }
    }

    const dotG = document.getElementById('dotGeminiStatus');
    const labelG = document.getElementById('labelGeminiStatus');
    if (dotG && labelG) {
      if (st.gemini && st.gemini.conectado) {
        dotG.className = 'status-dot gemini';
        labelG.innerText = `Gemini: Online`;
      } else {
        dotG.className = 'status-dot';
        labelG.innerText = `Gemini: Sin clave`;
      }
    }
  } catch(e) {}
}

// ---------------------------------------------------- RUNNER DIRECTO DE AGENTES
async function runHermesDirect() {
  const input = document.getElementById('inputHermesDirectPrompt');
  const prompt = input ? input.value.trim() : '';
  if (!prompt) {
    showToast('Escribe una instrucción para Hermes');
    return;
  }

  const out = document.getElementById('hermesLiveConsoleOutput');
  if (out) out.innerText = `\n▶ [HERMES EJECUTANDO]: ${prompt}\n\n`;

  try {
    const res = await fetch(`/api/ai/hermes/run?token=${encodeURIComponent(TOKEN)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: prompt, yolo: true }),
    });
    const d = await res.json();
    if (d.ok) {
      showToast('Hermes completó la tarea');
    } else {
      showToast('Error en Hermes: ' + (d.salida || d.error || ''));
    }
  } catch(e) {
    showToast('Error de conexión con Hermes');
  }
}

// ---------------------------------------------------- TOAST & UTILIDADES
function showToast(msg) {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const t = document.createElement('div');
  t.className = 'toast-msg';
  t.innerHTML = `<span>💬</span> <span>${escapeHtml(msg)}</span>`;
  container.appendChild(t);
  setTimeout(() => { t.remove(); }, 3500);
}

function escapeHtml(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---------------------------------------------------- BOOTSTRAP DE LA APLICACIÓN
window.addEventListener('DOMContentLoaded', () => {
  // Inicializar Identidad
  updateIdentityUI();

  // Navegación de vistas
  document.querySelectorAll('.nav-mode-btn').forEach(btn => {
    btn.onclick = () => switchView(btn.dataset.view);
  });

  // Botón invitar amigo
  const btnShare = document.getElementById('btnShareInviteLink');
  if (btnShare) {
    btnShare.onclick = () => {
      const url = `${location.origin}/?user=amigo&token=${encodeURIComponent(TOKEN)}`;
      navigator.clipboard.writeText(url).then(() => {
        showToast('¡Enlace de sesión para tu amigo copiado al portapapeles!');
      });
    };
  }

  // Alternador de identidad en topbar
  const pillId = document.getElementById('pillCurrentIdentity');
  if (pillId) pillId.onclick = () => switchIdentity();

  // Botón guardar
  const btnSave = document.getElementById('btnSaveActiveFile');
  if (btnSave) btnSave.onclick = () => saveActiveFile();

  // Runner Hermes
  const btnRunH = document.getElementById('btnRunHermesDirect');
  if (btnRunH) btnRunH.onclick = runHermesDirect;

  // Botón limpiar consola Hermes
  const btnClearH = document.getElementById('btnClearHermesConsole');
  if (btnClearH) {
    btnClearH.onclick = () => {
      const out = document.getElementById('hermesLiveConsoleOutput');
      if (out) out.innerText = 'Consola limpia.\n';
    };
  }

  // Botón reiniciar PTY
  const btnRestartPty = document.getElementById('btnRestartPty');
  if (btnRestartPty) btnRestartPty.onclick = restartPtyShell;

  // Inicializar Módulo Social
  initSocialModule();

  // Asegurar token y cargar feed inmediatamente
  ensureToken();
  fetchInitialFeed();

  // Inicializar WebSockets & Datos
  connectMainWebSocket();
  checkAiStatus();
  fetchFileTree();
});

// Inicializar Monaco Editor después de que RequireJS cargue
if (typeof require !== 'undefined') {
  require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' } });
  require(['vs/editor/editor.main'], function () {
    initMonacoEditor();
    initPtyTerminal();
  });
} else {
  window.addEventListener('load', () => {
    initPtyTerminal();
  });
}
