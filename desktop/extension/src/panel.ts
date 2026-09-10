import * as vscode from 'vscode';
import { HubConnection, HubEvent } from './transport';

export class CollaborationPanel implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  constructor(private readonly extensionUri: vscode.Uri, private readonly getHub: () => HubConnection | undefined) {}
  resolveWebviewView(view: vscode.WebviewView) { this.view = view; view.webview.options = { enableScripts: true }; view.webview.html = this.html(view.webview); view.webview.onDidReceiveMessage(async msg => {
    const hub = this.getHub(); if (!hub) return this.post({ type: 'error', text: 'Conecta Atlantis primero.' });
    try {
      if (msg.type === 'refresh') { await hub.refresh(); return this.post({ type: 'state', state: hub.state }); }
      if (msg.type === 'message') await hub.request('POST', '/v2/messages', { client_id: crypto.randomUUID(), channel: msg.channel, text: msg.text });
      if (msg.type === 'task') await hub.request('POST', '/v2/tasks', { client_id: crypto.randomUUID(), title: msg.title, description: msg.description, assignee_id: msg.assignee_id, paths: [], depends_on: [] });
      await hub.refresh(); this.post({ type: 'state', state: hub.state });
    } catch (error) { this.post({ type: 'error', text: String(error) }); }
  }); }
  post(message: unknown) { this.view?.webview.postMessage(message); }
  event(event: HubEvent) { this.post({ type: 'event', event }); }
  private html(webview: vscode.Webview) { const nonce = Math.random().toString(36).slice(2); return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}'"><style>
  :root{color-scheme:dark}body{font:13px system-ui;color:var(--vscode-foreground);padding:12px}button,input,textarea{font:inherit;color:inherit;background:var(--vscode-input-background);border:1px solid var(--vscode-input-border);padding:6px;border-radius:4px}button{cursor:pointer;background:var(--vscode-button-background)}nav{display:flex;gap:6px;margin-bottom:10px}.tab{flex:1}.active{outline:1px solid var(--vscode-focusBorder)}#log{height:220px;overflow:auto;border:1px solid var(--vscode-panel-border);padding:8px;white-space:pre-wrap}.row{display:flex;gap:6px;margin:6px 0}.row>*{flex:1}small{opacity:.7}
  </style><nav><button id="team" class="tab active">Equipo</button><button id="agents" class="tab">Agentes</button></nav><div id="status"><small>Desconectado</small></div><div id="log" role="log" aria-live="polite"></div><form id="form"><div class="row"><input id="text" required maxlength="12000" placeholder="Escribe un mensaje…"><button>Enviar</button></div></form><details><summary>Nueva tarea</summary><form id="task"><input id="title" required placeholder="Título"><textarea id="description" placeholder="Descripción"></textarea><input id="assignee" required placeholder="ID del agente"><button>Asignar</button></form></details><script nonce="${nonce}">
  const vscode=acquireVsCodeApi(),log=document.getElementById('log'),status=document.getElementById('status');let channel='team';const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));function line(s){log.innerHTML+=esc(s)+'\\n';log.scrollTop=log.scrollHeight}function render(s){log.textContent='';(s.messages||[]).forEach(m=>line('['+m.channel+'] '+m.actor_id+': '+m.text));(s.tasks||[]).forEach(t=>line('TAREA '+t.status+': '+t.title));status.textContent=(s.self?.name||'')+' · '+(s.members||[]).length+' miembros'}document.getElementById('team').onclick=()=>{channel='team';document.getElementById('team').classList.add('active');document.getElementById('agents').classList.remove('active')};document.getElementById('agents').onclick=()=>{channel='agents';document.getElementById('agents').classList.add('active');document.getElementById('team').classList.remove('active')};document.getElementById('form').onsubmit=e=>{e.preventDefault();const i=document.getElementById('text');vscode.postMessage({type:'message',channel,text:i.value});i.value=''};document.getElementById('task').onsubmit=e=>{e.preventDefault();vscode.postMessage({type:'task',title:title.value,description:description.value,assignee_id:assignee.value})};window.onmessage=e=>{const m=e.data;if(m.type==='state')render(m.state);if(m.type==='event')line(m.event.payload?.text||m.event.type);if(m.type==='error')line('ERROR: '+m.text)};vscode.postMessage({type:'refresh'});
  </script>`; }
}
