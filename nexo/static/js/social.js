/**
 * NEXO Studio 2.0 — Red Social y Matriz de Colaboración (4 Entidades)
 */

let SOCIAL_FEED = [];
let SOCIAL_ENTIDADES = {};
let SOCIAL_PRESENCIA = {};

function initSocialModule() {
  const btnPub = document.getElementById('btnPublishSocialPost');
  if (btnPub) btnPub.onclick = publishSocialPost;

  const btnToggleCode = document.getElementById('btnToggleComposerCode');
  if (btnToggleCode) btnToggleCode.onclick = toggleAttachedCodeBox;

  const btnAttach = document.getElementById('btnAttachActiveFile');
  if (btnAttach) btnAttach.onclick = attachCurrentEditorFile;

  const input = document.getElementById('socialPostInput');
  if (input) {
    input.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        publishSocialPost();
      }
    });
  }

  // Carga inicial vía REST por si el WS está conectando
  fetch(`/api/social/feed?token=${encodeURIComponent(TOKEN)}`)
    .then(r => r.json())
    .then(d => {
      if (d.ok) renderSocialFeed(d.feed, d.entidades, d.presencia);
    })
    .catch(() => {});
}

function toggleAttachedCodeBox() {
  const box = document.getElementById('composerCodeDrawer');
  if (!box) return;
  const isHidden = (box.style.display === 'none' || !box.style.display);
  box.style.display = isHidden ? 'flex' : 'none';
  if (isHidden) {
    const codeInput = document.getElementById('socialPostCode');
    if (codeInput) codeInput.focus();
  }
}

function attachCurrentEditorFile() {
  if (!currentFile || !openFiles[currentFile] || !monacoEditor) {
    showToast('Abre primero un archivo en el editor para adjuntarlo.');
    return;
  }
  const box = document.getElementById('composerCodeDrawer');
  if (box) box.style.display = 'flex';

  const fnInput = document.getElementById('composerAttachedFileName');
  if (fnInput) fnInput.value = currentFile;

  const codeInput = document.getElementById('socialPostCode');
  if (codeInput) codeInput.value = monacoEditor.getValue();

  showToast(`Archivo adjunto al post: ${currentFile}`);
}

function insertMention(mention) {
  const input = document.getElementById('socialPostInput');
  if (!input) return;
  const val = input.value;
  if (val && !val.endsWith(' ')) input.value += ' ';
  input.value += mention + ' ';
  input.focus();
}

function formatMentions(text) {
  if (!text) return '';
  let escaped = escapeHtml(text);
  escaped = escaped.replace(/(@hermes-daniel\b)/gi, '<span class="post-mention hermes-daniel">$1</span>');
  escaped = escaped.replace(/(@hermes-amigo\b)/gi, '<span class="post-mention hermes-amigo">$1</span>');
  escaped = escaped.replace(/(@daniel\b)/gi, '<span class="post-mention daniel">$1</span>');
  escaped = escaped.replace(/(@amigo\b)/gi, '<span class="post-mention amigo">$1</span>');
  escaped = escaped.replace(/(@todos\b|@all\b)/gi, '<span class="post-mention todos">$1</span>');
  return escaped.replace(/\n/g, '<br>');
}

function renderSocialFeed(feed, entidades, presencia) {
  if (entidades) SOCIAL_ENTIDADES = entidades;
  if (presencia) SOCIAL_PRESENCIA = presencia;
  if (feed) SOCIAL_FEED = feed;

  updatePresenceUI();

  const container = document.getElementById('socialTimelineContainer');
  if (!container) return;
  container.innerHTML = '';

  if (!SOCIAL_FEED || SOCIAL_FEED.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); padding: 48px 16px;">
        <div style="font-size: 32px; margin-bottom: 8px;">⚛</div>
        <div style="font-size: 14px; font-weight: 500;">No hay publicaciones en la red social aún.</div>
        <div style="font-size: 12px; margin-top: 4px;">Publica un mensaje o menciona a @hermes-daniel o @hermes-amigo para iniciar.</div>
      </div>
    `;
    return;
  }

  SOCIAL_FEED.forEach(post => {
    container.appendChild(createPostElement(post));
  });
}

function updatePresenceUI(presencia) {
  if (presencia) SOCIAL_PRESENCIA = presencia;
  for (const [id, info] of Object.entries(SOCIAL_PRESENCIA)) {
    const dot = document.getElementById(`statusDot-${id}`);
    const msg = document.getElementById(`statusMsg-${id}`);
    if (dot) {
      if (info.estado === 'online') dot.className = 'status-dot online';
      else if (info.estado === 'ready') dot.className = (id.includes('daniel') ? 'status-dot hermes' : 'status-dot gemini');
      else dot.className = 'status-dot';
    }
    if (msg && info.actividad) {
      msg.innerHTML = `<span>${info.estado === 'online' ? '📍' : '⚡'}</span> <span>${escapeHtml(info.actividad)}</span>`;
    }
  }
}

function createPostElement(post) {
  const el = document.createElement('div');
  el.className = `post-card author-${post.autor}`;
  el.id = `post-${post.id}`;

  const ent = SOCIAL_ENTIDADES[post.autor] || {
    nombre: post.autor,
    avatar: '👤',
    color: '#06b6d4',
    badge: 'DEV',
    rol: post.rol || 'Miembro',
  };

  const isDebate = post.tags && post.tags.includes('debate');

  // Code Block
  let codeHtml = '';
  if (post.codigo) {
    const fn = post.archivo || 'codigo_propuesto.py';
    codeHtml = `
      <div class="post-code-block">
        <div class="post-code-header">
          <span>📄 ${escapeHtml(fn)}</span>
          <div class="post-code-actions">
            <button type="button" class="post-code-btn" onclick="openFileInEditorFromSocial(${JSON.stringify(post.codigo)}, ${JSON.stringify(post.archivo || '')})">
              📥 Abrir en Editor
            </button>
            <button type="button" class="post-code-btn apply" onclick="applyCodeToWorkspace(${JSON.stringify(fn)}, ${JSON.stringify(post.codigo)})">
              💾 Aplicar al archivo
            </button>
          </div>
        </div>
        <pre class="post-code-content"><code>${escapeHtml(post.codigo)}</code></pre>
      </div>
    `;
  }

  // Tags
  let tagsHtml = '';
  if (post.tags && post.tags.length > 0) {
    tagsHtml = `<div class="post-tags-bar">${post.tags.map(t => `<span class="post-tag-item">#${escapeHtml(t)}</span>`).join('')}</div>`;
  }

  const reactionsHtml = renderReactionsHtml(post.id, post.reacciones);
  const repliesListHtml = (post.respuestas || []).map(r => renderReplyHtml(post.id, r)).join('');

  el.innerHTML = `
    <div class="post-header">
      <div class="post-author-box">
        <div class="post-author-avatar" style="border-color: ${ent.color};">${ent.avatar}</div>
        <div>
          <div class="post-author-name">
            <span>${escapeHtml(ent.nombre)}</span>
            <span class="post-badge" style="color: ${ent.color}; border-color: ${ent.color}55; background: ${ent.color}15;">${ent.badge || 'DEV'}</span>
          </div>
          <div style="font-size: 11px; color: var(--text-muted);">${escapeHtml(ent.rol || '')}</div>
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        ${isDebate ? '<span class="post-debate-tag">⚡ DEBATE IA</span>' : ''}
        <span class="post-timestamp">${escapeHtml(post.timestamp || '')}</span>
      </div>
    </div>

    <div class="post-body">${formatMentions(post.contenido)}</div>
    ${codeHtml}
    ${tagsHtml}

    <div class="post-footer">
      <div class="post-reactions-bar" id="reactions-${post.id}">
        ${reactionsHtml}
      </div>
      <div class="post-actions-bar">
        <button type="button" class="action-btn debate" onclick="triggerDebateModal('${post.id}')" title="Iniciar debate técnico entre Hermes-Daniel y Hermes-Amigo">
          ⚡ Debate IA
        </button>
        <button type="button" class="action-btn" onclick="focusReplyInput('${post.id}')">
          💬 Responder (${(post.respuestas || []).length})
        </button>
      </div>
    </div>

    <div class="post-replies-wrapper" id="replies-${post.id}">
      <div class="replies-list" id="replies-list-${post.id}">
        ${repliesListHtml}
      </div>
      <div id="typing-indicator-${post.id}" style="display: none;"></div>
      <div class="reply-input-box">
        <textarea class="reply-textarea" id="reply-input-${post.id}" placeholder="Escribe una respuesta... (menciona a @hermes-daniel o @hermes-amigo)"></textarea>
        <button type="button" class="btn-reply-send" onclick="sendReply('${post.id}')">Enviar</button>
      </div>
    </div>
  `;

  return el;
}

function renderReplyHtml(postId, reply) {
  const ent = SOCIAL_ENTIDADES[reply.autor] || {
    nombre: reply.autor,
    avatar: '👤',
    color: '#06b6d4',
    badge: 'DEV',
    rol: reply.rol || '',
  };

  const isIA = reply.tipo_autor === 'ia' || reply.autor.startsWith('hermes');
  const cardClass = reply.autor === 'hermes-daniel' ? 'author-hermes-daniel' : (reply.autor === 'hermes-amigo' ? 'author-hermes-amigo' : '');

  let codeSnippet = '';
  if (reply.codigo) {
    const fn = reply.archivo || 'propuesta.py';
    codeSnippet = `
      <div class="post-code-block" style="margin-top: 8px;">
        <div class="post-code-header">
          <span>Propuesta de Código (${escapeHtml(fn)})</span>
          <div class="post-code-actions">
            <button type="button" class="post-code-btn" onclick="openFileInEditorFromSocial(${JSON.stringify(reply.codigo)}, ${JSON.stringify(fn)})">
              📥 Abrir en Editor
            </button>
            <button type="button" class="post-code-btn apply" onclick="applyCodeToWorkspace(${JSON.stringify(fn)}, ${JSON.stringify(reply.codigo)})">
              💾 Aplicar al archivo
            </button>
          </div>
        </div>
        <pre class="post-code-content"><code>${escapeHtml(reply.codigo)}</code></pre>
      </div>
    `;
  }

  return `
    <div class="reply-card ${cardClass}" id="reply-${reply.id}">
      <div class="reply-header">
        <div class="reply-author-row">
          <span>${ent.avatar}</span>
          <span style="color: ${ent.color};">${escapeHtml(ent.nombre)}</span>
          <span class="entity-hero-badge ${reply.autor}">${ent.badge || (isIA ? 'IA' : 'DEV')}</span>
        </div>
        <span style="font-size: 10.5px; color: var(--text-muted);">${escapeHtml(reply.timestamp || '')}</span>
      </div>
      <div class="reply-body">${formatMentions(reply.contenido)}</div>
      ${codeSnippet}
    </div>
  `;
}

function renderReactionsHtml(postId, reacciones) {
  const emojis = ['🔥', '🚀', '💡', '🤖', '❤️'];
  if (!reacciones) reacciones = {};
  return emojis.map(emoji => {
    const list = reacciones[emoji] || [];
    const count = list.length;
    const active = list.includes(CURRENT_USER);
    return `
      <button type="button" class="reaction-pill ${active ? 'active' : ''}" onclick="toggleReaction('${postId}', '${emoji}')" title="${list.join(', ') || 'Sé el primero en reaccionar'}">
        <span>${emoji}</span>
        <span>${count > 0 ? count : ''}</span>
      </button>
    `;
  }).join('');
}

function openFileInEditorFromSocial(code, filename) {
  switchView('editor');
  if (filename) {
    currentFile = filename;
    const b = document.getElementById('breadcrumbCurrentFile');
    if (b) b.innerText = filename;
  }
  if (monacoEditor) {
    monacoEditor.setValue(code || '');
    if (filename) {
      monaco.editor.setModelLanguage(monacoEditor.getModel(), getFileLanguage(filename));
    }
  }
  showToast('Código cargado en Monaco Editor');
}

async function publishSocialPost() {
  const input = document.getElementById('socialPostInput');
  const contenido = input ? input.value.trim() : '';
  if (!contenido) {
    showToast('Por favor escribe algo para publicar');
    return;
  }

  const codeInput = document.getElementById('socialPostCode');
  const codigo = codeInput ? codeInput.value.trim() : '';
  const fileInput = document.getElementById('composerAttachedFileName');
  const archivo = fileInput ? fileInput.value.trim() : '';
  const debate = document.getElementById('checkAutoDebate') ? document.getElementById('checkAutoDebate').checked : false;

  const tags = [];
  if (debate) tags.push('debate');
  if (codigo) tags.push('codigo');
  if (archivo) tags.push('archivo');

  wsSend({
    tipo: 'social_post',
    autor: CURRENT_USER,
    contenido: contenido,
    codigo: codigo || null,
    archivo: archivo || null,
    tags: tags,
    debate: debate,
  });

  input.value = '';
  if (codeInput) codeInput.value = '';
  const box = document.getElementById('composerCodeDrawer');
  if (box) box.style.display = 'none';
  const chk = document.getElementById('checkAutoDebate');
  if (chk) chk.checked = false;

  showToast('Publicación enviada a la Red Social');
}

function sendReply(postId) {
  const input = document.getElementById(`reply-input-${postId}`);
  const text = input ? input.value.trim() : '';
  if (!text) return;

  wsSend({
    tipo: 'social_reply',
    post_id: postId,
    autor: CURRENT_USER,
    contenido: text,
  });

  input.value = '';
}

function focusReplyInput(postId) {
  const input = document.getElementById(`reply-input-${postId}`);
  if (input) input.focus();
}

function toggleReaction(postId, emoji) {
  wsSend({
    tipo: 'social_react',
    post_id: postId,
    emoji: emoji,
    usuario: CURRENT_USER,
  });
}

function triggerDebateModal(postId) {
  const post = SOCIAL_FEED.find(p => p.id === postId);
  const defTema = post ? post.contenido.slice(0, 75) : '';
  const tema = prompt('Tema o aspecto técnico a debatir entre Hermes-Daniel y Hermes-Amigo:', defTema);
  if (!tema) return;

  wsSend({
    tipo: 'social_debate',
    post_id: postId,
    tema: tema,
    codigo: post ? post.codigo : null,
    archivo: post ? post.archivo : null,
  });

  showToast('⚡ Debate IA iniciado entre Hermes-Daniel y Hermes-Amigo');
}

// WebSocket Event Handlers
function onSocialNewPost(post) {
  const exists = SOCIAL_FEED.some(p => p.id === post.id);
  if (!exists) {
    SOCIAL_FEED.unshift(post);
    const container = document.getElementById('socialTimelineContainer');
    if (container) {
      const el = createPostElement(post);
      container.insertBefore(el, container.firstChild);
    }
  }
  showToast(`Nuevo post de ${post.autor}`);
}

function onSocialNewReply(postId, respuesta) {
  const post = SOCIAL_FEED.find(p => p.id === postId);
  if (post) {
    if (!post.respuestas) post.respuestas = [];
    const exists = post.respuestas.some(r => r.id === respuesta.id);
    if (!exists) post.respuestas.push(respuesta);

    const list = document.getElementById(`replies-list-${postId}`);
    if (list) {
      const div = document.createElement('div');
      div.innerHTML = renderReplyHtml(postId, respuesta);
      list.appendChild(div.firstElementChild);
    }
    const tip = document.getElementById(`typing-indicator-${postId}`);
    if (tip) tip.style.display = 'none';
  }
}

function onSocialReactionUpdate(postId, replyId, reacciones) {
  const post = SOCIAL_FEED.find(p => p.id === postId);
  if (post) {
    post.reacciones = reacciones;
    const bar = document.getElementById(`reactions-${postId}`);
    if (bar) bar.innerHTML = renderReactionsHtml(postId, reacciones);
  }
}

function onSocialPresenceUpdate(presencia) {
  updatePresenceUI(presencia);
}

function onSocialIaTyping(postId, ia) {
  const tip = document.getElementById(`typing-indicator-${postId}`);
  if (tip) {
    tip.style.display = 'flex';
    const isCritic = (ia === 'hermes-amigo');
    tip.className = `ai-typing-card ${isCritic ? 'critic' : ''}`;
    tip.innerHTML = `<span>🤖</span> <span>${isCritic ? 'Hermes-Amigo está auditando código y redactando observaciones...' : 'Hermes-Daniel está pensando la propuesta técnica...'}</span>`;
  }
}
