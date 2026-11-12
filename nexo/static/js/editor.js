/**
 * NEXO Studio 2.0 — Editor Monaco y Explorador de Archivos
 */

let monacoEditor = null;
let currentFile = '';
let openFiles = {}; // { path: { model, dirty } }

function initMonacoEditor() {
  if (typeof monaco === 'undefined') return;

  const container = document.getElementById('monacoEditorInstance');
  if (!container) return;

  monacoEditor = monaco.editor.create(container, {
    value: '// Bienvenido a NEXO Studio 2.0 (Antigravity Matrix)\n// Selecciona un archivo en la barra lateral o crea uno nuevo.\n',
    language: 'javascript',
    theme: 'vs-dark',
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    minimap: { enabled: true },
    automaticLayout: true,
    scrollBeyondLastLine: false,
    bracketPairColorization: { enabled: true },
    lineNumbers: 'on',
    roundedSelection: true,
    cursorBlinking: 'smooth',
  });

  // Atajo de guardado Ctrl+S / Cmd+S
  monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function () {
    saveActiveFile();
  });

  monacoEditor.onDidChangeModelContent(() => {
    if (currentFile && openFiles[currentFile]) {
      openFiles[currentFile].dirty = true;
      updateEditorTabsUI();
    }
  });
}

function getFileLanguage(path) {
  const ext = path.split('.').pop().toLowerCase();
  const map = {
    py: 'python', js: 'javascript', ts: 'typescript', html: 'html',
    css: 'css', json: 'json', md: 'markdown', sh: 'shell', yml: 'yaml',
    yaml: 'yaml', sql: 'sql', txt: 'plaintext'
  };
  return map[ext] || 'plaintext';
}

function getFileIcon(filename) {
  if (filename.endsWith('.py')) return '🐍';
  if (filename.endsWith('.js') || filename.endsWith('.ts')) return '📜';
  if (filename.endsWith('.html')) return '🌐';
  if (filename.endsWith('.css')) return '🎨';
  if (filename.endsWith('.json')) return '📦';
  if (filename.endsWith('.md')) return '📝';
  if (filename.endsWith('.sh')) return '⚡';
  if (filename.endsWith('.sql')) return '🗄️';
  return '📄';
}

async function openFile(path) {
  switchView('editor');
  currentFile = path;
  
  const breadcrumb = document.getElementById('breadcrumbCurrentFile');
  if (breadcrumb) breadcrumb.innerText = path;

  if (!openFiles[path]) {
    try {
      const res = await fetch(`/api/archivo?ruta=${encodeURIComponent(path)}&token=${encodeURIComponent(TOKEN)}`);
      const d = await res.json();
      const content = d.contenido !== undefined ? d.contenido : '';
      const lang = getFileLanguage(path);
      const model = monaco.editor.createModel(content, lang);
      openFiles[path] = { model, dirty: false };
    } catch(e) {
      showToast('Error cargando archivo: ' + path);
      return;
    }
  }

  if (monacoEditor && openFiles[path]) {
    monacoEditor.setModel(openFiles[path].model);
  }
  updateEditorTabsUI();
}

async function saveActiveFile() {
  if (!currentFile || !monacoEditor) return;
  const content = monacoEditor.getValue();
  try {
    const res = await fetch(`/api/archivo?ruta=${encodeURIComponent(currentFile)}&token=${encodeURIComponent(TOKEN)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contenido: content }),
    });
    const d = await res.json();
    if (d.ok || res.status === 200) {
      if (openFiles[currentFile]) openFiles[currentFile].dirty = false;
      updateEditorTabsUI();
      showToast(`Guardado: ${currentFile}`);
      fetchFileTree();
    } else {
      showToast('Error guardando: ' + (d.error || ''));
    }
  } catch(e) {
    showToast('Error al conectar con el servidor para guardar');
  }
}

function updateEditorTabsUI() {
  const container = document.getElementById('editorTabsBar');
  if (!container) return;
  container.innerHTML = '';

  const paths = Object.keys(openFiles);
  if (paths.length === 0) {
    container.innerHTML = '<div style="padding: 10px 14px; font-size: 11.5px; color: var(--text-muted);">Sin archivos abiertos</div>';
    return;
  }

  paths.forEach(p => {
    const isAct = (p === currentFile);
    const item = openFiles[p];
    const filename = p.split('/').pop();
    const tab = document.createElement('div');
    tab.className = `editor-tab ${isAct ? 'active' : ''}`;
    tab.innerHTML = `
      <span>${getFileIcon(filename)}</span>
      <span>${escapeHtml(filename)}</span>
      ${item.dirty ? '<span style="color: var(--accent-daniel); font-size: 14px;">*</span>' : ''}
      <span class="tab-close-btn" onclick="closeEditorTab('${escapeHtml(p)}', event)">✕</span>
    `;
    tab.onclick = () => openFile(p);
    container.appendChild(tab);
  });
}

function closeEditorTab(path, ev) {
  if (ev) ev.stopPropagation();
  if (openFiles[path]) {
    openFiles[path].model.dispose();
    delete openFiles[path];
  }
  if (currentFile === path) {
    const remaining = Object.keys(openFiles);
    if (remaining.length > 0) {
      openFile(remaining[remaining.length - 1]);
    } else {
      currentFile = '';
      if (monacoEditor) monacoEditor.setValue('');
      const breadcrumb = document.getElementById('breadcrumbCurrentFile');
      if (breadcrumb) breadcrumb.innerText = 'Selecciona un archivo';
      updateEditorTabsUI();
    }
  } else {
    updateEditorTabsUI();
  }
}

async function fetchFileTree() {
  try {
    const res = await fetch(`/api/arbol?token=${encodeURIComponent(TOKEN)}`);
    const d = await res.json();
    if (d && d.arbol) renderFileTree(d.arbol);
  } catch(e) {}
}

function renderFileTree(tree) {
  const container = document.getElementById('fileTreeContainer');
  if (!container) return;
  container.innerHTML = '';

  function buildNode(item, indent) {
    const row = document.createElement('div');
    row.className = 'tree-node';
    row.style.paddingLeft = `${indent * 12 + 8}px`;

    if (item.tipo === 'directorio') {
      row.innerHTML = `<span>📁</span> <span style="font-weight: 500;">${escapeHtml(item.nombre)}</span>`;
      container.appendChild(row);
      if (item.hijos && item.hijos.length > 0) {
        item.hijos.forEach(ch => buildNode(ch, indent + 1));
      }
    } else {
      const isAct = (item.ruta === currentFile);
      if (isAct) row.classList.add('active');
      row.innerHTML = `<span>${getFileIcon(item.nombre)}</span> <span>${escapeHtml(item.nombre)}</span>`;
      row.onclick = () => openFile(item.ruta);
      container.appendChild(row);
    }
  }

  if (Array.isArray(tree)) {
    tree.forEach(item => buildNode(item, 0));
  }
}

async function applyCodeToWorkspace(ruta, codigo) {
  if (!ruta) {
    ruta = prompt('Nombre del archivo destino en el workspace (ej: app.py):', 'main.py');
    if (!ruta) return;
  }

  try {
    const res = await fetch(`/api/social/apply_code?token=${encodeURIComponent(TOKEN)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ruta: ruta, codigo: codigo }),
    });
    const d = await res.json();
    if (d.ok) {
      showToast(`Código aplicado exitosamente a ${ruta}`);
      await openFile(ruta);
    } else {
      showToast('Error al aplicar código: ' + (d.error || ''));
    }
  } catch(e) {
    showToast('Error de conexión al aplicar código');
  }
}
