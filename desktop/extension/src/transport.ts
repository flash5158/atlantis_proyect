import WebSocket from 'ws';

export type HubEvent = { v?: number; id?: string; seq?: number; type: string; actor_id?: string; payload?: any; created_at?: number };
export type HubState = { self?: any; members?: any[]; presence?: any[]; messages?: any[]; tasks?: any[]; files?: string[]; seq?: number };
type Listener = (event: HubEvent) => void;

export class HubConnection {
  private socket?: WebSocket;
  private listeners = new Set<Listener>();
  private timer?: ReturnType<typeof setTimeout>;
  private retry = 500;
  private closed = false;
  private seq = 0;
  public connected = false;
  public state: HubState = {};
  constructor(public endpoint: string, private token: string) { this.endpoint = endpoint.replace(/\/+$/, ''); }
  setToken(token: string) { this.token = token; }
  onEvent(listener: Listener) { this.listeners.add(listener); return { dispose: () => this.listeners.delete(listener) }; }
  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.endpoint}${path}`, { method, headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || `Hub HTTP ${response.status}`);
    return data as T;
  }
  async refresh() { this.state = await this.request<HubState>('GET', '/v2/state'); this.seq = this.state.seq || this.seq; return this.state; }
  connect() {
    this.closed = false; if (this.socket && this.socket.readyState < 2) return;
    const url = this.endpoint.replace(/^http/, 'ws') + '/v2/events';
    const socket = this.socket = new WebSocket(url);
    socket.on('open', () => { this.connected = true; this.retry = 500; socket.send(JSON.stringify({ token: this.token, last_seq: this.seq })); });
    socket.on('message', raw => { try { const event = JSON.parse(raw.toString()) as HubEvent; if (event.seq && event.seq > this.seq) this.seq = event.seq; for (const listener of this.listeners) listener(event); } catch { /* malformed event is ignored */ } });
    socket.on('close', () => { this.connected = false; this.scheduleReconnect(); });
    socket.on('error', () => { this.connected = false; });
  }
  close() { this.closed = true; if (this.timer) clearTimeout(this.timer); this.socket?.close(); this.connected = false; }
  private scheduleReconnect() { if (this.closed || this.timer) return; this.timer = setTimeout(() => { this.timer = undefined; this.connect(); }, this.retry); this.retry = Math.min(this.retry * 2, 30000); }
}
