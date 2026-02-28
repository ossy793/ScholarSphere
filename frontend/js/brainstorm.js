import { api, requireAuth, getUser } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('unauthenticated');
renderLayout('Brainstorm', 'Brainstorm');

// ── State ─────────────────────────────────────────────────────────────────────
let documentContext  = '';   // plain text sent to AI
let chatHistory      = [];
let isWaiting        = false;
let currentBlobUrl   = null; // revoke on next upload
let currentSessionId = null; // DB session ID (null = unsaved)

const _user     = getUser();
const _initials = (_user?.full_name || 'U')
  .split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);

// ── Local-storage persistence (fast restore / fallback) ───────────────────────
const _BS_KEYS = [
  'ossyquiz_bs_ctx', 'ossyquiz_bs_tab', 'ossyquiz_bs_paste',
  'ossyquiz_bs_meta', 'ossyquiz_bs_session_id',
];

// ── IndexedDB — stores raw PDF bytes so the viewer survives page navigation ────
const _IDB = {
  _db: null,
  async db() {
    if (this._db) return this._db;
    this._db = await new Promise((resolve, reject) => {
      const req = indexedDB.open('ossyquiz_brainstorm', 1);
      req.onupgradeneeded = e => e.target.result.createObjectStore('pdfs');
      req.onsuccess = e => resolve(e.target.result);
      req.onerror   = e => reject(e.target.error);
    });
    return this._db;
  },
  async save(key, buf) {
    const db = await this.db();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('pdfs', 'readwrite');
      tx.objectStore('pdfs').put(buf, key);
      tx.oncomplete = resolve;
      tx.onerror    = e => reject(e.target.error);
    });
  },
  async get(key) {
    const db = await this.db();
    return new Promise((resolve, reject) => {
      const tx  = db.transaction('pdfs', 'readonly');
      const req = tx.objectStore('pdfs').get(key);
      req.onsuccess = e => resolve(e.target.result || null);
      req.onerror   = e => reject(e.target.error);
    });
  },
  async del(key) {
    try {
      const db = await this.db();
      await new Promise((resolve, reject) => {
        const tx = db.transaction('pdfs', 'readwrite');
        tx.objectStore('pdfs').delete(key);
        tx.oncomplete = resolve;
        tx.onerror    = e => reject(e.target.error);
      });
    } catch {}
  },
};

function _bsSave() {
  try {
    const tab = document.getElementById('tab-paste-btn')?.classList.contains('active') ? 'paste' : 'upload';
    localStorage.setItem('ossyquiz_bs_ctx',        documentContext);
    localStorage.setItem('ossyquiz_bs_tab',        tab);
    localStorage.setItem('ossyquiz_bs_paste',      document.getElementById('paste-textarea')?.value || '');
    localStorage.setItem('ossyquiz_bs_session_id', currentSessionId || '');
  } catch (e) {}
}

function _bsSaveMeta(name, extLabel, sizeLabel) {
  try { localStorage.setItem('ossyquiz_bs_meta', JSON.stringify({ name, extLabel, sizeLabel })); } catch (e) {}
}

function _bsClear() {
  const sid = localStorage.getItem('ossyquiz_bs_session_id');
  if (sid) _IDB.del(sid);
  _IDB.del('current_pdf');
  _IDB.del('current_docx');
  _BS_KEYS.forEach(k => localStorage.removeItem(k));
  currentSessionId = null;
}

// Fetch raw file bytes for a session from the backend (works for all stored sessions)
async function _fetchSessionFile(sessionId) {
  try {
    return await api.getBuffer(`/brainstorm/sessions/${sessionId}/file`);
  } catch {
    return null;
  }
}

async function _bsRestore() {
  // Prefer DB restore when a session ID is saved
  const savedSessionId = localStorage.getItem('ossyquiz_bs_session_id');
  if (savedSessionId) {
    try {
      await _loadSessionIntoView(savedSessionId, false);
      return;
    } catch {
      localStorage.removeItem('ossyquiz_bs_session_id');
    }
  }

  // Fallback: restore doc context from localStorage (no session)
  const ctx = localStorage.getItem('ossyquiz_bs_ctx');
  if (!ctx) return;
  documentContext = ctx;

  const tab       = localStorage.getItem('ossyquiz_bs_tab') || 'upload';
  const pasteText = localStorage.getItem('ossyquiz_bs_paste') || '';
  let   meta      = null;
  try { meta = JSON.parse(localStorage.getItem('ossyquiz_bs_meta') || 'null'); } catch {}

  const scroll = document.getElementById('doc-viewer-scroll');
  scroll.style.padding = '0';
  scroll.style.height  = '';

  if (tab === 'paste' && pasteText) {
    window.switchInputTab('paste');
    document.getElementById('paste-textarea').value = pasteText;
    scroll.innerHTML = `<pre class="txt-render">${escHtml(pasteText)}</pre>`;
    document.getElementById('doc-meta-bar').innerHTML = `
      <strong>Pasted Text</strong>
      <span class="badge-sm">TEXT</span>
      <span style="margin-left:auto">${pasteText.length.toLocaleString()} chars</span>`;
  } else {
    const isPdf  = meta?.extLabel === 'PDF';
    const isDocx = meta?.extLabel === 'DOCX';
    if (isPdf) {
      let buf = await _IDB.get('current_pdf').catch(() => null);
      if (!buf) {
        buf = await _fetchSessionFile(savedSessionId).catch(() => null);
        if (buf) await _IDB.save('current_pdf', buf).catch(() => {});
      }
      if (buf) {
        if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
        currentBlobUrl = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }));
        scroll.style.height = '100%';
        scroll.innerHTML = `<iframe src="${currentBlobUrl}#toolbar=1&navpanes=0"
          title="Document viewer" style="width:100%;height:100%;border:none;display:block;"></iframe>`;
      } else {
        scroll.style.height = '';
        scroll.innerHTML = `${pdfRestoredNotice()}<pre class="txt-render">${escHtml(ctx)}</pre>`;
      }
    } else if (isDocx && typeof mammoth !== 'undefined') {
      let buf = await _IDB.get('current_docx').catch(() => null);
      if (!buf) {
        buf = await _fetchSessionFile(savedSessionId).catch(() => null);
        if (buf) await _IDB.save('current_docx', buf).catch(() => {});
      }
      if (buf) {
        const htmlResult = await mammoth.convertToHtml({ arrayBuffer: buf });
        scroll.style.height = '';
        scroll.innerHTML = `<div class="docx-render">${htmlResult.value}</div>`;
      } else {
        scroll.style.height = '';
        scroll.innerHTML = `<pre class="txt-render">${escHtml(ctx)}</pre>`;
      }
    } else {
      scroll.style.height = '';
      scroll.innerHTML = `<pre class="txt-render">${escHtml(ctx)}</pre>`;
    }
    if (meta) {
      document.getElementById('doc-meta-bar').innerHTML = `
        <strong>${escHtml(meta.name)}</strong>
        <span class="badge-sm">${meta.extLabel}</span>
        <span style="margin-left:auto">${meta.sizeLabel}</span>`;
    }
  }

  document.getElementById('doc-upload-area').style.display = 'none';
  document.getElementById('doc-viewer-area').classList.add('visible');
  document.getElementById('doc-controls').innerHTML =
    `<button class="btn btn-secondary btn-sm" onclick="changeDocument()">Change</button>`;

  enableChat();
  clearChat(false);
}

// ── Session management ────────────────────────────────────────────────────────

async function createSession(filename, fileType, text, fileSizeBytes) {
  const title = fileType === 'paste'
    ? (text.slice(0, 70).replace(/\s+/g, ' ').trim() || 'Pasted Text')
    : filename;
  try {
    const res = await api.post('/brainstorm/sessions', {
      title,
      filename,
      file_type:       fileType,
      extracted_text:  text,
      file_size_bytes: fileSizeBytes || null,
    });
    currentSessionId = res.id;
    localStorage.setItem('ossyquiz_bs_session_id', currentSessionId);
    // Save PDF under session ID so history clicks can find it.
    // Keep 'current_pdf' as a fallback until the user explicitly changes document.
    if (fileType === 'pdf') {
      const buf = await _IDB.get('current_pdf').catch(() => null);
      if (buf) {
        await _IDB.save(currentSessionId, buf).catch(() => {});
      }
    }
  } catch (e) {
    console.warn('Could not save brainstorm session:', e.message);
  }
}

// ── Input-mode tab switching ──────────────────────────────────────────────────
window.switchInputTab = function (tab) {
  document.getElementById('tab-upload-panel').style.display = tab === 'upload' ? '' : 'none';
  document.getElementById('tab-paste-panel').style.display  = tab === 'paste'  ? '' : 'none';
  document.getElementById('tab-upload-btn').classList.toggle('active', tab === 'upload');
  document.getElementById('tab-paste-btn').classList.toggle('active',  tab === 'paste');
  document.getElementById('upload-error').classList.add('hidden');
};

// ── Paste-text submission ─────────────────────────────────────────────────────
window.submitPastedText = function () {
  const text  = document.getElementById('paste-textarea').value.trim();
  const errEl = document.getElementById('upload-error');
  errEl.classList.add('hidden');
  if (!text) {
    errEl.textContent = 'Please paste some text before continuing.';
    errEl.classList.remove('hidden');
    return;
  }
  handlePastedText(text);
};

function handlePastedText(text) {
  const truncated = text.length > 12000
    ? text.slice(0, 12000) + '\n... [content truncated]'
    : text;
  documentContext = truncated;

  const scroll = document.getElementById('doc-viewer-scroll');
  scroll.style.padding = '0';
  scroll.style.height  = '';
  scroll.innerHTML = `<pre class="txt-render">${escHtml(text)}</pre>`;

  document.getElementById('doc-upload-area').style.display = 'none';
  document.getElementById('doc-viewer-area').classList.add('visible');
  document.getElementById('doc-meta-bar').innerHTML = `
    <strong>Pasted Text</strong>
    <span class="badge-sm">TEXT</span>
    <span style="margin-left:auto">${text.length.toLocaleString()} chars</span>`;
  document.getElementById('doc-controls').innerHTML =
    `<button class="btn btn-secondary btn-sm" onclick="changeDocument()">Change</button>`;

  enableChat();
  clearChat(false);
  _bsSave();
  createSession('Pasted Text', 'paste', truncated, null);
}

// ── Drag & drop ───────────────────────────────────────────────────────────────
window.onDragOver = function (e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.add('drag-over');
};
window.onDragLeave = function () {
  document.getElementById('upload-zone').classList.remove('drag-over');
};
window.onDrop = function (e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
};
window.onFileSelected = function (e) {
  const file = e.target.files[0];
  if (file) handleFile(file);
  e.target.value = '';
};

// ── Core file handler ─────────────────────────────────────────────────────────
async function handleFile(file) {
  const errEl = document.getElementById('upload-error');
  errEl.classList.add('hidden');

  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!['.pdf', '.docx', '.txt'].includes(ext)) {
    errEl.textContent = 'Unsupported file. Please upload a PDF, DOCX, or TXT file.';
    errEl.classList.remove('hidden');
    return;
  }

  setLoadingState(file.name);
  const sessionIdBefore = currentSessionId;

  try {
    if (ext === '.pdf')  await handlePdf(file);
    if (ext === '.docx') await handleDocx(file);
    if (ext === '.txt')  await handleTxt(file);

    const extLabel = ext.slice(1).toUpperCase();
    showViewerArea(file.name, extLabel, file.size);
    enableChat();
    clearChat(false);
    // PDF and DOCX already create the session inside their handlers via the
    // combined endpoint. Only call createSession for TXT (no file bytes needed).
    if (currentSessionId === sessionIdBefore) {
      createSession(file.name, ext.slice(1), documentContext, file.size);
    }
  } catch (err) {
    resetUploadZone();
    errEl.textContent = err.message || 'Failed to load file. Please try again.';
    errEl.classList.remove('hidden');
  }
}

// ── PDF ───────────────────────────────────────────────────────────────────────
async function handlePdf(file) {
  if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);

  // Store raw bytes in IndexedDB so the PDF survives same-browser navigation
  const arrayBuffer = await file.arrayBuffer();
  await _IDB.save('current_pdf', arrayBuffer).catch(() => {});

  currentBlobUrl = URL.createObjectURL(new Blob([arrayBuffer], { type: 'application/pdf' }));

  const scroll = document.getElementById('doc-viewer-scroll');
  scroll.style.padding = '0';
  scroll.style.height  = '100%';
  scroll.innerHTML = `<iframe src="${currentBlobUrl}#toolbar=1&navpanes=0"
    title="Document viewer" style="width:100%;height:100%;border:none;display:block;"></iframe>`;

  // Combined endpoint: extract text + create session + persist file bytes on backend
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.postForm('/brainstorm/sessions/from-file', formData);
  documentContext = res.text;

  // Session is now created — store its ID and cache bytes under the session key too
  currentSessionId = res.id;
  localStorage.setItem('ossyquiz_bs_session_id', currentSessionId);
  await _IDB.save(currentSessionId, arrayBuffer).catch(() => {});
}

// ── DOCX ──────────────────────────────────────────────────────────────────────
async function handleDocx(file) {
  if (typeof mammoth === 'undefined') throw new Error('mammoth.js failed to load.');
  const arrayBuffer = await file.arrayBuffer();
  const htmlResult  = await mammoth.convertToHtml({ arrayBuffer });
  const textResult  = await mammoth.extractRawText({ arrayBuffer });

  const scroll = document.getElementById('doc-viewer-scroll');
  scroll.style.padding = '0';
  scroll.style.height  = '';
  scroll.innerHTML = `<div class="docx-render">${htmlResult.value}</div>`;

  let text = textResult.value.trim();
  if (!text) throw new Error('This DOCX file contains no extractable text.');
  if (text.length > 12000) text = text.slice(0, 12000) + '\n... [content truncated]';
  documentContext = text;

  // Combined endpoint: persist file bytes on backend and create session
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.postForm('/brainstorm/sessions/from-file', formData);
  // Use mammoth's extraction for AI context (consistent with what's displayed)
  // The session is now created — store its ID and cache bytes in IDB
  currentSessionId = res.id;
  localStorage.setItem('ossyquiz_bs_session_id', currentSessionId);
  await _IDB.save(currentSessionId, arrayBuffer).catch(() => {});
  await _IDB.save('current_docx', arrayBuffer).catch(() => {});
}

// ── TXT ───────────────────────────────────────────────────────────────────────
async function handleTxt(file) {
  const text = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = e => resolve(e.target.result);
    reader.onerror = () => reject(new Error('Failed to read file.'));
    reader.readAsText(file, 'utf-8');
  });
  if (!text.trim()) throw new Error('The text file appears to be empty.');

  const scroll = document.getElementById('doc-viewer-scroll');
  scroll.style.padding = '0';
  scroll.style.height  = '';
  scroll.innerHTML = `<pre class="txt-render">${escHtml(text)}</pre>`;

  documentContext = text.length > 12000
    ? text.slice(0, 12000) + '\n... [content truncated]'
    : text;
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function setLoadingState(filename) {
  document.getElementById('upload-zone').innerHTML = `
    <div class="upload-icon">⏳</div>
    <h3>Loading ${escHtml(filename)}…</h3>
    <p>Please wait</p>
    <div style="margin-top:12px"><span class="spinner spinner-dark"></span></div>`;
}

function resetUploadZone() {
  document.getElementById('upload-zone').innerHTML = `
    <div class="upload-icon">📄</div>
    <h3>Upload your material</h3>
    <p>Drop a file here or click to browse</p>
    <p class="text-xs" style="margin-bottom:16px">PDF · DOCX · TXT</p>
    <button class="btn btn-primary btn-sm"
      onclick="event.stopPropagation();document.getElementById('file-input').click()">
      Choose File
    </button>`;
}

function showViewerArea(filename, extLabel, sizeBytes) {
  document.getElementById('doc-upload-area').style.display = 'none';
  document.getElementById('doc-viewer-area').classList.add('visible');

  const kb = (sizeBytes / 1024).toFixed(1);
  document.getElementById('doc-meta-bar').innerHTML = `
    <strong>${escHtml(filename)}</strong>
    <span class="badge-sm">${extLabel}</span>
    <span style="margin-left:auto">${kb} KB</span>`;
  document.getElementById('doc-controls').innerHTML =
    `<button class="btn btn-secondary btn-sm" onclick="changeDocument()">Change File</button>`;

  _bsSaveMeta(filename, extLabel, kb + ' KB');
  _bsSave();
}

window.changeDocument = function () {
  _bsClear();
  if (currentBlobUrl) { URL.revokeObjectURL(currentBlobUrl); currentBlobUrl = null; }

  document.getElementById('doc-upload-area').style.display = '';
  document.getElementById('doc-viewer-area').classList.remove('visible');
  document.getElementById('doc-history-area').classList.remove('visible');
  document.getElementById('doc-controls').innerHTML = '';
  document.getElementById('upload-error').classList.add('hidden');
  document.getElementById('doc-viewer-scroll').innerHTML = '';
  document.getElementById('paste-textarea').value = '';
  resetUploadZone();
  switchInputTab('upload');

  documentContext  = '';
  currentSessionId = null;
  disableChat();
  clearChat(false);
};

// ── Chat enable / disable ─────────────────────────────────────────────────────
function enableChat() {
  const input = document.getElementById('chat-input');
  input.disabled    = false;
  input.placeholder = 'Ask anything about the document… (Enter to send)';
  document.getElementById('send-btn').disabled = false;
  input.focus();
}

function disableChat() {
  const input = document.getElementById('chat-input');
  input.disabled    = true;
  input.placeholder = 'Upload a file or paste text to start chatting…';
  document.getElementById('send-btn').disabled = true;
}

// ── Chat ──────────────────────────────────────────────────────────────────────
window.clearChat = function (showEmpty = true) {
  chatHistory = [];
  const container = document.getElementById('chat-messages');
  const msg = documentContext
    ? 'Your content is ready. Ask anything about it below.'
    : 'Upload a file or paste text on the left, then ask anything about it.';
  if (showEmpty || !documentContext) {
    container.innerHTML = `
      <div class="chat-empty" id="chat-empty">
        <div class="empty-icon">🧠</div>
        <h3>${documentContext ? 'Document loaded!' : 'Ready to Brainstorm'}</h3>
        <p>${msg}</p>
      </div>`;
  } else {
    container.innerHTML = '';
  }
};

window.sendMessage = async function () {
  if (isWaiting || !documentContext) return;
  const input = document.getElementById('chat-input');
  const text  = input.value.trim();
  if (!text) return;

  input.value = '';
  autoResize(input);

  document.getElementById('chat-empty')?.remove();
  appendBubble('user', text);
  chatHistory.push({ role: 'user', content: text });

  const typingId = showTyping();
  isWaiting = true;
  document.getElementById('send-btn').disabled = true;
  input.disabled = true;

  try {
    const res = await api.post('/brainstorm/chat', {
      message:    text,
      context:    documentContext,
      history:    chatHistory.slice(0, -1),
      session_id: currentSessionId || undefined,
    });
    removeTyping(typingId);
    appendBubble('assistant', res.reply);
    chatHistory.push({ role: 'assistant', content: res.reply });
    _bsSave();
  } catch (err) {
    removeTyping(typingId);
    appendBubble('assistant', `⚠️ ${err.message || 'Something went wrong. Please try again.'}`);
  } finally {
    isWaiting = false;
    document.getElementById('send-btn').disabled = false;
    input.disabled = false;
    input.focus();
  }
};

function appendBubble(role, content) {
  const container = document.getElementById('chat-messages');
  const html = role === 'assistant' ? renderMarkdown(content) : escHtml(content);
  const el   = document.createElement('div');
  el.className = `msg ${role}`;
  el.innerHTML = `
    <div class="msg-avatar">${role === 'user' ? _initials : 'AI'}</div>
    <div class="msg-bubble">${html}</div>`;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}

function showTyping() {
  const id = `typing-${Date.now()}`;
  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.id = id;
  el.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-bubble">
      <div class="typing-indicator"><span></span><span></span><span></span></div>
    </div>`;
  const c = document.getElementById('chat-messages');
  c.appendChild(el);
  c.scrollTop = c.scrollHeight;
  return id;
}

function removeTyping(id) { document.getElementById(id)?.remove(); }

// ── Input keyboard handling ───────────────────────────────────────────────────
window.onInputKeydown = function (e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
};

window.autoResize = function (el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
};

// ── Panel resize ──────────────────────────────────────────────────────────────
window.startResize = function (e) {
  e.preventDefault();
  const layout   = document.getElementById('bs-layout');
  const docPanel = document.getElementById('doc-panel');
  const startX   = e.clientX;
  const startW   = docPanel.offsetWidth;
  const totalW   = layout.offsetWidth;

  function onMove(ev) {
    const newW = Math.min(Math.max(startW + ev.clientX - startX, 260), totalW * 0.65);
    docPanel.style.width = newW + 'px';
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  }
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
};

// ── History panel ─────────────────────────────────────────────────────────────

window.openHistory = async function () {
  document.getElementById('doc-upload-area').style.display = 'none';
  document.getElementById('doc-viewer-area').classList.remove('visible');
  document.getElementById('doc-history-area').classList.add('visible');
  await loadHistory();
};

window.closeHistory = function () {
  document.getElementById('doc-history-area').classList.remove('visible');
  if (documentContext) {
    document.getElementById('doc-viewer-area').classList.add('visible');
  } else {
    document.getElementById('doc-upload-area').style.display = '';
  }
};

window.startNewSession = function () {
  window.changeDocument();
};

async function loadHistory() {
  const list = document.getElementById('history-list');
  list.innerHTML = `
    <div class="history-empty">
      <div class="empty-icon">⏳</div>
      <p>Loading sessions…</p>
    </div>`;
  try {
    const sessions = await api.get('/brainstorm/sessions');
    if (!sessions.length) {
      list.innerHTML = `
        <div class="history-empty">
          <div class="empty-icon">🕐</div>
          <p>No past sessions yet.<br>Upload a document or paste text to start!</p>
        </div>`;
      return;
    }
    list.innerHTML = sessions.map(renderSessionItem).join('');
  } catch (e) {
    list.innerHTML = `
      <div class="history-empty">
        <div class="empty-icon">⚠️</div>
        <p>Failed to load history.<br>${escHtml(e.message)}</p>
      </div>`;
  }
}

function renderSessionItem(s) {
  const icon  = fileTypeIcon(s.document?.file_type);
  const date  = formatRelativeDate(s.updated_at);
  const msgs  = s.message_count;
  const title = escHtml(s.title);
  const active = s.id === currentSessionId ? ' active' : '';
  return `
    <div class="history-item${active}" onclick="openSession('${s.id}')">
      <div class="history-item-icon">${icon}</div>
      <div class="history-item-body">
        <div class="history-item-title" id="hist-title-${s.id}">${title}</div>
        <div class="history-item-meta">${date} &middot; ${msgs} message${msgs !== 1 ? 's' : ''}</div>
      </div>
      <div class="history-item-actions">
        <button class="history-action-btn" title="Rename"
          onclick="event.stopPropagation();startRenameSession('${s.id}','${title}')">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
        <button class="history-action-btn danger" title="Delete"
          onclick="event.stopPropagation();deleteSession('${s.id}',this)">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>
            <path d="M10 11v6M14 11v6"/>
            <path d="M9 6V4h6v2"/>
          </svg>
        </button>
      </div>
    </div>`;
}

async function _loadSessionIntoView(sessionId, closeHistoryPanel = true) {
  const session = await api.get(`/brainstorm/sessions/${sessionId}`);

  if (closeHistoryPanel) {
    document.getElementById('doc-history-area').classList.remove('visible');
  }

  if (session.document) {
    documentContext = session.document.extracted_text;

    const scroll = document.getElementById('doc-viewer-scroll');
    const isPdf  = session.document.file_type === 'pdf';
    scroll.style.padding = '0';

    if (isPdf) {
      let buf = await _IDB.get(sessionId).catch(() => null);
      // Fallback 1: race condition — bytes may still be under the temp key
      if (!buf && sessionId === localStorage.getItem('ossyquiz_bs_session_id')) {
        buf = await _IDB.get('current_pdf').catch(() => null);
        if (buf) await _IDB.save(sessionId, buf).catch(() => {});
      }
      // Fallback 2: fetch from backend (persistent, works for all sessions)
      if (!buf) {
        buf = await _fetchSessionFile(sessionId);
        if (buf) await _IDB.save(sessionId, buf).catch(() => {});
      }
      if (buf) {
        if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
        currentBlobUrl = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }));
        scroll.style.height = '100%';
        scroll.innerHTML = `<iframe src="${currentBlobUrl}#toolbar=1&navpanes=0"
          title="Document viewer" style="width:100%;height:100%;border:none;display:block;"></iframe>`;
      } else {
        scroll.style.height = '';
        scroll.innerHTML = `${pdfRestoredNotice()}<pre class="txt-render">${escHtml(documentContext)}</pre>`;
      }
    } else if (session.document.file_type === 'docx' && typeof mammoth !== 'undefined') {
      // Try to re-render DOCX from stored bytes
      let buf = await _IDB.get(sessionId).catch(() => null);
      if (!buf && sessionId === localStorage.getItem('ossyquiz_bs_session_id')) {
        buf = await _IDB.get('current_docx').catch(() => null);
        if (buf) await _IDB.save(sessionId, buf).catch(() => {});
      }
      if (!buf) {
        buf = await _fetchSessionFile(sessionId);
        if (buf) await _IDB.save(sessionId, buf).catch(() => {});
      }
      if (buf) {
        const htmlResult = await mammoth.convertToHtml({ arrayBuffer: buf });
        scroll.style.height = '';
        scroll.innerHTML = `<div class="docx-render">${htmlResult.value}</div>`;
      } else {
        scroll.style.height = '';
        scroll.innerHTML = `<pre class="txt-render">${escHtml(documentContext)}</pre>`;
      }
    } else {
      scroll.style.height = '';
      scroll.innerHTML = `<pre class="txt-render">${escHtml(documentContext)}</pre>`;
    }

    const sizeLabel = session.document.file_size_bytes
      ? `${(session.document.file_size_bytes / 1024).toFixed(1)} KB`
      : `${documentContext.length.toLocaleString()} chars`;

    document.getElementById('doc-meta-bar').innerHTML = `
      <strong>${escHtml(session.document.filename)}</strong>
      <span class="badge-sm">${session.document.file_type.toUpperCase()}</span>
      <span style="margin-left:auto">${sizeLabel}</span>`;

    document.getElementById('doc-upload-area').style.display = 'none';
    document.getElementById('doc-viewer-area').classList.add('visible');
    document.getElementById('doc-controls').innerHTML =
      `<button class="btn btn-secondary btn-sm" onclick="changeDocument()">Change</button>`;
  }

  // Restore chat
  chatHistory = session.messages.map(m => ({ role: m.role, content: m.content }));
  const container = document.getElementById('chat-messages');
  if (chatHistory.length > 0) {
    container.innerHTML = '';
    chatHistory.forEach(msg => appendBubble(msg.role, msg.content));
  } else {
    clearChat(false);
  }

  currentSessionId = sessionId;
  localStorage.setItem('ossyquiz_bs_session_id', sessionId);
  _bsSave();
  enableChat();
}

window.openSession = async function (sessionId) {
  try {
    await _loadSessionIntoView(sessionId, true);
  } catch (e) {
    alert('Could not load session: ' + e.message);
  }
};

window.deleteSession = async function (sessionId, btn) {
  const item = btn.closest('.history-item');
  item.style.opacity = '0.4';
  item.style.pointerEvents = 'none';
  try {
    await api.del(`/brainstorm/sessions/${sessionId}`);
    item.remove();
    if (currentSessionId === sessionId) {
      currentSessionId = null;
      localStorage.removeItem('ossyquiz_bs_session_id');
    }
    // Show empty state if list is now empty
    const list = document.getElementById('history-list');
    if (!list.querySelector('.history-item')) {
      list.innerHTML = `
        <div class="history-empty">
          <div class="empty-icon">🕐</div>
          <p>No past sessions yet.<br>Upload a document or paste text to start!</p>
        </div>`;
    }
  } catch (e) {
    item.style.opacity = '';
    item.style.pointerEvents = '';
    alert('Could not delete session: ' + e.message);
  }
};

window.startRenameSession = function (sessionId, currentTitle) {
  const titleEl = document.getElementById(`hist-title-${sessionId}`);
  if (!titleEl) return;
  // Decode HTML entities for the input value
  const raw = currentTitle.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"');
  titleEl.innerHTML = `
    <input class="history-rename-input" id="rename-input-${sessionId}"
      value="${escHtml(raw)}"
      onblur="finishRenameSession('${sessionId}')"
      onkeydown="if(event.key==='Enter')this.blur();if(event.key==='Escape'){this.dataset.cancel='1';this.blur();}">`;
  const input = document.getElementById(`rename-input-${sessionId}`);
  input.focus();
  input.select();
};

window.finishRenameSession = async function (sessionId) {
  const input = document.getElementById(`rename-input-${sessionId}`);
  const titleEl = document.getElementById(`hist-title-${sessionId}`);
  if (!input || !titleEl) return;

  if (input.dataset.cancel === '1') {
    titleEl.textContent = input.value; // restore — but we don't have original easily
    await loadHistory(); // just reload
    return;
  }

  const newTitle = input.value.trim();
  if (!newTitle) { await loadHistory(); return; }

  try {
    await api.put(`/brainstorm/sessions/${sessionId}`, { title: newTitle });
    titleEl.textContent = newTitle;
  } catch (e) {
    await loadHistory();
  }
};

// ── Utilities ─────────────────────────────────────────────────────────────────

function fileTypeIcon(type) {
  const icons = { pdf: '📕', docx: '📘', txt: '📄', paste: '📝' };
  return icons[type] || '📄';
}

function formatRelativeDate(isoStr) {
  const d    = new Date(isoStr);
  const diff = (Date.now() - d) / 1000;
  if (diff < 60)     return 'just now';
  if (diff < 3600)   return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400)  return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// ── Markdown renderer ─────────────────────────────────────────────────────────
function renderMarkdown(text) {
  let s = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  s = s.replace(/```[\s\S]*?```/g, m => {
    const code = m.slice(3,-3).replace(/^[a-z]+\n/,'');
    return `<pre style="background:#f1f5f9;padding:10px;border-radius:6px;overflow-x:auto;font-size:0.82em;margin:8px 0"><code>${code}</code></pre>`;
  });
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  s = s.replace(/^#{1,3} (.+)$/gm, '<strong style="font-size:1.02em">$1</strong>');
  s = s.replace(/^[-•*] (.+)$/gm, '<li>$1</li>');
  s = s.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
  s = s.replace(/<\/ul>\s*<ul>/g, '');
  s = s.split(/\n\n+/).map(p => p.trim()).filter(p => p).map(p => p.startsWith('<') ? p : `<p>${p}</p>`).join('');
  s = s.replace(/\n/g, '<br>');
  return s;
}

function pdfRestoredNotice() {
  return `<div style="
      background:#fffbeb;border-bottom:1px solid #fde68a;
      padding:10px 16px;font-size:.82rem;color:#92400e;
      display:flex;align-items:center;gap:8px;flex-shrink:0">
    📄
    <span>
      PDF viewer is only available when the file is freshly uploaded.
      <strong>Re-upload the file</strong> to see the full PDF —
      or just use the chat below, it still has all the content.
    </span>
    <button onclick="changeDocument()"
      style="margin-left:auto;padding:4px 10px;font-size:.78rem;font-weight:600;
             border:1px solid #f59e0b;border-radius:5px;background:#fef3c7;
             color:#92400e;cursor:pointer;white-space:nowrap;flex-shrink:0">
      Re-upload PDF
    </button>
  </div>`;
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Restore saved state on page load ──────────────────────────────────────────
window.addEventListener('beforeunload', _bsSave);
_bsRestore().catch(console.error);
