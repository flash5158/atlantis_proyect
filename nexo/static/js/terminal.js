/**
 * NEXO Studio 2.0 — Terminal PTY (xterm.js + WebSocket)
 */

let ptyTerm = null;
let ptyWs = null;
let ptyFitAddon = null;

function initPtyTerminal() {
  const container = document.getElementById('ptyTerminalContainer');
  if (!container || typeof Terminal === 'undefined') return;

  ptyTerm = new Terminal({
    cursorBlink: true,
    fontSize: 12.5,
    fontFamily: "'JetBrains Mono', monospace",
    theme: {
      background: '#04060a',
      foreground: '#e2e8f0',
      cursor: '#06b6d4',
      selectionBackground: 'rgba(6, 182, 212, 0.3)',
    }
  });

  if (typeof FitAddon !== 'undefined' && FitAddon.FitAddon) {
    ptyFitAddon = new FitAddon.FitAddon();
    ptyTerm.loadAddon(ptyFitAddon);
  }

  ptyTerm.open(container);
  if (ptyFitAddon) ptyFitAddon.fit();

  window.addEventListener('resize', () => {
    if (ptyFitAddon) {
      try { ptyFitAddon.fit(); } catch(e){}
    }
  });

  connectPtyWebSocket();

  ptyTerm.onData(data => {
    if (ptyWs && ptyWs.readyState === WebSocket.OPEN) {
      ptyWs.send(data);
    }
  });

  ptyTerm.onResize(size => {
    if (ptyWs && ptyWs.readyState === WebSocket.OPEN) {
      ptyWs.send(JSON.stringify({ tipo: 'resize', cols: size.cols, rows: size.rows }));
    }
  });
}

function connectPtyWebSocket() {
  if (ptyWs) {
    try { ptyWs.close(); } catch(e){}
    ptyWs = null;
  }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/pty?token=${encodeURIComponent(TOKEN)}`;
  ptyWs = new WebSocket(url);
  ptyWs.binaryType = 'arraybuffer';

  ptyWs.onopen = () => {
    if (ptyTerm) {
      ptyTerm.write('\r\n\x1b[36m⚡ [NEXO PTY Shell Conectada]\x1b[0m\r\n');
    }
  };

  ptyWs.onmessage = ev => {
    if (!ptyTerm) return;
    if (ev.data instanceof ArrayBuffer) {
      ptyTerm.write(new Uint8Array(ev.data));
    } else {
      ptyTerm.write(ev.data);
    }
  };

  ptyWs.onclose = () => {
    if (ptyTerm) {
      ptyTerm.write('\r\n\x1b[33m⚡ [PTY Desconectada — Reintentando...]\x1b[0m\r\n');
    }
  };
}

function restartPtyShell() {
  if (ptyTerm) ptyTerm.reset();
  connectPtyWebSocket();
  showToast('Shell reiniciada');
}
