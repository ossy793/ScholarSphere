import { api, requireAuth, getUser, getToken } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('unauthenticated');
renderLayout('Study Zone', 'Study Zone');

// ── State ─────────────────────────────────────────────────────────────────────
let documentContext  = '';   // plain text sent to AI
let chatHistory      = [];
let isWaiting        = false;
let currentBlobUrl   = null; // revoke on next upload
let currentSessionId = null; // DB session ID (null = unsaved)
let currentFilename  = 'Document'; // used for summary PDF filename

// ── Model selection ───────────────────────────────────────────────────────────
const _MODEL_COLORS = { groq: '#f97316', openai: '#10a37f', gemini: '#4285f4' };

// SVG logos for each provider
const _MODEL_LOGOS = {
  groq: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect width="24" height="24" rx="6" fill="#f97316"/>
    <path d="M12 5.5C8.41 5.5 5.5 8.41 5.5 12C5.5 15.59 8.41 18.5 12 18.5C15.59 18.5 18.5 15.59 18.5 12V10.5H12V13.5H15.6C15.03 15.48 13.68 16.5 12 16.5C9.52 16.5 7.5 14.48 7.5 12C7.5 9.52 9.52 7.5 12 7.5C13.12 7.5 14.14 7.92 14.91 8.62L16.74 6.79C15.49 5.67 13.82 5 12 5.5Z" fill="white"/>
  </svg>`,
  openai: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect width="24" height="24" rx="6" fill="#10a37f"/>
    <path d="M19.2 9.46a4.36 4.36 0 0 0-.38-3.59 4.41 4.41 0 0 0-4.74-2.11A4.42 4.42 0 0 0 3.71 5.88a4.36 4.36 0 0 0-2.91 2.12 4.41 4.41 0 0 0 .54 5.17 4.36 4.36 0 0 0 .37 3.59 4.41 4.41 0 0 0 4.75 2.11 4.36 4.36 0 0 0 3.28 1.46 4.41 4.41 0 0 0 4.21-3.06 4.36 4.36 0 0 0 2.91-2.12 4.41 4.41 0 0 0-.54-5.69zM12.74 18.5a3.27 3.27 0 0 1-2.1-.76l.1-.06 3.49-2.01a.58.58 0 0 0 .29-.5V10.8l1.47.85a.05.05 0 0 1 .03.04v4.07a3.29 3.29 0 0 1-3.28 3.28zM5.66 15.8a3.27 3.27 0 0 1-.39-2.2l.1.06 3.49 2.01a.56.56 0 0 0 .57 0l4.26-2.46v1.7a.06.06 0 0 1-.02.05L9.44 17.1a3.29 3.29 0 0 1-3.78-1.3zM4.9 8.63a3.27 3.27 0 0 1 1.72-1.44v4.14a.56.56 0 0 0 .28.49l4.24 2.45-1.47.85a.06.06 0 0 1-.05 0L5.11 12.6A3.29 3.29 0 0 1 4.9 8.63zm10.78 2.82-4.25-2.46 1.47-.85a.06.06 0 0 1 .05 0l3.52 2.03a3.28 3.28 0 0 1-.49 5.91v-4.14a.58.58 0 0 0-.3-.49zm1.46-2.21-.1-.06-3.48-2.03a.57.57 0 0 0-.57 0L8.73 9.61V7.9a.05.05 0 0 1 .02-.04l3.52-2.03a3.29 3.29 0 0 1 4.87 3.4zm-9.21 3.01-1.47-.85a.06.06 0 0 1-.03-.04V8.28a3.29 3.29 0 0 1 5.38-2.52l-.1.06L8.23 7.83a.58.58 0 0 0-.29.5zm.8-1.72 1.9-1.1 1.9 1.1v2.19l-1.89 1.09-1.9-1.09z" fill="white"/>
  </svg>`,
  gemini: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect width="24" height="24" rx="6" fill="#4285f4"/>
    <path d="M12 3C12 3 12 8.5 7 12C12 15.5 12 21 12 21C12 21 12 15.5 17 12C12 8.5 12 3 12 3Z" fill="white"/>
    <path d="M3 12C3 12 8.5 12 12 7C15.5 12 21 12 21 12C21 12 15.5 12 12 17C8.5 12 3 12 3 12Z" fill="white" opacity="0.7"/>
  </svg>`,
};

let _selectedModel  = localStorage.getItem('sz_model') || 'groq';

// All models always shown and selectable — backend merge updates availability
let _availableModels = [
  { id: 'groq',   name: 'Llama 3.3 70B',    provider: 'Groq',   display_name: 'Groq',    available: true },
  { id: 'openai', name: 'GPT-4o mini',       provider: 'OpenAI', display_name: 'ChatGPT', available: true },
  { id: 'gemini', name: 'Gemini 2.0 Flash',  provider: 'Google', display_name: 'Gemini',  available: true },
];

async function _loadModels() {
  try {
    const fromApi = await api.get('/brainstorm/models');
    // Merge API availability into our full list
    _availableModels = _availableModels.map(m => {
      const api = fromApi.find(a => a.id === m.id);
      return api ? { ...m, ...api } : m;
    });
    // Add any models the backend knows about that we don't have hardcoded
    fromApi.forEach(a => {
      if (!_availableModels.find(m => m.id === a.id)) _availableModels.push(a);
    });
  } catch (_) {
    // Keep hardcoded defaults — Groq stays available, others disabled
  }
  // If selected model is not available, fall back to first available
  if (!_availableModels.find(m => m.id === _selectedModel && m.available !== false)) {
    _selectedModel = _availableModels.find(m => m.available !== false)?.id || 'groq';
  }
  _renderModelPicker();
  _updateModelUI();
  // Re-render setup cards in case overlay is already visible
  _renderSetupModelCards();
}

function _renderModelPicker() {
  const picker = document.getElementById('model-picker');
  if (!picker) return;
  const header = `<div class="model-picker-header">Choose AI Model</div>`;
  picker.innerHTML = header + _availableModels.map(m => {
    const isActive    = m.id === _selectedModel;
    const isDisabled  = m.available === false;
    const logo        = _MODEL_LOGOS[m.id] || `<span style="width:18px;height:18px;border-radius:5px;background:${_MODEL_COLORS[m.id]||'#888'};display:inline-block"></span>`;
    const displayName = m.display_name || m.provider;
    return `
    <button class="model-option${isActive ? ' active' : ''}${isDisabled ? ' disabled' : ''}"
      onclick="${isDisabled ? '' : `selectModel('${m.id}')`}"
      ${isDisabled ? 'disabled title="API key not configured"' : ''}>
      <span class="model-logo-wrap">${logo}</span>
      <span class="model-option-info">
        <span class="model-option-name">UrPadi × ${displayName}</span>
        <span class="model-option-provider">${m.name}${isDisabled ? ' · key not set' : ''}</span>
      </span>
      ${isActive ? '<span class="model-option-badge">Active</span>' : ''}
    </button>`;
  }).join('');
}

function _updateModelUI() {
  const m = _availableModels.find(x => x.id === _selectedModel);
  if (!m) return;
  const displayName = m.display_name || m.provider;
  const logo        = _MODEL_LOGOS[m.id];
  const dot         = document.getElementById('model-dot');
  const label       = document.getElementById('model-btn-label');
  const power       = document.getElementById('model-powered-label');
  const logoWrap    = document.getElementById('model-header-logo');
  if (dot)      dot.style.background = _MODEL_COLORS[m.id] || '#888';
  if (label)    label.textContent    = displayName;
  if (power)    power.innerHTML      = `powered by <strong>${displayName}</strong>`;
  if (logoWrap && logo) logoWrap.innerHTML = logo;
  // Update empty-state model badge
  const emptyBadge = document.getElementById('empty-model-badge');
  if (emptyBadge) {
    emptyBadge.innerHTML = `${logo || ''}<span>Powered by <strong>${displayName}</strong> · ${m.name}</span>`;
  }
}

window.selectModel = function (modelId) {
  const m = _availableModels.find(x => x.id === modelId);
  if (!m || m.available === false) return;
  _selectedModel = modelId;
  localStorage.setItem('sz_model', modelId);
  _renderModelPicker();
  _updateModelUI();
  document.getElementById('model-picker').style.display = 'none';
};

window.toggleModelPicker = function () {
  const picker = document.getElementById('model-picker');
  if (!picker) return;
  const isOpen = picker.style.display !== 'none';
  picker.style.display = isOpen ? 'none' : 'block';
};

// Close model picker when clicking outside
document.addEventListener('click', e => {
  const wrap = document.getElementById('model-selector-wrap');
  if (wrap && !wrap.contains(e.target)) {
    const picker = document.getElementById('model-picker');
    if (picker) picker.style.display = 'none';
  }
});

_loadModels();

// ── Session Setup ──────────────────────────────────────────────────────────────
let _setupDuration        = 1800; // seconds chosen in setup — defaults to 30 min
let _setupGoal            = '';   // optional goal text
let _setupCompleted       = false; // true once user clicked "Start Studying"
let _fabGreetingDismissed = false; // true once user dismisses the FAB speech bubble

function _renderSetupModelCards() {
  const grid = document.getElementById('setup-model-grid');
  if (!grid) return;
  grid.innerHTML = _availableModels.map(m => {
    const logo        = _MODEL_LOGOS[m.id] || '';
    const displayName = m.display_name || m.provider;
    const isSelected  = m.id === _selectedModel;
    return `
    <button class="setup-model-card${isSelected ? ' selected' : ''}" onclick="setupPickModel('${m.id}')">
      <span class="setup-model-logo">${logo}</span>
      <span class="setup-model-name">UrPadi × ${displayName}</span>
      <span class="setup-model-sub">${m.name}</span>
    </button>`;
  }).join('');
}

function _showSetupOverlay() {
  _renderSetupModelCards();
  document.getElementById('session-setup-overlay')?.classList.add('visible');
}

window.setupPickModel = function (id) {
  _selectedModel = id;
  localStorage.setItem('sz_model', id);
  _renderSetupModelCards();
  _updateModelUI();
};

window.setupPickDuration = function (secs) {
  _setupDuration = secs;
  document.querySelectorAll('.setup-duration-pill').forEach(p => {
    p.classList.toggle('active', parseInt(p.dataset.secs) === secs);
  });
  // Clear custom input when a preset pill is clicked
  const customInput = document.getElementById('setup-custom-mins');
  if (customInput) customInput.value = '';
};

window.setupPickCustomDuration = function (val) {
  const mins = parseInt(val, 10);
  if (!val || isNaN(mins) || mins < 1) return;
  _setupDuration = Math.min(mins, 480) * 60; // cap at 8 hrs
  // Deactivate preset pills since custom value overrides them
  document.querySelectorAll('.setup-duration-pill').forEach(p => p.classList.remove('active'));
};

function _formatDuration(secs) {
  if (secs >= 7200) return `${secs / 3600} hours`;
  if (secs >= 3600) return '1 hour';
  return `${secs / 60} minutes`;
}

window.startStudySession = function () {
  _setupGoal             = document.getElementById('setup-goal-input')?.value.trim() || '';
  _setupCompleted        = true;
  _fabGreetingDismissed  = false; // reset so greeting shows fresh

  document.getElementById('session-setup-overlay')?.classList.remove('visible');

  // Pre-configure timer if duration chosen
  if (_setupDuration > 0) {
    window.onTimerPresetChange(String(_setupDuration));
    // Show timer indicators immediately
    const badge = document.getElementById('chat-timer-badge');
    if (badge) badge.style.display = 'flex';
    const inlinePill = document.getElementById('inline-timer');
    if (inlinePill) inlinePill.style.display = 'flex';
    const floatPill = document.getElementById('floating-timer');
    if (floatPill) floatPill.style.display = 'flex';
  }

  const m           = _availableModels.find(x => x.id === _selectedModel);
  const displayName = m?.display_name || 'Groq';
  const goalPart    = _setupGoal
    ? `\n\nWe're focusing on: **"${_setupGoal}"**.` : '';
  const timerPart   = _setupDuration > 0
    ? ` You have **${_formatDuration(_setupDuration)}** on the clock.` : '';
  const welcome =
    `👋 Hey! I'm **UrPadi**, your study companion powered by **${displayName}**.${goalPart}${timerPart}\n\n` +
    `Upload your document or paste some text on the left and let's get to work! 📚`;

  // Store welcome message in chat history so it appears when user opens chat
  const container = document.getElementById('chat-messages');
  document.getElementById('chat-empty')?.remove();
  appendBubble('assistant', welcome);
  chatHistory.push({ role: 'assistant', content: welcome });
  container.scrollTop = container.scrollHeight;

  // Show the greeting bubble beside the chat toggle (desktop + mobile)
  const goalShort  = _setupGoal ? `<br><em style="color:var(--text-muted);font-size:0.78rem">"${_setupGoal}"</em>` : '';
  const bubbleText = `👋 Hey! I'm <strong>UrPadi</strong>, powered by <strong>${displayName}</strong>.${goalShort}<br><span style="color:var(--text-muted);font-size:0.78rem">Upload a doc, then click to chat!</span>`;
  setTimeout(() => _showGreeting(bubbleText), 400);
};


function _setDesktopFileBadge(name, extLabel) {
  const badge = document.getElementById('desktop-file-badge');
  if (!badge) return;
  if (name) {
    badge.textContent = `— ${name}${extLabel ? ' · ' + extLabel : ''}`;
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

function _docControlsHtml(changeLabel = 'Change') {
  return `
    <button class="btn btn-outline btn-sm" id="summary-btn" onclick="generateSummary()">📄 Summary</button>
    <button class="btn btn-secondary btn-sm" onclick="changeDocument()">${changeLabel}</button>
  `;
}

// ── Proactive reading engagement timers ───────────────────────────────────────
// Fires at 15 min, 45 min, and 90 min of uninterrupted reading (no chat sent).
const _READING_CHECKPOINTS = [
  { ms: 15 * 60 * 1000, msg: "Well done — you've been reading for 15 minutes. If you need any help or have a question, just ask!" },
  { ms: 45 * 60 * 1000, msg: "You've been at it for 45 minutes — great focus! Feel free to ask me anything about what you've read so far." },
  { ms: 90 * 60 * 1000, msg: "An hour and a half of reading — impressive! Consider doing a quick review or asking me a question to test your understanding." },
];
let _readingTimers       = [];  // active setTimeout IDs
let _readingStartedAt    = null; // Date when timers were last set

function _startReadingTimers() {
  _clearReadingTimers();
  _readingStartedAt = Date.now();
  _READING_CHECKPOINTS.forEach(({ ms, msg }) => {
    const id = setTimeout(() => _injectProactiveMessage(msg), ms);
    _readingTimers.push(id);
  });
}

function _clearReadingTimers() {
  _readingTimers.forEach(clearTimeout);
  _readingTimers = [];
  _readingStartedAt = null;
}

function _resetReadingTimers() {
  // Restart timers after user sends a message (activity resets the clock)
  if (documentContext) _startReadingTimers();
}

function _injectProactiveMessage(msg) {
  // Only show if the chat panel is open and there's a document loaded
  if (!documentContext) return;
  const layout = document.getElementById('bs-layout');
  if (window.innerWidth > 768 && layout?.classList.contains('chat-collapsed')) return;
  if (window.innerWidth <= 768 && !layout?.classList.contains('mobile-chat-active')) {
    // Mobile: show badge on UrPadi tab instead of injecting silently
    const badge = document.getElementById('mobile-fab-badge');
    if (badge) badge.classList.add('visible');
    appendBubble('assistant', msg);
    chatHistory.push({ role: 'assistant', content: msg });
    return;
  }
  appendBubble('assistant', msg);
  chatHistory.push({ role: 'assistant', content: msg });
}

const _user     = getUser();
const _initials = (_user?.full_name || 'U')
  .split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);

// ── Local-storage persistence (fast restore / fallback) ───────────────────────
const _BS_KEYS = [
  'pritis_bs_ctx', 'pritis_bs_tab', 'pritis_bs_paste',
  'pritis_bs_meta', 'pritis_bs_session_id',
];

// ── IndexedDB — stores raw PDF bytes so the viewer survives page navigation ────
const _IDB = {
  _db: null,
  async db() {
    if (this._db) return this._db;
    this._db = await new Promise((resolve, reject) => {
      const req = indexedDB.open('pritis_brainstorm', 1);
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
    localStorage.setItem('pritis_bs_ctx',        documentContext);
    localStorage.setItem('pritis_bs_tab',        tab);
    localStorage.setItem('pritis_bs_paste',      document.getElementById('paste-textarea')?.value || '');
    localStorage.setItem('pritis_bs_session_id', currentSessionId || '');
  } catch (e) {}
}

function _bsSaveMeta(name, extLabel, sizeLabel) {
  try { localStorage.setItem('pritis_bs_meta', JSON.stringify({ name, extLabel, sizeLabel })); } catch (e) {}
}

function _bsClear() {
  const sid = localStorage.getItem('pritis_bs_session_id');
  if (sid) _IDB.del(sid);
  _IDB.del('current_pdf');
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
  const savedSessionId = localStorage.getItem('pritis_bs_session_id');
  if (savedSessionId) {
    try {
      await _loadSessionIntoView(savedSessionId, false);
      return;
    } catch {
      localStorage.removeItem('pritis_bs_session_id');
    }
  }

  // Fallback: restore doc context from localStorage (no session)
  const ctx = localStorage.getItem('pritis_bs_ctx');
  if (!ctx) return;
  documentContext = ctx;

  const tab       = localStorage.getItem('pritis_bs_tab') || 'upload';
  const pasteText = localStorage.getItem('pritis_bs_paste') || '';
  let   meta      = null;
  try { meta = JSON.parse(localStorage.getItem('pritis_bs_meta') || 'null'); } catch {}

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
    _setDesktopFileBadge('Pasted Text', 'TEXT');
  } else {
    const isPdf  = meta?.extLabel === 'PDF';
    if (isPdf) {
      let buf = await _IDB.get('current_pdf').catch(() => null);
      if (!buf && savedSessionId) {
        buf = await _fetchSessionFile(savedSessionId).catch(() => null);
        if (buf) await _IDB.save('current_pdf', buf).catch(() => {});
      }
      if (buf) {
        if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
        currentBlobUrl = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }));
        _renderPdfInScroll(scroll, currentBlobUrl, ctx);
      } else if (savedSessionId) {
        _renderWithPdfPolling(savedSessionId, scroll, ctx);
      } else {
        scroll.style.height = '';
        scroll.innerHTML = `${pdfRestoredNotice()}<pre class="txt-render">${escHtml(ctx)}</pre>`;
      }
    } else if (savedSessionId && meta?.extLabel && !['TXT', 'TEXT', 'PASTE'].includes(meta.extLabel)) {
      // DOCX, PPTX, image — use unified PDF viewer via polling
      _renderWithPdfPolling(savedSessionId, scroll, ctx);
    } else {
      scroll.style.height = '';
      scroll.innerHTML = `<pre class="txt-render">${escHtml(ctx)}</pre>`;
    }
    if (meta) {
      document.getElementById('doc-meta-bar').innerHTML = `
        <strong>${escHtml(meta.name)}</strong>
        <span class="badge-sm">${meta.extLabel}</span>
        <span style="margin-left:auto">${meta.sizeLabel}</span>`;
      _setDesktopFileBadge(meta.name, meta.extLabel);
    }
  }

  document.getElementById('doc-upload-area').style.display = 'none';
  document.getElementById('doc-viewer-area').classList.add('visible');
  document.getElementById('doc-controls').innerHTML = _docControlsHtml('Change');

  enableChat();
  clearChat(false);
  setTimeout(_guidedStudyWelcome, 800);
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
    localStorage.setItem('pritis_bs_session_id', currentSessionId);
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

  currentFilename = 'Pasted Text';
  document.getElementById('doc-upload-area').style.display = 'none';
  document.getElementById('doc-viewer-area').classList.add('visible');
  document.getElementById('doc-meta-bar').innerHTML = `
    <strong>Pasted Text</strong>
    <span class="badge-sm">TEXT</span>
    <span style="margin-left:auto">${text.length.toLocaleString()} chars</span>`;
  _setDesktopFileBadge('Pasted Text', 'TEXT');
  document.getElementById('doc-controls').innerHTML = _docControlsHtml('Change');

  enableChat();
  clearChat(false);
  setTimeout(_guidedStudyWelcome, 800);
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

  if (file.size > 50 * 1024 * 1024) {
    errEl.textContent = 'Cannot upload file, it exceeds the 50MB limit.';
    errEl.classList.remove('hidden');
    return;
  }

  const _IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.webp', '.gif'];
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!['.pdf', '.docx', '.pptx', '.txt', ..._IMAGE_EXTS].includes(ext)) {
    errEl.textContent = 'Unsupported file. Please upload a PDF, DOCX, PPTX, TXT, or image file (PNG, JPG, JPEG, WEBP, GIF).';
    errEl.classList.remove('hidden');
    return;
  }

  setLoadingState(file.name);
  const sessionIdBefore = currentSessionId;

  let ocrWarning = null;
  try {
    if (ext === '.pdf')              ocrWarning = await handlePdf(file);
    else if (ext === '.docx')        await handleDocx(file);
    else if (ext === '.pptx')        await handlePptx(file);
    else if (ext === '.txt')         await handleTxt(file);
    else if (_IMAGE_EXTS.includes(ext)) ocrWarning = await handleImage(file);

    const extLabel = ext.slice(1).toUpperCase();
    showViewerArea(file.name, extLabel, file.size);
    enableChat(ocrWarning);
    clearChat(false);
    if (!ocrWarning) setTimeout(_guidedStudyWelcome, 800);
    if (currentSessionId === sessionIdBefore) {
      createSession(file.name, ext.slice(1), documentContext, file.size);
    }
  } catch (err) {
    resetUploadZone();
    errEl.textContent = err.message || 'Failed to load file. Please try again.';
    errEl.classList.remove('hidden');
  }
}

// ── Mobile PDF helper ────────────────────────────────────────────────────────
// Mobile browsers can't embed PDFs in iframes — use PDF.js to render pages as canvases.
function _isMobile() {
  return window.innerWidth <= 768 || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
}

let _pageCounterTimer = null;

function _startDesktopPageCounter(iframe, blobUrl) {
  // Get total pages via PDF.js metadata (no rendering)
  if (typeof pdfjsLib === 'undefined') return;
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js';
  pdfjsLib.getDocument(blobUrl).promise.then(pdf => {
    const total = pdf.numPages;
    _updatePageCounter(1, total);
    // Poll iframe URL hash for current page (Chrome native PDF updates #page=N)
    if (_pageCounterTimer) clearInterval(_pageCounterTimer);
    _pageCounterTimer = setInterval(() => {
      try {
        const hash = iframe.contentWindow.location.hash || '';
        const m = hash.match(/page=(\d+)/);
        _updatePageCounter(m ? parseInt(m[1]) : 1, total);
      } catch (_) {}
    }, 800);
  }).catch(() => {});
}

function _renderPdfInScroll(scroll, blobUrl, extractedText) {
  scroll.style.padding = '0';
  if (!_isMobile()) {
    // Desktop: native iframe PDF viewer
    scroll.style.height = '';
    scroll.innerHTML = `<iframe src="${blobUrl}#toolbar=1&navpanes=0"
      title="Document viewer"></iframe>`;
    // Attach page counter to panel-head
    const iframe = scroll.querySelector('iframe');
    if (iframe) _startDesktopPageCounter(iframe, blobUrl);
    return;
  }

  // Mobile: render via PDF.js if available
  if (typeof pdfjsLib !== 'undefined') {
    scroll.style.height = '';
    scroll.innerHTML = `
      <div style="padding:40px;text-align:center;color:var(--text-muted)">
        <div class="spinner spinner-dark" style="margin:0 auto 14px"></div>
        <p style="font-size:0.85rem">Rendering PDF pages…</p>
      </div>`;
    _renderPdfPages(scroll, blobUrl, extractedText);
  } else {
    _renderPdfTextFallback(scroll, blobUrl, extractedText);
  }
}

function _updatePageCounter(current, total) {
  const el = document.getElementById('pdf-page-counter');
  if (!el) return;
  el.style.display = '';
  el.textContent = `${current} / ${total}`;
}

async function _renderPdfPages(scroll, blobUrl, extractedText) {
  try {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js';

    const pdf        = await pdfjsLib.getDocument(blobUrl).promise;
    const total      = pdf.numPages;
    const panelW     = scroll.clientWidth || 360;
    // Cap pixel ratio at 3 — beyond that gains are invisible but memory doubles
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 3);

    scroll.innerHTML = '';
    scroll.style.background = '#888';
    scroll.style.overflowY  = 'auto';
    scroll.style.padding    = '8px 6px';

    // Measure first page to set accurate placeholder heights for all pages
    const firstPage = await pdf.getPage(1);
    const firstVp   = firstPage.getViewport({ scale: 1 });
    const baseScale = (panelW - 12) / firstVp.width;
    const placeholderH = Math.floor(firstVp.height * baseScale);

    // Build placeholder wrappers for EVERY page upfront so the scrollbar is accurate
    const wrappers = [];
    for (let i = 1; i <= total; i++) {
      const wrapper = document.createElement('div');
      wrapper.dataset.page = String(i);
      wrapper.style.cssText =
        `margin-bottom:8px;border-radius:4px;overflow:hidden;` +
        `box-shadow:0 1px 6px rgba(0,0,0,.25);background:#e8e8e8;` +
        `height:${placeholderH}px;display:flex;align-items:center;justify-content:center;`;
      wrapper.innerHTML = `<span style="font-size:0.72rem;color:#aaa">Page ${i} of ${total}</span>`;
      scroll.appendChild(wrapper);
      wrappers.push(wrapper);
    }

    // Show initial count as soon as we know total pages
    _updatePageCounter(1, total);

    // Lazy-render each page only when it scrolls into view (+ 400 px pre-load margin)
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          const w = entry.target;
          if (w.dataset.rendered) return;
          w.dataset.rendered = '1';
          observer.unobserve(w);
          _renderOnePage(pdf, parseInt(w.dataset.page), w, panelW, pixelRatio);
        });
      },
      { root: scroll, rootMargin: '400px' }
    );
    wrappers.forEach(w => observer.observe(w));

    // Track current page as user scrolls — update panel-head counter
    const pageTracker = new IntersectionObserver(
      (entries) => {
        let best = null, bestRatio = 0;
        entries.forEach(e => {
          if (e.intersectionRatio > bestRatio) { bestRatio = e.intersectionRatio; best = e.target; }
        });
        if (best) _updatePageCounter(parseInt(best.dataset.page), total);
      },
      { root: scroll, threshold: [0.1, 0.5, 1.0] }
    );
    wrappers.forEach(w => pageTracker.observe(w));

  } catch {
    // PDF.js failed — fall back to extracted text
    scroll.style.background = '';
    scroll.style.padding    = '0';
    _renderPdfTextFallback(scroll, blobUrl, extractedText);
  }
}

async function _renderOnePage(pdf, pageNum, wrapper, panelW, pixelRatio) {
  try {
    const page     = await pdf.getPage(pageNum);
    const baseVp   = page.getViewport({ scale: 1 });
    // Render at full device resolution for crisp text on retina/HD screens
    const scale    = (panelW - 12) / baseVp.width;
    const viewport = page.getViewport({ scale: scale * pixelRatio });

    const canvas = document.createElement('canvas');
    canvas.width  = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    // CSS width stays at 100% — the extra pixels make text sharp on HiDPI screens
    canvas.style.cssText = 'display:block;width:100%;height:auto;background:#fff';

    wrapper.innerHTML = '';
    wrapper.style.height = 'auto';
    wrapper.appendChild(canvas);

    await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
  } catch {
    wrapper.innerHTML =
      `<span style="font-size:0.72rem;color:#bbb;padding:8px">Page ${pageNum}</span>`;
    wrapper.style.height = '80px';
  }
}

function _renderPdfTextFallback(scroll, blobUrl, extractedText) {
  scroll.style.height = '';
  scroll.innerHTML = `
    <div style="padding:10px 14px;background:var(--bg);border-bottom:1px solid var(--border);
                display:flex;align-items:center;gap:10px;flex-shrink:0;">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        style="width:15px;height:15px;color:var(--primary);flex-shrink:0">
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
      <span style="font-size:0.78rem;color:var(--text-muted);flex:1">PDF text extracted for UrPadi</span>
      <a href="${blobUrl}" target="_blank" rel="noopener"
         style="padding:5px 12px;background:var(--primary);color:#fff;border-radius:6px;
                font-size:0.78rem;font-weight:600;text-decoration:none;white-space:nowrap;flex-shrink:0;">
        Open PDF ↗
      </a>
    </div>
    <pre class="txt-render">${escHtml(extractedText)}</pre>`;
}

// ── Unified PDF viewer with polling (Pipeline B) ──────────────────────────────
// Polls /pdf-status until the backend finishes converting, then renders the PDF.
async function _renderWithPdfPolling(sessionId, scroll, extractedText = '') {
  scroll.style.padding = '0';
  scroll.style.height  = '';
  scroll.innerHTML = `
    <div style="padding:40px;text-align:center;color:var(--text-muted)">
      <div class="spinner spinner-dark" style="margin:0 auto 14px"></div>
      <p style="font-size:0.85rem">Preparing document viewer…</p>
      <p style="font-size:0.75rem;margin-top:6px;opacity:.6">
        You can already start chatting below while the viewer loads.</p>
    </div>`;

  for (let attempt = 0; attempt < 30; attempt++) { // max ~60 s
    try {
      const status = await api.get(`/brainstorm/sessions/${sessionId}/pdf-status`);
      if (status.ready) {
        const pdfBuf = await api.getBuffer(`/brainstorm/sessions/${sessionId}/document.pdf`);
        if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
        currentBlobUrl = URL.createObjectURL(new Blob([pdfBuf], { type: 'application/pdf' }));
        _renderPdfInScroll(scroll, currentBlobUrl, extractedText);
        return;
      }
    } catch { /* ignore transient errors — keep polling */ }
    await new Promise(r => setTimeout(r, 2000));
  }

  // Timeout fallback — show extracted text with a note
  scroll.style.height = '';
  scroll.innerHTML = `
    <div style="background:#fff3cd;color:#856404;border-bottom:1px solid #ffc107;
                padding:10px 16px;font-size:.82rem;line-height:1.5;flex-shrink:0;">
      ⚠️ Document viewer could not be prepared. You can still chat with UrPadi about the content.
    </div>
    <pre class="txt-render">${escHtml(extractedText)}</pre>`;
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

  if (_isMobile()) {
    // Mobile: show loading spinner while extracting text (iframe doesn't work on mobile)
    scroll.style.height = '';
    scroll.innerHTML = `
      <div style="padding:40px;text-align:center;color:var(--text-muted)">
        <div class="spinner spinner-dark" style="margin:0 auto 14px"></div>
        <p style="font-size:0.85rem">Extracting text from PDF…</p>
      </div>`;
  } else {
    scroll.style.height = '100%';
    scroll.innerHTML = `<iframe src="${currentBlobUrl}#toolbar=1&navpanes=0"
      title="Document viewer" style="width:100%;height:100%;border:none;display:block;"></iframe>`;
  }

  // Use XHR so we can track real upload progress (0→40%), then simulate
  // server-side processing (40→85%) while waiting for the response.
  const formData = new FormData();
  formData.append('file', file);

  _bsProgress(4, 'Preparing upload…');
  let simTimer = null;

  const res = await api.postFormProgress(
    '/brainstorm/sessions/from-file',
    formData,
    {
      onProgress: (ratio) => {
        if (simTimer) return;
        const pct = Math.round(ratio * 40);
        _bsProgress(pct, `Uploading… ${pct}%`);
      },
      onUploadComplete: () => {
        simTimer = _simProgress(40, 85, 14000, (p) => {
          const label = p < 55 ? `Extracting text… ${p}%`
                      : p < 72 ? `Running OCR… ${p}%`
                      : `Processing… ${p}%`;
          _bsProgress(p, label);
        });
      },
    }
  );

  if (simTimer) clearInterval(simTimer);
  _bsProgress(100, 'Document ready! ✓');

  documentContext = res.text || '';

  // On mobile, replace loading state with extracted text view
  if (_isMobile()) {
    _renderPdfInScroll(scroll, currentBlobUrl, documentContext);
  }

  // Session is now created — store its ID and cache bytes under the session key too
  currentSessionId = res.id;
  localStorage.setItem('pritis_bs_session_id', currentSessionId);
  await _IDB.save(currentSessionId, arrayBuffer).catch(() => {});

  // Return OCR warning if text extraction failed — caller shows warning banner
  if (res.ocr_failed) {
    return 'Could not extract text from this scanned PDF. The document is shown for viewing only — AI analysis is unavailable.';
  }
  return null;
}

// ── DOCX ──────────────────────────────────────────────────────────────────────
async function handleDocx(file) {
  const formData = new FormData();
  formData.append('file', file);

  _bsProgress(4, 'Preparing upload…');
  let simTimer = null;

  const res = await api.postFormProgress(
    '/brainstorm/sessions/from-file',
    formData,
    {
      onProgress: (ratio) => {
        if (simTimer) return;
        const pct = Math.round(ratio * 40);
        _bsProgress(pct, `Uploading… ${pct}%`);
      },
      onUploadComplete: () => {
        simTimer = _simProgress(40, 85, 6000, (p) => _bsProgress(p, `Extracting text… ${p}%`));
      },
    }
  );

  if (simTimer) clearInterval(simTimer);
  _bsProgress(100, 'Document ready! ✓');

  documentContext = res.text || '';
  currentSessionId = res.id;
  localStorage.setItem('pritis_bs_session_id', currentSessionId);

  // Kick off viewer polling in background — do not await so chat is usable immediately
  const scroll = document.getElementById('doc-viewer-scroll');
  _renderWithPdfPolling(currentSessionId, scroll, documentContext);
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

// ── PPTX ──────────────────────────────────────────────────────────────────────
async function handlePptx(file) {
  const formData = new FormData();
  formData.append('file', file);

  _bsProgress(4, 'Preparing upload…');
  let simTimer = null;

  const res = await api.postFormProgress(
    '/brainstorm/sessions/from-file',
    formData,
    {
      onProgress: (ratio) => {
        if (simTimer) return;
        const pct = Math.round(ratio * 40);
        _bsProgress(pct, `Uploading… ${pct}%`);
      },
      onUploadComplete: () => {
        simTimer = _simProgress(40, 85, 8000, (p) => _bsProgress(p, `Extracting slides… ${p}%`));
      },
    }
  );

  if (simTimer) clearInterval(simTimer);
  _bsProgress(100, 'Document ready! ✓');

  documentContext = res.text || '';
  currentSessionId = res.id;
  localStorage.setItem('pritis_bs_session_id', currentSessionId);

  const scroll = document.getElementById('doc-viewer-scroll');
  _renderWithPdfPolling(currentSessionId, scroll, documentContext);
}

// ── Image ─────────────────────────────────────────────────────────────────────
async function handleImage(file) {
  const formData = new FormData();
  formData.append('file', file);

  _bsProgress(4, 'Preparing upload…');
  let simTimer = null;

  const res = await api.postFormProgress(
    '/brainstorm/sessions/from-file',
    formData,
    {
      onProgress: (ratio) => {
        if (simTimer) return;
        const pct = Math.round(ratio * 40);
        _bsProgress(pct, `Uploading… ${pct}%`);
      },
      onUploadComplete: () => {
        simTimer = _simProgress(40, 85, 8000, (p) => _bsProgress(p, `Running OCR… ${p}%`));
      },
    }
  );

  if (simTimer) clearInterval(simTimer);
  _bsProgress(100, 'Image ready! ✓');

  documentContext = res.text || '';
  currentSessionId = res.id;
  localStorage.setItem('pritis_bs_session_id', currentSessionId);

  // Show OCR warning if no text was extracted
  if (res.ocr_failed) {
    return 'No text could be extracted from this image. The image is shown for viewing only — AI analysis is unavailable.';
  }

  const scroll = document.getElementById('doc-viewer-scroll');
  _renderWithPdfPolling(currentSessionId, scroll, documentContext);
  return null;
}

// ── Progress helpers ──────────────────────────────────────────────────────────
function _bsProgress(pct, label) {
  const fill = document.getElementById('bs-progress-fill');
  const lbl  = document.getElementById('bs-progress-label');
  if (fill) fill.style.width = Math.max(4, Math.min(100, Math.round(pct))) + '%';
  if (lbl && label) lbl.textContent = label;
}

// Smooth simulated progress from `from` to `to` over `durationMs`.
// Returns a timer ID — call clearInterval(id) to stop it early.
function _simProgress(from, to, durationMs, cb) {
  const interval = 80;
  const steps    = Math.ceil(durationMs / interval);
  let   step     = 0;
  const id = setInterval(() => {
    step = Math.min(step + 1, steps);
    const eased = 1 - Math.pow(1 - step / steps, 2.5); // ease-out cubic
    cb(Math.round(from + (to - from) * eased));
    if (step >= steps) clearInterval(id);
  }, interval);
  return id;
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function setLoadingState(filename) {
  document.getElementById('upload-zone').innerHTML = `
    <div class="upload-icon">📄</div>
    <p style="font-weight:600;font-size:0.9rem;margin-bottom:4px">${escHtml(filename)}</p>
    <div class="progress-track" style="width:85%;margin:8px auto 4px">
      <div id="bs-progress-fill" class="progress-fill" style="width:4%"></div>
    </div>
    <p id="bs-progress-label" class="progress-pct" style="text-align:center">Preparing…</p>`;
}

function resetUploadZone() {
  document.getElementById('upload-zone').innerHTML = `
    <div class="upload-icon">📄</div>
    <h3>Upload your material</h3>
    <p>Drop a file here or click to browse</p>
    <p class="text-xs" style="margin-bottom:16px">PDF · DOCX · PPTX · TXT · PNG · JPG · WEBP</p>
    <button class="btn btn-primary btn-sm"
      onclick="event.stopPropagation();document.getElementById('file-input').click()">
      Choose File
    </button>`;
}

function showViewerArea(filename, extLabel, sizeBytes) {
  currentFilename = filename;
  document.getElementById('doc-upload-area').style.display = 'none';
  document.getElementById('doc-viewer-area').classList.add('visible');

  const kb = (sizeBytes / 1024).toFixed(1);
  document.getElementById('doc-meta-bar').innerHTML = `
    <strong>${escHtml(filename)}</strong>
    <span class="badge-sm">${extLabel}</span>
    <span style="margin-left:auto">${kb} KB</span>`;
  _setDesktopFileBadge(filename, extLabel);
  document.getElementById('doc-controls').innerHTML = _docControlsHtml('Change File');

  _bsSaveMeta(filename, extLabel, kb + ' KB');
  _bsSave();
}

// ── Generate Summary ──────────────────────────────────────────────────────────
window.generateSummary = async function () {
  if (!documentContext) return;

  const btn = document.getElementById('summary-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner spinner-dark" style="width:12px;height:12px"></span> Generating…';
  }

  try {
    const res = await api.post('/brainstorm/summary', {
      context: documentContext,
      filename: currentFilename,
    });

    // Build PDF using jsPDF
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ unit: 'mm', format: 'a4' });
    const pageW   = doc.internal.pageSize.getWidth();
    const pageH   = doc.internal.pageSize.getHeight();
    const margin  = 18;
    const maxW    = pageW - margin * 2;
    let y = margin + 8;

    // ── Header bar ──
    doc.setFillColor(0, 119, 255);
    doc.rect(0, 0, pageW, 22, 'F');

    // Logo: white rounded square with "P"
    doc.setFillColor(255, 255, 255);
    doc.roundedRect(margin, 3, 10, 10, 2, 2, 'F');
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(11);
    doc.setTextColor(0, 119, 255);
    doc.text('P', margin + 3.5, 10.5);

    // App name + tagline
    doc.setTextColor(255, 255, 255);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(11);
    doc.text('Pritis', margin + 13, 9);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(7.5);
    doc.setTextColor(200, 225, 255);
    doc.text('AI-Powered Quiz & Study Platform', margin + 13, 14);

    // Date + label top-right
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    doc.setTextColor(255, 255, 255);
    doc.text(new Date().toLocaleDateString('en-GB', { day:'numeric', month:'long', year:'numeric' }), pageW - margin, 9.5, { align: 'right' });
    doc.setFontSize(7);
    doc.setTextColor(200, 225, 255);
    doc.text('Document Summary', pageW - margin, 14.5, { align: 'right' });

    y = 30; // start content below taller header

    // ── Document title ──
    doc.setTextColor(10, 22, 40);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(15);
    const titleLines = doc.splitTextToSize(currentFilename.replace(/\.[^.]+$/, ''), maxW);
    doc.text(titleLines, margin, y);
    y += titleLines.length * 7 + 2;

    // Divider
    doc.setDrawColor(220, 228, 240);
    doc.setLineWidth(0.4);
    doc.line(margin, y, pageW - margin, y);
    y += 6;

    // ── Render summary sections ──
    const lines = res.summary.split('\n');
    const SECTION_HEADERS = [
      'DOCUMENT TITLE', 'OVERVIEW', 'KEY CONCEPTS', 'IMPORTANT POINTS',
      'DEFINITIONS AND KEY TERMS', 'EXAMPLES OR APPLICATIONS',
      'STEP-BY-STEP PROCESSES', 'EXAM AND REVISION HIGHLIGHTS', 'KEY TAKEAWAYS',
      // legacy headers (backwards compat)
      'KEY POINTS', 'IMPORTANT DETAILS', 'CONCLUSION',
    ];

    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line) { y += 3; continue; }

      const isHeader = SECTION_HEADERS.some(h => line.toUpperCase().startsWith(h));

      if (isHeader) {
        if (y > pageH - 30) { doc.addPage(); y = margin + 6; }
        y += 3;
        doc.setFillColor(240, 245, 255);
        doc.setDrawColor(0, 119, 255);
        doc.roundedRect(margin - 2, y - 5, maxW + 4, 9, 2, 2, 'FD');
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(10);
        doc.setTextColor(0, 80, 200);
        doc.text(line, margin + 1, y);
        y += 8;
        doc.setTextColor(10, 22, 40);
      } else {
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(10);
        doc.setTextColor(40, 50, 65);
        const wrapped = doc.splitTextToSize(line, maxW - 2);
        for (const wl of wrapped) {
          if (y > pageH - 16) { doc.addPage(); y = margin + 6; }
          doc.text(wl, margin + 2, y);
          y += 5.5;
        }
      }
    }

    // ── Footer on each page ──
    const pageCount = doc.internal.getNumberOfPages();
    for (let i = 1; i <= pageCount; i++) {
      doc.setPage(i);
      // Footer divider
      doc.setDrawColor(220, 228, 240);
      doc.setLineWidth(0.3);
      doc.line(margin, pageH - 13, pageW - margin, pageH - 13);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(7.5);
      doc.setTextColor(160, 170, 185);
      doc.text('Pritis — AI-Powered Quiz & Study Platform  |  pritis.name.ng', margin, pageH - 8);
      doc.text(`Page ${i} of ${pageCount}`, pageW - margin, pageH - 8, { align: 'right' });
    }

    // Download
    const safeName = currentFilename.replace(/[^a-z0-9_\-. ]/gi, '_').replace(/\.[^.]+$/, '');
    doc.save(`Summary - ${safeName}.pdf`);

    if (btn) { btn.disabled = false; btn.innerHTML = '✓ Downloaded!'; }
    setTimeout(() => { if (btn) btn.innerHTML = '📄 Summary'; btn.disabled = false; }, 3000);

  } catch (err) {
    if (btn) { btn.disabled = false; btn.innerHTML = '📄 Summary'; }
    alert('Could not generate summary: ' + (err.message || 'Unknown error'));
  }
};

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
  const banner = document.getElementById('ocr-warning-banner');
  if (banner) banner.remove();
  resetUploadZone();
  switchInputTab('upload');

  documentContext  = '';
  currentSessionId = null;
  disableChat();
  clearChat(false);

  // Reset setup state and show the setup overlay for the new session
  _setupCompleted = false;
  _setupDuration  = 1800;
  _setupGoal      = '';
  _fabGreetingDismissed = false;
  document.querySelectorAll('.setup-duration-pill').forEach(p => {
    p.classList.toggle('active', parseInt(p.dataset.secs) === 1800);
  });
  const goalInput = document.getElementById('setup-goal-input');
  if (goalInput) goalInput.value = '';
  const customInput = document.getElementById('setup-custom-mins');
  if (customInput) customInput.value = '';
  timerStop();
  setTimeout(_showSetupOverlay, 80);
};

// ── Chat enable / disable ─────────────────────────────────────────────────────
function enableChat(ocrWarning) {
  const input = document.getElementById('chat-input');

  // Remove any previous OCR warning banner
  const prev = document.getElementById('ocr-warning-banner');
  if (prev) prev.remove();

  if (ocrWarning) {
    // Show warning above the chat — keep chat disabled since there's no text context
    input.disabled    = true;
    input.placeholder = 'Text extraction failed — AI chat unavailable for this document.';
    document.getElementById('send-btn').disabled = true;

    const banner = document.createElement('div');
    banner.id = 'ocr-warning-banner';
    banner.style.cssText =
      'background:#fff3cd;color:#856404;border:1px solid #ffc107;border-radius:8px;' +
      'padding:10px 14px;font-size:0.82rem;line-height:1.5;margin-bottom:10px;';
    banner.innerHTML =
      '<strong>⚠️ Text extraction failed</strong><br>' +
      'This appears to be a scanned image PDF. The document is displayed above for viewing. ' +
      'AI analysis requires extractable text — try uploading a typed/digital PDF or DOCX instead.';

    const chatMessages = document.getElementById('chat-messages');
    chatMessages.parentNode.insertBefore(banner, chatMessages);
  } else {
    input.disabled    = false;
    input.placeholder = 'Ask UrPadi anything about the document… (Enter to send)';
    document.getElementById('send-btn').disabled = false;
    document.getElementById('mic-btn') && (document.getElementById('mic-btn').disabled = false);
    // Don't auto-focus on mobile — it scrolls the off-screen chat panel into view
    if (!window.matchMedia('(max-width: 768px)').matches) input.focus();
    _startReadingTimers(); // begin proactive engagement countdown
    _showTimerBar(true);
  }
}

function disableChat() {
  const input = document.getElementById('chat-input');
  input.disabled    = true;
  input.placeholder = 'Upload a document to start chatting with UrPadi…';
  document.getElementById('send-btn').disabled = true;
  document.getElementById('mic-btn') && (document.getElementById('mic-btn').disabled = true);
  _clearReadingTimers(); // stop timers when no document is active
  _showTimerBar(false);
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
        <h3>${documentContext ? 'UrPadi is ready!' : 'Meet UrPadi'}</h3>
        <p>${msg}</p>
      </div>`;
  } else {
    container.innerHTML = '';
  }
};

// ══════════════════════════════════════════════════════════════════════════════
// SMART LEARNING ENHANCEMENTS
// Flashcards · Visual Explanation · YouTube Resources · Confusion Detection
// ══════════════════════════════════════════════════════════════════════════════

// ── Confusion detection ────────────────────────────────────────────────────────
const _CONFUSION_RE = /don'?t understand|i'?m confused|not (getting|clear)|explain (again|more|further|this)|what do you mean|help me understand|i'?m lost|make it (simpler|clearer)|what (is|are) (a |an |the )?|clarif/i;

function _isConfused(text) {
  return _CONFUSION_RE.test(text);
}

// ── Inject action buttons below an assistant message ──────────────────────────
// topic: hint string derived from recent chat, used for resources/flashcards
let _msgActionCounter = 0;

function _appendMessageActions(afterEl, topicHint) {
  if (!documentContext) return;
  const id  = `ma-${++_msgActionCounter}`;
  const row = document.createElement('div');
  row.className = 'msg-action-row';
  row.id = id;
  row.innerHTML = `
    <button class="msg-action-btn" id="${id}-fc"
      onclick="triggerFlashcards('${id}', '${escHtml(topicHint).replace(/'/g,"\\'")}')">
      📇 Flashcards
    </button>
    <button class="msg-action-btn" id="${id}-vis"
      onclick="triggerVisual('${id}', '${escHtml(topicHint).replace(/'/g,"\\'")}')">
      🎨 Visual Explanation
    </button>
    <button class="msg-action-btn" id="${id}-res"
      onclick="triggerResources('${id}', '${escHtml(topicHint).replace(/'/g,"\\'")}')">
      🎥 Resources
    </button>`;
  afterEl.after(row);
  return row;
}

// Extract a short topic hint from the last user message + AI reply
function _extractTopic(userMsg, aiReply) {
  // Use the first sentence of the AI reply as topic hint (max 80 chars)
  const first = (aiReply || userMsg || '').split(/[.\n]/)[0].trim();
  return first.slice(0, 80);
}

// ── Flashcards ────────────────────────────────────────────────────────────────
window.triggerFlashcards = async function (rowId, topic) {
  const row = document.getElementById(rowId);
  const btn = document.getElementById(`${rowId}-fc`);
  if (!btn || btn.disabled) return;

  // Disable all three buttons while loading
  ['fc','vis','res'].forEach(k => {
    const b = document.getElementById(`${rowId}-${k}`);
    if (b) b.disabled = true;
  });
  btn.textContent = '⏳ Generating…';

  let cards;
  try {
    const res = await api.post('/brainstorm/flashcards', {
      context: documentContext,
      topic,
      model: _selectedModel,
      count: 7,
    });
    cards = res.cards;
  } catch (err) {
    btn.disabled = false;
    btn.textContent = '📇 Flashcards';
    ['vis','res'].forEach(k => {
      const b = document.getElementById(`${rowId}-${k}`);
      if (b) b.disabled = false;
    });
    _injectAfterRow(row, `<p style="color:var(--danger);font-size:.84rem">⚠️ ${escHtml(err.message)}</p>`);
    return;
  }

  // Build flip-card deck HTML
  const deckId = `fc-${rowId}`;
  const deck = document.createElement('div');
  deck.className = 'flashcard-deck';
  deck.id = deckId;
  deck.innerHTML = `
    <div class="flashcard-header">📇 Flashcards — ${cards.length} cards (tap to flip)</div>
    <div class="flashcard-viewport">
      <div class="flashcard-scene">
        <div class="flashcard" id="${deckId}-card" onclick="flipFlashcard('${deckId}')">
          <div class="flashcard-face flashcard-front">
            <div class="flashcard-label">Question</div>
            <div class="flashcard-text" id="${deckId}-q">${escHtml(cards[0].q)}</div>
          </div>
          <div class="flashcard-face flashcard-back">
            <div class="flashcard-label">Answer</div>
            <div class="flashcard-text" id="${deckId}-a">${escHtml(cards[0].a)}</div>
          </div>
        </div>
      </div>
      <p class="flashcard-hint">Tap card to reveal answer</p>
      <div class="flashcard-nav">
        <button class="flashcard-nav-btn" onclick="navFlashcard('${deckId}',-1)" id="${deckId}-prev" disabled>‹</button>
        <span class="flashcard-counter" id="${deckId}-counter">1 / ${cards.length}</span>
        <button class="flashcard-nav-btn" onclick="navFlashcard('${deckId}',1)" id="${deckId}-next">›</button>
      </div>
    </div>`;

  // Store cards on the element for nav
  deck._cards   = cards;
  deck._current = 0;

  row.after(deck);

  // Re-enable non-clicked buttons
  ['vis','res'].forEach(k => {
    const b = document.getElementById(`${rowId}-${k}`);
    if (b) b.disabled = false;
  });
  btn.textContent = '📇 Flashcards ✓';
};

window.flipFlashcard = function (deckId) {
  document.getElementById(`${deckId}-card`)?.classList.toggle('flipped');
};

window.navFlashcard = function (deckId, dir) {
  const deck = document.getElementById(deckId);
  if (!deck) return;
  const cards = deck._cards;
  let idx = deck._current + dir;
  if (idx < 0 || idx >= cards.length) return;
  deck._current = idx;

  // Reset flip, update content
  const card  = document.getElementById(`${deckId}-card`);
  if (card) card.classList.remove('flipped');
  const qEl = document.getElementById(`${deckId}-q`);
  const aEl = document.getElementById(`${deckId}-a`);
  if (qEl) qEl.textContent = cards[idx].q;
  if (aEl) aEl.textContent = cards[idx].a;

  document.getElementById(`${deckId}-counter`).textContent = `${idx + 1} / ${cards.length}`;
  document.getElementById(`${deckId}-prev`).disabled = idx === 0;
  document.getElementById(`${deckId}-next`).disabled = idx === cards.length - 1;
};

// ── Visual Explanation ────────────────────────────────────────────────────────
let _visualCounter = 0;

window.triggerVisual = function (rowId, topic) {
  const row = document.getElementById(rowId);
  if (!row) return;

  // Toggle: if prompt already open, close it
  const existing = document.getElementById(`${rowId}-vis-prompt`);
  if (existing) { existing.remove(); return; }

  // Remove any previous visual card for this row
  const prevCard = document.getElementById(`${rowId}-vis-card`);
  if (prevCard) prevCard.remove();

  const promptEl = document.createElement('div');
  promptEl.id = `${rowId}-vis-prompt`;
  promptEl.className = 'visual-prompt-row';
  promptEl.innerHTML = `
    <div class="visual-prompt-box">
      <div class="visual-prompt-title">🎨 Visual Explanation</div>
      <p class="visual-prompt-hint">What would you like explained visually?<br>
        <span style="opacity:.7">Tip: mention a page number or concept — e.g. "Contract formation flowchart" or "Explain page 2"</span>
      </p>
      <div class="visual-prompt-input-row" id="${rowId}-vis-inputs">
        <input type="text" id="${rowId}-vis-input" class="visual-prompt-input"
          placeholder="e.g. Explain contract law on page 3 with a diagram"
          value="${escHtml(topic || '')}" />
        <button class="visual-prompt-btn"
          onclick="generateVisualDiagram('${rowId}', document.getElementById('${rowId}-vis-input').value)">
          Generate
        </button>
      </div>
    </div>`;

  row.after(promptEl);

  const inp = document.getElementById(`${rowId}-vis-input`);
  inp.focus();
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') generateVisualDiagram(rowId, inp.value);
  });
};

window.generateVisualDiagram = async function (rowId, userQuery) {
  userQuery = (userQuery || '').trim();
  const inputsEl = document.getElementById(`${rowId}-vis-inputs`);
  if (inputsEl) {
    inputsEl.innerHTML = '<p style="color:var(--text-muted);font-size:.82rem;padding:4px 0">⏳ Generating diagram…</p>';
  }

  let data;
  try {
    data = await api.post('/brainstorm/visual', {
      session_id: currentSessionId || null,
      user_query: userQuery,
      model:      _selectedModel,
    });
  } catch (err) {
    if (inputsEl) {
      const safeQ = escHtml(userQuery).replace(/'/g, "\\'");
      inputsEl.innerHTML = `
        <p style="color:var(--danger);font-size:.82rem;margin:0 0 8px">⚠️ ${escHtml(err.message)}</p>
        <button class="visual-prompt-btn" onclick="generateVisualDiagram('${rowId}', '${safeQ}')">Retry</button>`;
    }
    return;
  }

  // Remove prompt
  const promptEl = document.getElementById(`${rowId}-vis-prompt`);
  if (promptEl) promptEl.remove();

  const cardId  = `${rowId}-vis-card`;
  const diagId  = `vis-diag-${++_visualCounter}`;
  const safeQ   = escHtml(userQuery).replace(/'/g, "\\'");

  let bodyHtml = '';

  const card = document.createElement('div');
  card.className = 'visual-card';
  card.id = cardId;

  if (data.mermaid) {
    card.innerHTML = `
      <div class="visual-card-inner">
        <div class="visual-card-title">🎨 ${escHtml(data.title || userQuery || 'Visual Breakdown')}</div>
        <div class="visual-diagram-wrap" id="${diagId}-wrap">
          <div class="mermaid" id="${diagId}"></div>
        </div>
        ${data.description ? `<p class="visual-desc">${escHtml(data.description)}</p>` : ''}
        <div class="visual-actions">
          <button class="visual-action-btn" onclick="downloadVisualSVG('${diagId}-wrap')">⬇ Download SVG</button>
          <button class="visual-action-btn" onclick="triggerVisual('${rowId}', '${safeQ}')">🔄 Regenerate</button>
        </div>
      </div>`;
    // Set raw mermaid syntax via textContent to avoid HTML-escaping issues
    card.querySelector(`#${diagId}`).textContent = data.mermaid;
  } else if (data.sections) {
    const sHtml = (data.sections || []).map(s => `
      <div class="visual-section">
        <div class="visual-section-heading">
          <span class="v-icon">${s.icon || '📌'}</span>
          <span>${escHtml(s.heading || '')}</span>
        </div>
        <ul>${(s.points || []).map(p => `<li>${escHtml(p)}</li>`).join('')}</ul>
      </div>`).join('');
    card.innerHTML = `
      <div class="visual-card-inner">
        <div class="visual-card-title">🎨 ${escHtml(data.title || userQuery || 'Visual Breakdown')}</div>
        ${sHtml}
        <div class="visual-actions">
          <button class="visual-action-btn" onclick="triggerVisual('${rowId}', '${safeQ}')">🔄 Regenerate</button>
        </div>
      </div>`;
  }

  const row = document.getElementById(rowId);
  if (row) row.after(card);

  // Render Mermaid diagram
  if (data.mermaid && window.mermaid) {
    try {
      await mermaid.run({ nodes: [document.getElementById(diagId)] });
    } catch (e) {
      const diagEl = document.getElementById(diagId);
      if (diagEl) {
        const desc = data.description ? `<p style="font-size:.82rem;color:var(--text);margin:0 0 10px">${escHtml(data.description)}</p>` : '';
        diagEl.innerHTML = `
          <div style="padding:14px;text-align:left">
            ${desc}
            <p style="color:var(--text-muted);font-size:.78rem;margin:0 0 10px">⚠️ Diagram could not render. Click Regenerate to try again.</p>
            <button class="visual-action-btn" onclick="triggerVisual('${rowId}', '${safeQ}')">🔄 Regenerate</button>
          </div>`;
      }
    }
  }

  const btn = document.getElementById(`${rowId}-vis`);
  if (btn) btn.textContent = '🎨 Visual ✓';
};

window.downloadVisualSVG = function (containerId) {
  const el  = document.getElementById(containerId);
  const svg = el?.querySelector('svg');
  if (!svg) return;
  const blob = new Blob([svg.outerHTML], { type: 'image/svg+xml' });
  const url  = URL.createObjectURL(blob);
  const a    = Object.assign(document.createElement('a'), { href: url, download: 'visual-explanation.svg' });
  a.click();
  URL.revokeObjectURL(url);
};

// Strip a filename extension and return a clean title
function _cleanDocTopic(filename) {
  return (filename || '').replace(/\.[^.]+$/, '').replace(/[-_]/g, ' ').trim();
}

// Detect whether a string looks like a greeting/question rather than an academic topic
const _GREETING_RE = /^(how|what|why|when|where|can|could|would|hey|hi|i'?m|i am|let'?s|let me|great|sure|ok|okay|here|upload|tap|click|ask|feel free|welcome|don'?t|you can|you need|you have|📄|👋|i've|i have)/i;

// Decide the best topic hint: always prefer document filename over AI reply text
function _bestTopicHint(rawTopic) {
  const docTopic = _cleanDocTopic(currentFilename);
  // Document filename is always more reliable than extracted AI text
  if (docTopic) return docTopic;
  // If we have no filename, fall back to raw topic only if it looks academic
  if (!rawTopic || _GREETING_RE.test(rawTopic.trim())) return '';
  return rawTopic;
}

// ── Learning Resources (YouTube) ─────────────────────────────────────────────
window.triggerResources = async function (rowId, topic) {
  const row = document.getElementById(rowId);
  const btn = document.getElementById(`${rowId}-res`);
  if (!btn || btn.disabled) return;

  ['fc','vis','res'].forEach(k => {
    const b = document.getElementById(`${rowId}-${k}`);
    if (b) b.disabled = true;
  });
  btn.textContent = '⏳ Fetching…';

  let data;
  try {
    // Send ONLY the session_id — let the backend scan the document via RAG
    // to determine the subject. Never send filenames or AI reply text as topic.
    data = await api.post('/brainstorm/resources/smart', {
      session_id: currentSessionId || null,
      model: _selectedModel,
    });
  } catch (err) {
    btn.disabled = false;
    btn.textContent = '🎥 Resources';
    ['fc','vis'].forEach(k => {
      const b = document.getElementById(`${rowId}-${k}`);
      if (b) b.disabled = false;
    });
    _injectAfterRow(row, `<p style="color:var(--danger);font-size:.84rem">⚠️ ${escHtml(err.message)}</p>`);
    return;
  }

  const rack = document.createElement('div');
  rack.className = 'resources-rack';
  rack.id = `res-rack-${rowId}`;

  if (!data.configured) {
    // No YouTube API key
    rack.innerHTML = `
      <div class="resources-rack-title">🎥 Resources</div>
      <p style="font-size:0.75rem;color:var(--text-muted);margin:4px 0 8px">
        YouTube API key not configured — add <code>YOUTUBE_API_KEY</code> to your <code>.env</code> file.
      </p>`;
  } else if (data.error || !data.videos?.length) {
    // AI couldn't determine topic OR no results — ask the user
    const inputId = `res-manual-${rowId}`;
    rack.innerHTML = `
      <div class="resources-rack-title">🎥 Resources</div>
      <p style="font-size:0.82rem;color:var(--text-muted);margin:4px 0 10px;line-height:1.5">
        What topic would you like videos on?
      </p>
      <div style="display:flex;gap:8px;align-items:center;max-width:340px">
        <input type="text" id="${inputId}"
          placeholder="e.g. Business Law contracts"
          style="flex:1;height:34px;border:1.5px solid var(--border);border-radius:8px;
                 padding:0 10px;font-size:0.84rem;background:var(--surface);color:var(--text);outline:none"
          onkeydown="if(event.key==='Enter') searchResourcesManually('${rowId}')" />
        <button onclick="searchResourcesManually('${rowId}')"
          style="height:34px;padding:0 14px;border-radius:8px;border:none;
                 background:var(--primary);color:#fff;font-weight:700;font-size:0.82rem;
                 cursor:pointer;white-space:nowrap">
          Search
        </button>
      </div>`;
  } else {
    // Show AI intro message in the chat first
    if (data.intro) {
      const introEl = document.createElement('div');
      introEl.className = 'msg assistant';
      introEl.innerHTML = `
        <div class="msg-avatar">UP</div>
        <div class="msg-bubble">${renderMarkdown(data.intro)}</div>`;
      document.getElementById('chat-messages')?.appendChild(introEl);
      const msgs = document.getElementById('chat-messages');
      if (msgs) msgs.scrollTop = msgs.scrollHeight;
    }

    // Build embedded video cards
    const cardsHtml = data.videos.map((v, idx) => {
      const embedId = `yt-embed-${rowId}-${idx}`;
      return `
        <div class="resource-card resource-card-embed" id="${embedId}-card">
          <div class="resource-embed-preview" id="${embedId}-preview"
               onclick="playYouTubeEmbed('${embedId}','${escHtml(v.embedUrl)}','${escHtml(v.id)}')">
            <img class="resource-thumb" src="${escHtml(v.thumbnail)}" alt="${escHtml(v.title)}"
                 loading="lazy" onerror="this.style.display='none'">
            <div class="resource-play-btn">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="#fff">
                <polygon points="5 3 19 12 5 21 5 3"/>
              </svg>
            </div>
          </div>
          <div id="${embedId}-player" class="resource-embed-player" style="display:none">
            <!-- iframe injected on play -->
          </div>
          <div class="resource-info">
            <div class="resource-title">${escHtml(v.title)}</div>
            <div class="resource-channel">${escHtml(v.channel)}</div>
          </div>
        </div>`;
    }).join('');
    rack.innerHTML = `<div class="resources-rack-title">🎥 Recommended Videos — ${escHtml(data.topic || topic || '')}</div>${cardsHtml}`;
  }

  row.after(rack);

  ['fc','vis'].forEach(k => {
    const b = document.getElementById(`${rowId}-${k}`);
    if (b) b.disabled = false;
  });
  btn.textContent = '🎥 Resources ✓';
};

// Manual topic search — called when user types their own topic in the fallback form
window.searchResourcesManually = async function (rowId) {
  const inputEl = document.getElementById(`res-manual-${rowId}`);
  const rack    = document.getElementById(`res-rack-${rowId}`);
  if (!inputEl || !rack) return;

  const query = inputEl.value.trim();
  if (!query) { inputEl.focus(); return; }

  // Show loading state inside the rack
  rack.innerHTML = `
    <div class="resources-rack-title">🎥 Resources</div>
    <div style="display:flex;align-items:center;gap:8px;padding:6px 0;font-size:0.82rem;color:var(--text-muted)">
      <div class="typing-indicator"><span></span><span></span><span></span></div>
      Searching for "${escHtml(query)}"…
    </div>`;

  let data;
  try {
    data = await api.post('/brainstorm/resources/smart', {
      session_id: currentSessionId || null,
      user_query: query,
      model: _selectedModel,
    });
  } catch (err) {
    rack.innerHTML = `
      <div class="resources-rack-title">🎥 Resources</div>
      <p style="color:var(--danger);font-size:0.82rem">⚠️ ${escHtml(err.message)}</p>`;
    return;
  }

  if (!data.videos?.length) {
    // Still no results — show the form again with an error hint
    const inputId = `res-manual-${rowId}`;
    rack.innerHTML = `
      <div class="resources-rack-title">🎥 Resources</div>
      <p style="font-size:0.8rem;color:var(--danger);margin:0 0 8px">No results for "${escHtml(query)}". Try a different topic.</p>
      <div style="display:flex;gap:8px;align-items:center;max-width:340px">
        <input type="text" id="${inputId}" value="${escHtml(query)}"
          placeholder="e.g. Business Law contracts"
          style="flex:1;height:34px;border:1.5px solid var(--border);border-radius:8px;
                 padding:0 10px;font-size:0.84rem;background:var(--surface);color:var(--text);outline:none"
          onkeydown="if(event.key==='Enter') searchResourcesManually('${rowId}')" />
        <button onclick="searchResourcesManually('${rowId}')"
          style="height:34px;padding:0 14px;border-radius:8px;border:none;
                 background:var(--primary);color:#fff;font-weight:700;font-size:0.82rem;cursor:pointer">
          Search
        </button>
      </div>`;
    return;
  }

  // Show AI intro if provided
  if (data.intro) {
    const introEl = document.createElement('div');
    introEl.className = 'msg assistant';
    introEl.innerHTML = `<div class="msg-avatar">UP</div><div class="msg-bubble">${renderMarkdown(data.intro)}</div>`;
    document.getElementById('chat-messages')?.appendChild(introEl);
    const msgs = document.getElementById('chat-messages');
    if (msgs) msgs.scrollTop = msgs.scrollHeight;
  }

  // Render video cards inside the rack
  const cardsHtml = data.videos.map((v, idx) => {
    const embedId = `yt-embed-${rowId}-m${idx}`;
    return `
      <div class="resource-card resource-card-embed" id="${embedId}-card">
        <div class="resource-embed-preview" id="${embedId}-preview"
             onclick="playYouTubeEmbed('${embedId}','${escHtml(v.embedUrl)}','${escHtml(v.id)}')">
          <img class="resource-thumb" src="${escHtml(v.thumbnail)}" alt="${escHtml(v.title)}"
               loading="lazy" onerror="this.style.display='none'">
          <div class="resource-play-btn">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="#fff">
              <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
          </div>
        </div>
        <div id="${embedId}-player" class="resource-embed-player" style="display:none"></div>
        <div class="resource-info">
          <div class="resource-title">${escHtml(v.title)}</div>
          <div class="resource-channel">${escHtml(v.channel)}</div>
        </div>
      </div>`;
  }).join('');

  rack.innerHTML = `<div class="resources-rack-title">🎥 Videos — ${escHtml(data.topic || query)}</div>${cardsHtml}`;
};

// Swap thumbnail preview with embedded iframe player
window.playYouTubeEmbed = function (embedId, embedUrl, videoId) {
  const preview = document.getElementById(`${embedId}-preview`);
  const player  = document.getElementById(`${embedId}-player`);
  if (!preview || !player) return;
  preview.style.display = 'none';
  player.style.display  = 'block';
  player.innerHTML = `
    <iframe
      src="${escHtml(embedUrl)}"
      title="YouTube video"
      frameborder="0"
      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
      allowfullscreen
      style="width:100%;height:200px;border-radius:8px;display:block">
    </iframe>`;
};

// Helper: inject raw HTML as a div after an action row
function _injectAfterRow(rowEl, html) {
  const div = document.createElement('div');
  div.style.cssText = 'padding:4px 0 8px 40px';
  div.innerHTML = html;
  rowEl.after(div);
}

// ══════════════════════════════════════════════════════════════════════════════
// VOICE INTERACTION  — record → Groq Whisper → text → AI → TTS
// ══════════════════════════════════════════════════════════════════════════════

let _mediaRecorder  = null;
let _audioChunks    = [];
let _voiceTimerInt  = null;
let _voiceElapsed   = 0;          // seconds recorded
const _MAX_RECORD_S = 120;        // hard cap — 2 min per voice note

// ── Enable / disable mic button alongside chat enable/disable ─────────────────
function _setMicEnabled(enabled) {
  const btn = document.getElementById('mic-btn');
  if (btn) btn.disabled = !enabled;
}

// Patch into enableChat / disableChat
const _origEnableChat  = enableChat;  // eslint-disable-line no-use-before-define
const _origDisableChat = disableChat;

// ── Start / stop recording ────────────────────────────────────────────────────
window.toggleVoiceInput = async function () {
  if (_mediaRecorder && _mediaRecorder.state === 'recording') {
    _stopRecording();
  } else {
    await _startRecording();
  }
};

async function _startRecording() {
  if (!documentContext) return;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    _showVoiceError('Microphone access denied. Please allow mic access and try again.');
    return;
  }

  // Pick the best supported MIME type
  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
    .find(t => MediaRecorder.isTypeSupported(t)) || '';

  _audioChunks   = [];
  _mediaRecorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  _mediaRecorder.addEventListener('dataavailable', e => {
    if (e.data?.size > 0) _audioChunks.push(e.data);
  });
  _mediaRecorder.addEventListener('stop', _handleRecordingStop);
  _mediaRecorder.start(250); // collect chunks every 250 ms

  // UI — recording state
  const btn = document.getElementById('mic-btn');
  btn.classList.add('recording');
  document.getElementById('mic-icon-idle').style.display = 'none';
  document.getElementById('mic-icon-stop').style.display = '';

  const bar = document.getElementById('voice-status-bar');
  bar.className = 'voice-status-bar visible';
  document.getElementById('voice-status-text').textContent = 'Recording…';

  _voiceElapsed  = 0;
  _voiceTimerInt = setInterval(() => {
    _voiceElapsed++;
    document.getElementById('voice-timer').textContent = _fmtVoiceTime(_voiceElapsed);
    if (_voiceElapsed >= _MAX_RECORD_S) _stopRecording();
  }, 1000);
}

function _stopRecording() {
  if (!_mediaRecorder) return;
  clearInterval(_voiceTimerInt);
  _mediaRecorder.stop();
  _mediaRecorder.stream?.getTracks().forEach(t => t.stop());

  // UI — transcribing state
  const btn = document.getElementById('mic-btn');
  btn.classList.remove('recording');
  btn.classList.add('transcribing');
  document.getElementById('mic-icon-idle').style.display = '';
  document.getElementById('mic-icon-stop').style.display = 'none';

  const bar = document.getElementById('voice-status-bar');
  bar.className = 'voice-status-bar transcribing visible';
  document.getElementById('voice-status-text').textContent = 'Transcribing…';
  document.getElementById('voice-timer').textContent = '';
  document.getElementById('voice-cancel-btn') && (document.querySelector('.voice-cancel-btn').style.display = 'none');
}

window.cancelVoice = function () {
  if (_mediaRecorder) {
    _mediaRecorder.removeEventListener('stop', _handleRecordingStop);
    _mediaRecorder.stop();
    _mediaRecorder.stream?.getTracks().forEach(t => t.stop());
    _mediaRecorder = null;
  }
  clearInterval(_voiceTimerInt);
  _resetVoiceUI();
};

async function _handleRecordingStop() {
  _mediaRecorder = null;
  if (!_audioChunks.length) { _resetVoiceUI(); return; }

  const mimeType = _audioChunks[0].type || 'audio/webm';
  const blob      = new Blob(_audioChunks, { type: mimeType });
  _audioChunks    = [];

  const ext  = mimeType.includes('ogg') ? 'ogg' : mimeType.includes('mp4') ? 'mp4' : 'webm';
  const form = new FormData();
  form.append('audio', blob, `voice.${ext}`);

  try {
    const res  = await api.postForm('/brainstorm/transcribe', form);
    const text = (res?.text || '').trim();
    if (!text) {
      _showVoiceError("Couldn't catch that — please try again.");
      return;
    }
    _resetVoiceUI();
    // Put transcribed text in the input and send immediately
    const input = document.getElementById('chat-input');
    input.value = text;
    autoResize(input);
    sendMessage();
  } catch (err) {
    _showVoiceError(err.message || 'Transcription failed.');
  }
}

function _resetVoiceUI() {
  clearInterval(_voiceTimerInt);
  const btn = document.getElementById('mic-btn');
  if (btn) {
    btn.classList.remove('recording', 'transcribing');
    btn.disabled = !documentContext;
  }
  document.getElementById('mic-icon-idle').style.display = '';
  document.getElementById('mic-icon-stop').style.display = 'none';
  const bar = document.getElementById('voice-status-bar');
  if (bar) bar.className = 'voice-status-bar';
  const cancelBtn = document.querySelector('.voice-cancel-btn');
  if (cancelBtn) cancelBtn.style.display = '';
}

function _showVoiceError(msg) {
  _resetVoiceUI();
  const bar = document.getElementById('voice-status-bar');
  if (!bar) return;
  bar.className = 'voice-status-bar visible';
  bar.style.background   = '#fef2f2';
  bar.style.borderColor  = '#fecaca';
  bar.style.color        = '#b91c1c';
  bar.innerHTML = `<span>⚠️ ${escHtml(msg)}</span>
    <button class="voice-cancel-btn" onclick="cancelVoice()">✕</button>`;
  setTimeout(() => { if (bar) bar.className = 'voice-status-bar'; bar.style = ''; }, 4000);
}

function _fmtVoiceTime(s) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Sync mic enable/disable with document context
function _syncMicBtn() {
  _setMicEnabled(!!documentContext);
}

// ── Text-to-Speech ────────────────────────────────────────────────────────────
let _currentUtterance = null;
let _currentTtsBtn    = null;

window.toggleTTS = function (btn) {
  const bubble  = btn.closest('.msg')?.querySelector('.msg-bubble');
  const rawText = bubble?.innerText || '';

  // If already speaking this message — stop
  if (_currentTtsBtn === btn && speechSynthesis.speaking) {
    speechSynthesis.cancel();
    _resetTtsBtn(btn);
    return;
  }

  // Cancel any ongoing speech
  if (speechSynthesis.speaking) {
    speechSynthesis.cancel();
    if (_currentTtsBtn) _resetTtsBtn(_currentTtsBtn);
  }

  const utterance   = new SpeechSynthesisUtterance(rawText);
  utterance.rate    = 0.95;
  utterance.pitch   = 1;
  utterance.volume  = 1;

  // Prefer a natural-sounding voice
  const voices = speechSynthesis.getVoices();
  const pref   = voices.find(v =>
    /Google|Natural|Premium|Enhanced|Microsoft|Samantha/i.test(v.name) && /en/i.test(v.lang)
  ) || voices.find(v => /en/i.test(v.lang));
  if (pref) utterance.voice = pref;

  utterance.onstart = () => {
    btn.classList.add('speaking');
    btn.querySelector('.tts-tooltip').textContent = 'Stop';
    btn.title = 'Stop';
    _currentTtsBtn    = btn;
    _currentUtterance = utterance;
  };
  utterance.onend = utterance.onerror = () => {
    _resetTtsBtn(btn);
    _currentTtsBtn    = null;
    _currentUtterance = null;
  };

  speechSynthesis.speak(utterance);
};

function _resetTtsBtn(btn) {
  if (!btn) return;
  btn.classList.remove('speaking');
  const tip = btn.querySelector('.tts-tooltip');
  if (tip) tip.textContent = 'Listen';
  btn.title = 'Listen to this message';
}

// Voices are loaded asynchronously — pre-load them
if ('speechSynthesis' in window) {
  speechSynthesis.getVoices(); // warm up
  speechSynthesis.addEventListener('voiceschanged', () => speechSynthesis.getVoices());
}

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
    // Read current PDF page so the AI reads the right pages for scanned docs
    const _pageEl = document.getElementById('pdf-page-counter');
    const _currentPage = _pageEl
      ? (parseInt(_pageEl.textContent.split('/')[0].trim()) || 1)
      : 1;

    const res = await api.post('/brainstorm/chat', {
      message:      text,
      context:      documentContext,
      history:      chatHistory.slice(0, -1),
      session_id:   currentSessionId || undefined,
      current_page: _currentPage,
      model:        _selectedModel,
    });
    removeTyping(typingId);
    const msgEl = appendBubble('assistant', res.reply);
    chatHistory.push({ role: 'assistant', content: res.reply });

    // Attach action buttons below every assistant reply during a study session
    const topicHint = _extractTopic(text, res.reply);
    _appendMessageActions(msgEl, topicHint);

    // If user seems confused, also auto-nudge with a visual button highlight
    if (_isConfused(text)) {
      const visBtn = msgEl.nextElementSibling?.querySelector('[id$="-vis"]');
      if (visBtn) {
        visBtn.style.borderColor = 'var(--primary)';
        visBtn.style.color       = 'var(--primary)';
      }
    }

    _resetReadingTimers(); // user interacted — restart the inactivity clock
    _bsSave();
    // On mobile: show badge on UrPadi tab if user is on Document tab
    if (window.innerWidth <= 768) {
      const layout = document.getElementById('bs-layout');
      if (!layout.classList.contains('mobile-chat-active')) {
        const badge = document.getElementById('mobile-fab-badge');
        if (badge) badge.classList.add('visible');
      }
    }
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

  const ttsId   = `tts-${Date.now()}-${Math.random().toString(36).slice(2,6)}`;
  const copyBtn = role === 'assistant' ? `
    <button class="msg-copy-btn" title="Copy message" onclick="
      navigator.clipboard.writeText(this.closest('.msg').querySelector('.msg-bubble').innerText).then(() => {
        const t = this.querySelector('.copy-tooltip');
        t.textContent = 'Copied!';
        this.classList.add('copied');
        setTimeout(() => { t.textContent = 'Copy'; this.classList.remove('copied'); }, 2000);
      });
    ">
      <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <rect x='9' y='9' width='13' height='13' rx='2' ry='2'/>
        <path d='M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1'/>
      </svg>
      <span class="copy-tooltip">Copy</span>
    </button>
    <button class="msg-tts-btn" id="${ttsId}" title="Listen to this message"
      onclick="toggleTTS(this)">
      <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <polygon points='11 5 6 9 2 9 2 15 6 15 11 19 11 5'/>
        <path d='M19.07 4.93a10 10 0 010 14.14'/>
        <path d='M15.54 8.46a5 5 0 010 7.07'/>
      </svg>
      <span class="tts-tooltip">Listen</span>
    </button>` : '';

  el.innerHTML = `
    <div class="msg-avatar">${role === 'user' ? _initials : 'UP'}</div>
    <div class="msg-bubble">${html}</div>${copyBtn}`;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return el;
}

function showTyping() {
  const id = `typing-${Date.now()}`;
  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.id = id;
  el.innerHTML = `
    <div class="msg-avatar">UP</div>
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

// ── Study Timer ───────────────────────────────────────────────────────────────
let _timerDuration  = 0;    // total seconds
let _timerRemaining = 0;    // seconds left
let _timerInterval  = null;
let _timerRunning   = false;
let _timerChecksDone = new Set(); // which % checkpoints have fired

const _TIMER_KEY = 'pritis_study_timer';

function _timerSave() {
  if (!_timerDuration) return;
  localStorage.setItem(_TIMER_KEY, JSON.stringify({
    duration:   _timerDuration,
    remaining:  _timerRemaining,
    running:    _timerRunning,
    checksDone: [..._timerChecksDone],
    savedAt:    Date.now(),
  }));
}

function _timerTick() {
  if (_timerRemaining <= 0) {
    _timerFinish();
    return;
  }
  _timerRemaining--;
  _timerRender();
  _timerCheckpoint();
  if (_timerRemaining % 5 === 0) _timerSave(); // persist every 5 s
}

function _timerRender() {
  const display  = document.getElementById('timer-display');
  const bar      = document.getElementById('timer-progress-bar');

  const m = String(Math.floor(_timerRemaining / 60)).padStart(2, '0');
  const s = String(_timerRemaining % 60).padStart(2, '0');
  const timeStr = `${m}:${s}`;

  if (display) display.textContent = timeStr;

  const pct = _timerDuration > 0
    ? ((_timerDuration - _timerRemaining) / _timerDuration) * 100
    : 0;
  if (bar) bar.style.width = pct + '%';

  // Colour states
  const ratio = _timerDuration > 0 ? _timerRemaining / _timerDuration : 1;
  const colorState = _timerRunning ? (ratio <= 0.1 ? 'warning' : 'running') : '';
  if (display) display.className = 'timer-display ' + colorState;
  if (bar) bar.className = 'timer-progress-bar' + (ratio <= 0.1 ? ' warning' : '');

  // Update start/pause icon
  const icon = document.getElementById('timer-btn-icon');
  if (icon) {
    icon.innerHTML = _timerRunning
      ? '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>'
      : '<polygon points="5 3 19 12 5 21 5 3"/>';
  }

  // ── Chat panel badge ──
  const badge       = document.getElementById('chat-timer-badge');
  const badgeDisplay = document.getElementById('chat-timer-display');
  if (badge && badgeDisplay) {
    badgeDisplay.textContent = timeStr;
    badge.className = 'chat-timer-badge' + (ratio <= 0.1 ? ' warning' : '');
  }

  // ── Inline timer (doc panel header) ──
  const inlinePill    = document.getElementById('inline-timer');
  const inlineDisplay = document.getElementById('inline-timer-display');
  if (inlinePill && inlineDisplay) {
    inlineDisplay.textContent = timeStr;
    inlinePill.className = 'inline-timer' + (ratio <= 0.1 ? ' warning' : '');
  }

  // ── Floating timer pill (fixed, always visible) ──
  const floatPill    = document.getElementById('floating-timer');
  const floatDisplay = document.getElementById('floating-timer-display');
  if (floatPill && floatDisplay) {
    floatDisplay.textContent = timeStr;
    floatPill.className = 'floating-timer' + (ratio <= 0.1 ? ' warning' : '');
  }
}

// Injects an AI message into the chat from the timer system.
// Does NOT force-open the chat — shows a badge nudge on mobile instead.
function _injectTimerChatMessage(markdown, { showActions = false } = {}) {
  if (!documentContext) return null; // only relevant during an active study session

  const container = document.getElementById('chat-messages');
  if (!container) return null;

  // Build bubble with raw HTML support (for quiz CTA links)
  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.innerHTML = `
    <div class="msg-avatar">UP</div>
    <div class="msg-bubble">${renderMarkdown(markdown)}</div>`;
  document.getElementById('chat-empty')?.remove();
  container.appendChild(el);

  if (showActions) {
    const topic = _extractTopic('', markdown);
    _appendMessageActions(el, topic);
  }

  container.scrollTop = container.scrollHeight;
  chatHistory.push({ role: 'assistant', content: markdown });

  // On mobile: nudge badge without force-opening chat
  if (window.matchMedia('(max-width:768px)').matches && !_mobileChatOpen) {
    const badge = document.getElementById('mobile-fab-badge');
    if (badge) badge.classList.add('visible');
  }
  return el;
}

function _timerCheckpoint() {
  const elapsed = _timerDuration - _timerRemaining;
  const pct = elapsed / _timerDuration;

  if (!_timerChecksDone.has(25) && pct >= 0.25) {
    _timerChecksDone.add(25);
    _showTimerNotification("You're 25% through your study session — great start! Keep your focus.");
    _injectTimerChatMessage(
      "⏱️ **25% check-in!** You're making good progress.\n\n" +
      "Staying focused? Try to summarise in one sentence what you've read so far — it helps lock it in. " +
      "I'm here if you want to quiz yourself or clarify anything!",
      { showActions: true }
    );
  }
  if (!_timerChecksDone.has(50) && pct >= 0.5) {
    _timerChecksDone.add(50);
    _showTimerNotification("Halfway there! Pause and reflect — what are the key ideas so far?");
    _injectTimerChatMessage(
      "🎯 **Halfway through your session!**\n\n" +
      "Great work so far. Try this: **can you explain the main concept in your own words?** " +
      "Type it below and I'll give you feedback — or just ask me to recap the key ideas.",
      { showActions: true }
    );
  }
  if (!_timerChecksDone.has(80) && pct >= 0.8) {
    _timerChecksDone.add(80);
    const minsLeft = Math.ceil(_timerRemaining / 60);
    _showTimerNotification(`${minsLeft} minutes left. <a href="ai-generate.html">Generate a quiz →</a>`, 'warning');
    _injectTimerChatMessage(
      `⚡ **Almost done — ${minsLeft} minutes left!**\n\n` +
      "You've put in solid study time. Now let's test what you know.\n\n" +
      `<a href="ai-generate.html?auto=1" class="quiz-cta-btn" style="display:inline-flex;align-items:center;gap:6px;margin-top:8px;padding:8px 16px;background:var(--primary);color:#fff;border-radius:8px;font-weight:700;font-size:0.84rem;text-decoration:none">` +
      `📝 Generate Quiz from this Document</a>`
    );
  }
}

function _timerFinish() {
  clearInterval(_timerInterval);
  _timerInterval = null;
  _timerRunning  = false;
  _timerRemaining = 0;
  localStorage.removeItem(_TIMER_KEY);

  const display = document.getElementById('timer-display');
  const bar     = document.getElementById('timer-progress-bar');
  if (display) { display.textContent = 'Done!'; display.className = 'timer-display done'; }
  if (bar)     { bar.className = 'timer-progress-bar done'; }

  const startBtn = document.getElementById('timer-start-btn');
  const stopBtn  = document.getElementById('timer-stop-btn');
  if (startBtn) startBtn.style.display = 'none';
  if (stopBtn)  stopBtn.style.display  = 'none';

  // Update timer indicators to "Done"
  const badge        = document.getElementById('chat-timer-badge');
  const badgeDisplay = document.getElementById('chat-timer-display');
  if (badge)        { badge.style.display = 'flex'; badge.className = 'chat-timer-badge done'; }
  if (badgeDisplay) badgeDisplay.textContent = 'Done!';
  const inlinePillD  = document.getElementById('inline-timer');
  const inlineDispD  = document.getElementById('inline-timer-display');
  if (inlinePillD)  { inlinePillD.style.display = 'flex'; inlinePillD.className = 'inline-timer done'; }
  if (inlineDispD)  inlineDispD.textContent = 'Done!';
  const pill        = document.getElementById('floating-timer');
  const pillDisplay = document.getElementById('floating-timer-display');
  if (pill)        { pill.style.display = 'flex'; pill.className = 'floating-timer done'; }
  if (pillDisplay) pillDisplay.textContent = 'Done!';

  _showTimerNotification('<strong>Session complete!</strong> Great work! 🎉', 'done');
  _injectSessionEndChoice();
}

// ── Session-end interactive choice card ───────────────────────────────────────
function _injectSessionEndChoice() {
  if (!documentContext) return;
  const container = document.getElementById('chat-messages');
  if (!container) return;

  document.getElementById('chat-empty')?.remove();

  const card = document.createElement('div');
  card.className = 'msg assistant';
  card.id = 'session-end-card';
  card.innerHTML = `
    <div class="msg-avatar">UP</div>
    <div class="msg-bubble" style="max-width:340px">
      <p style="margin:0 0 10px;font-weight:700;font-size:0.95rem">🎉 Session complete! Amazing work.</p>
      <p style="margin:0 0 14px;font-size:0.88rem;color:var(--text-muted)">What would you like to do next?</p>
      <div style="display:flex;flex-direction:column;gap:8px;">
        <button onclick="extendStudyTime()" style="
          padding:10px 14px;border-radius:8px;border:2px solid var(--primary);
          background:transparent;color:var(--primary);font-weight:700;font-size:0.85rem;
          cursor:pointer;text-align:left;transition:all .15s"
          onmouseover="this.style.background='var(--primary)';this.style.color='#fff'"
          onmouseout="this.style.background='transparent';this.style.color='var(--primary)'">
          ⏱️ Extend my reading time
        </button>
        <button onclick="startInlineQuiz()" style="
          padding:10px 14px;border-radius:8px;border:none;
          background:var(--primary);color:#fff;font-weight:700;font-size:0.85rem;
          cursor:pointer;text-align:left;transition:opacity .15s"
          onmouseover="this.style.opacity='.85'"
          onmouseout="this.style.opacity='1'">
          📝 Attempt a short quiz (5 questions)
        </button>
      </div>
    </div>`;
  container.appendChild(card);
  container.scrollTop = container.scrollHeight;

  // Badge nudge on mobile
  if (window.matchMedia('(max-width:768px)').matches && !_mobileChatOpen) {
    document.getElementById('mobile-fab-badge')?.classList.add('visible');
  }
}

// Extend time — replaces the choice card with quick extend options
window.extendStudyTime = function () {
  const card = document.getElementById('session-end-card');
  if (card) {
    const bubble = card.querySelector('.msg-bubble');
    bubble.innerHTML = `
      <p style="margin:0 0 10px;font-weight:700;font-size:0.92rem">⏱️ How much more time?</p>
      <div style="display:flex;flex-wrap:wrap;gap:7px;margin-bottom:10px">
        <button onclick="applyExtension(15)" class="setup-duration-pill">+15 min</button>
        <button onclick="applyExtension(30)" class="setup-duration-pill">+30 min</button>
        <button onclick="applyExtension(45)" class="setup-duration-pill">+45 min</button>
        <button onclick="applyExtension(60)" class="setup-duration-pill">+1 hr</button>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:0.82rem;color:var(--text-muted);font-weight:600">Custom:</span>
        <input type="number" id="extend-custom-mins" min="1" max="480" placeholder="e.g. 20"
               class="form-control" style="width:90px" />
        <button onclick="applyExtension(parseInt(document.getElementById('extend-custom-mins').value)||0)"
                style="padding:6px 12px;border-radius:7px;border:none;background:var(--primary);
                       color:#fff;font-weight:700;font-size:0.82rem;cursor:pointer">Go</button>
      </div>`;
  }
};

window.applyExtension = function (mins) {
  if (!mins || mins < 1) return;
  const addSecs = mins * 60;
  _timerDuration  += addSecs;
  _timerRemaining  = addSecs;
  _timerRunning    = false; // reset so timerStartPause restarts interval

  // Re-enable timer UI
  const display = document.getElementById('timer-display');
  const bar     = document.getElementById('timer-progress-bar');
  if (display) { display.className = 'timer-display'; }
  if (bar)     { bar.className = 'timer-progress-bar'; }
  const startBtn = document.getElementById('timer-start-btn');
  const stopBtn  = document.getElementById('timer-stop-btn');
  if (startBtn) startBtn.style.display = '';
  if (stopBtn)  stopBtn.style.display  = '';

  timerStartPause(); // auto-start

  // Replace choice card with confirmation
  const card = document.getElementById('session-end-card');
  if (card) {
    const bubble = card.querySelector('.msg-bubble');
    bubble.innerHTML = `<p style="margin:0;font-size:0.9rem">⏱️ Got it! Added <strong>${mins} minutes</strong>. Keep going — you've got this! 💪</p>`;
  }
};

// ── Inline 5-question quiz in chat ────────────────────────────────────────────
let _inlineQuiz = null; // { questions, current, score, msgEl }

window.startInlineQuiz = function () {
  // Replace choice card with loading state
  const card = document.getElementById('session-end-card');
  if (card) {
    const bubble = card.querySelector('.msg-bubble');
    bubble.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;padding:4px 0">
        <div class="typing-indicator"><span></span><span></span><span></span></div>
        <span style="font-size:0.87rem;color:var(--text-muted)">Generating your quiz…</span>
      </div>`;
  }

  const prompt =
    'Generate exactly 5 multiple-choice quiz questions based on this document. ' +
    'Return ONLY valid JSON in this exact format, no extra text:\n' +
    '{"questions":[{"q":"Question text","options":["A","B","C","D"],"answer":0}]}\n' +
    'answer is the 0-based index of the correct option.';

  api.post('/brainstorm/chat', {
    message: prompt,
    context: documentContext,
    history: [],
    model: _selectedModel,
  }).then(res => {
    let parsed;
    try {
      const raw = res.reply.replace(/```json|```/g, '').trim();
      const start = raw.indexOf('{');
      const end   = raw.lastIndexOf('}');
      parsed = JSON.parse(raw.slice(start, end + 1));
    } catch {
      if (card) card.querySelector('.msg-bubble').innerHTML =
        `<p style="color:var(--danger)">⚠️ Couldn't generate the quiz. <a href="ai-generate.html?auto=1" style="color:var(--primary);font-weight:700">Try the full quiz generator →</a></p>`;
      return;
    }

    const questions = (parsed.questions || []).slice(0, 5);
    if (!questions.length) {
      if (card) card.querySelector('.msg-bubble').innerHTML =
        `<p style="color:var(--danger)">⚠️ No questions returned. <a href="ai-generate.html?auto=1" style="color:var(--primary);font-weight:700">Try the full quiz generator →</a></p>`;
      return;
    }

    _inlineQuiz = { questions, current: 0, score: 0 };

    // Replace loading state with Q1
    if (card) card.remove();
    _renderInlineQuestion();
  }).catch(() => {
    if (card) card.querySelector('.msg-bubble').innerHTML =
      `<p style="color:var(--danger)">⚠️ Something went wrong. <a href="ai-generate.html?auto=1" style="color:var(--primary);font-weight:700">Try the full quiz generator →</a></p>`;
  });
};

function _renderInlineQuestion() {
  const { questions, current } = _inlineQuiz;
  const q = questions[current];
  const container = document.getElementById('chat-messages');
  if (!container || !q) return;

  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.id = `inline-q-${current}`;

  const optionsHtml = q.options.map((opt, i) => `
    <button onclick="answerInlineQuiz(${current},${i})" style="
      display:block;width:100%;margin-bottom:6px;padding:8px 12px;
      border-radius:8px;border:1.5px solid var(--border);background:var(--bg);
      color:var(--text);font-size:0.85rem;text-align:left;cursor:pointer;
      transition:all .15s"
      onmouseover="this.style.borderColor='var(--primary)';this.style.color='var(--primary)'"
      onmouseout="if(!this.disabled){this.style.borderColor='var(--border)';this.style.color='var(--text)'}">
      <strong>${String.fromCharCode(65+i)}.</strong> ${escHtml(opt)}
    </button>`).join('');

  el.innerHTML = `
    <div class="msg-avatar">UP</div>
    <div class="msg-bubble" style="max-width:380px">
      <p style="margin:0 0 4px;font-size:0.75rem;color:var(--text-muted);font-weight:600">
        QUESTION ${current + 1} OF ${questions.length}
      </p>
      <p style="margin:0 0 12px;font-weight:700;font-size:0.9rem">${escHtml(q.q)}</p>
      <div id="inline-q-options-${current}">${optionsHtml}</div>
    </div>`;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}

window.answerInlineQuiz = function (qIndex, chosenIndex) {
  if (!_inlineQuiz || _inlineQuiz.current !== qIndex) return;
  const q = _inlineQuiz.questions[qIndex];

  // Disable all option buttons for this question
  const optionsDiv = document.getElementById(`inline-q-options-${qIndex}`);
  if (optionsDiv) {
    optionsDiv.querySelectorAll('button').forEach((btn, i) => {
      btn.disabled = true;
      btn.style.cursor = 'default';
      btn.onmouseover = null;
      btn.onmouseout  = null;
      if (i === q.answer) {
        btn.style.background    = '#dcfce7';
        btn.style.borderColor   = '#16a34a';
        btn.style.color         = '#15803d';
      } else if (i === chosenIndex) {
        btn.style.background    = '#fee2e2';
        btn.style.borderColor   = '#dc2626';
        btn.style.color         = '#b91c1c';
      }
    });
  }

  const correct = chosenIndex === q.answer;
  if (correct) _inlineQuiz.score++;

  // Show brief feedback then advance
  const feedbackEl = document.createElement('div');
  feedbackEl.className = 'msg assistant';
  feedbackEl.innerHTML = `
    <div class="msg-avatar">UP</div>
    <div class="msg-bubble" style="padding:8px 14px;font-size:0.86rem">
      ${correct
        ? '✅ <strong>Correct!</strong> Well done.'
        : `❌ <strong>Not quite.</strong> The correct answer was <strong>${String.fromCharCode(65 + q.answer)}. ${escHtml(q.options[q.answer])}</strong>.`}
    </div>`;
  const container = document.getElementById('chat-messages');
  container.appendChild(feedbackEl);
  container.scrollTop = container.scrollHeight;

  _inlineQuiz.current++;

  if (_inlineQuiz.current < _inlineQuiz.questions.length) {
    setTimeout(_renderInlineQuestion, 600);
  } else {
    setTimeout(_showInlineQuizResult, 800);
  }
};

function _showInlineQuizResult() {
  const { score, questions } = _inlineQuiz;
  const total   = questions.length;
  const pct     = Math.round((score / total) * 100);
  const emoji   = pct >= 80 ? '🏆' : pct >= 60 ? '👍' : '📚';
  const comment = pct >= 80
    ? "Outstanding! You clearly understand the material."
    : pct >= 60
      ? "Good effort! A bit more review and you'll nail it."
      : "Keep studying — practice makes perfect!";

  const container = document.getElementById('chat-messages');
  if (!container) return;

  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.innerHTML = `
    <div class="msg-avatar">UP</div>
    <div class="msg-bubble" style="max-width:340px">
      <p style="margin:0 0 6px;font-size:1.3rem;text-align:center">${emoji}</p>
      <p style="margin:0 0 4px;font-weight:700;font-size:1rem;text-align:center">
        You scored ${score}/${total} (${pct}%)
      </p>
      <p style="margin:0 0 14px;font-size:0.87rem;color:var(--text-muted);text-align:center">${comment}</p>
      <a href="ai-generate.html?auto=1"
         style="display:block;padding:10px 16px;border-radius:8px;
                background:var(--primary);color:#fff;font-weight:700;
                font-size:0.87rem;text-align:center;text-decoration:none">
        📝 Practice more questions from this document →
      </a>
    </div>`;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  _inlineQuiz = null;
}

window.onTimerPresetChange = function (val) {
  if (!val) return;
  _timerDuration  = parseInt(val, 10);
  _timerRemaining = _timerDuration;
  _timerRunning   = false;
  _timerChecksDone.clear();
  clearInterval(_timerInterval);
  _timerInterval = null;

  const startBtn = document.getElementById('timer-start-btn');
  const stopBtn  = document.getElementById('timer-stop-btn');
  if (startBtn) startBtn.style.display = '';
  if (stopBtn)  stopBtn.style.display  = '';

  // Show timer indicators whenever a preset is picked
  const badge      = document.getElementById('chat-timer-badge');
  if (badge) badge.style.display = 'flex';
  const inlinePill = document.getElementById('inline-timer');
  if (inlinePill) inlinePill.style.display = 'flex';
  const floatPill  = document.getElementById('floating-timer');
  if (floatPill) floatPill.style.display = 'flex';

  _timerRender();
  _timerSave();
};

window.timerStartPause = function () {
  if (!_timerDuration || _timerRemaining <= 0) return;
  if (_timerRunning) {
    clearInterval(_timerInterval);
    _timerInterval = null;
    _timerRunning  = false;
  } else {
    _timerRunning  = true;
    _timerInterval = setInterval(_timerTick, 1000);
  }
  _timerRender();
  _timerSave();
};

window.timerStop = function () {
  clearInterval(_timerInterval);
  _timerInterval  = null;
  _timerRunning   = false;
  _timerRemaining = 0;
  _timerDuration  = 0;
  _timerChecksDone.clear();
  localStorage.removeItem(_TIMER_KEY);

  const display  = document.getElementById('timer-display');
  const bar      = document.getElementById('timer-progress-bar');
  const preset   = document.getElementById('timer-preset');
  const startBtn = document.getElementById('timer-start-btn');
  const stopBtn  = document.getElementById('timer-stop-btn');
  if (display)  { display.textContent = '00:00'; display.className = 'timer-display'; }
  if (bar)      { bar.style.width = '0%'; bar.className = 'timer-progress-bar'; }
  if (preset)   preset.value = '';
  if (startBtn) startBtn.style.display = 'none';
  if (stopBtn)  stopBtn.style.display  = 'none';

  // Hide timer indicators
  const badge      = document.getElementById('chat-timer-badge');
  if (badge) badge.style.display = 'none';
  const inlinePill = document.getElementById('inline-timer');
  if (inlinePill) inlinePill.style.display = 'none';
  const floatPill  = document.getElementById('floating-timer');
  if (floatPill) floatPill.style.display = 'none';
};

// Show/hide the timer bar whenever a document is loaded / cleared
function _showTimerBar(show) {
  const bar = document.getElementById('study-timer-bar');
  if (bar) bar.style.display = show ? '' : 'none';
  if (!show) timerStop();
}

// Restore timer state after navigating back to this page
function _timerRestoreFromStorage() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(_TIMER_KEY)); } catch (_) {}
  if (!saved || !saved.duration || saved.remaining <= 0) return;

  // Subtract time that elapsed while the user was away
  const elapsed   = saved.running ? Math.floor((Date.now() - saved.savedAt) / 1000) : 0;
  const remaining = Math.max(0, saved.remaining - elapsed);

  _timerDuration   = saved.duration;
  _timerRemaining  = remaining;
  _timerRunning    = false;
  _timerChecksDone = new Set(saved.checksDone || []);

  // Show timer UI elements
  const startBtn  = document.getElementById('timer-start-btn');
  const stopBtn   = document.getElementById('timer-stop-btn');
  const badge     = document.getElementById('chat-timer-badge');
  const inline    = document.getElementById('inline-timer');
  const floatPill = document.getElementById('floating-timer');
  const timerBar  = document.getElementById('study-timer-bar');
  if (startBtn)  startBtn.style.display  = '';
  if (stopBtn)   stopBtn.style.display   = '';
  if (badge)     badge.style.display     = 'flex';
  if (inline)    inline.style.display    = 'flex';
  if (floatPill) floatPill.style.display = 'flex';
  if (timerBar)  timerBar.style.display  = '';

  if (remaining <= 0) {
    _timerFinish();
    return;
  }

  _timerRender();

  // Auto-resume if the timer was running when the user left
  if (saved.running) {
    _timerRunning  = true;
    _timerInterval = setInterval(_timerTick, 1000);
    _timerRender();
  }
}

_timerRestoreFromStorage();

// ── Study Notifications (toast + bell badge) ──────────────────────────────────
let _notifItems = [];

function _showTimerNotification(msg, type = '') {
  // Track for drawer
  _notifItems.push({ msg, type, time: Date.now() });

  // Bell badge
  const badge = document.getElementById('timer-notif-badge');
  const bell  = document.getElementById('timer-notif-btn');
  if (badge) { badge.textContent = _notifItems.length; badge.classList.add('visible'); }
  if (bell)  bell.classList.add('has-unread');

  // Update drawer list
  _renderNotifDrawer();

  // Toast popup
  const container = document.getElementById('study-notif-toasts');
  if (!container) return;

  const icons = { done: '✅', warning: '⚠️', '': '⏱️' };
  const icon  = icons[type] || '⏱️';

  const toast = document.createElement('div');
  toast.className = `study-notif-toast${type ? ' ' + type : ''}`;
  toast.style.position = 'relative';
  toast.innerHTML = `
    <span class="toast-icon">${icon}</span>
    <span class="toast-body">${msg}</span>
    <button class="toast-close" onclick="this.closest('.study-notif-toast').remove()">×</button>
    <div class="toast-progress"><div class="toast-progress-bar"></div></div>
  `;
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('visible'));

  // Auto-dismiss after 6 s
  const AUTO = 6000;
  setTimeout(() => {
    toast.classList.remove('visible');
    setTimeout(() => toast.remove(), 300);
  }, AUTO);
}

function _renderNotifDrawer() {
  const list = document.getElementById('notif-drawer-list');
  if (!list) return;
  if (!_notifItems.length) {
    list.innerHTML = '<div class="notif-drawer-empty">No notifications yet</div>';
    return;
  }
  list.innerHTML = [..._notifItems].reverse().map(n => {
    const age = Math.round((Date.now() - n.time) / 60000);
    const when = age < 1 ? 'just now' : `${age} min ago`;
    const icons = { done: '✅', warning: '⚠️', '': '⏱️' };
    return `
    <div class="notif-drawer-item">
      <span class="notif-drawer-item-icon">${icons[n.type] || '⏱️'}</span>
      <div>
        <div>${n.msg}</div>
        <div class="notif-drawer-item-time">${when}</div>
      </div>
    </div>`;
  }).join('');
}

window.toggleNotifDrawer = function () {
  const drawer = document.getElementById('notif-drawer');
  if (!drawer) return;
  drawer.classList.toggle('open');
  if (drawer.classList.contains('open')) {
    // Mark as read — clear badge
    const badge = document.getElementById('timer-notif-badge');
    const bell  = document.getElementById('timer-notif-btn');
    if (badge) badge.classList.remove('visible');
    if (bell)  bell.classList.remove('has-unread');
    _renderNotifDrawer();
  }
};

window.clearNotifications = function () {
  _notifItems = [];
  _renderNotifDrawer();
  const badge = document.getElementById('timer-notif-badge');
  const bell  = document.getElementById('timer-notif-btn');
  if (badge) { badge.textContent = '0'; badge.classList.remove('visible'); }
  if (bell)  bell.classList.remove('has-unread');
  document.getElementById('notif-drawer')?.classList.remove('open');
};

// Close drawer when clicking outside
document.addEventListener('click', e => {
  const bar = document.getElementById('study-timer-bar');
  const drawer = document.getElementById('notif-drawer');
  if (bar && drawer && !bar.contains(e.target) && !drawer.contains(e.target)) {
    drawer.classList.remove('open');
  }
});

// ── Guided Study Mode ─────────────────────────────────────────────────────────
let _guidedModeActive = false;
let _guidedStep       = 0;
let _guidedTimer      = null;

async function _guidedStudyWelcome() {
  if (!documentContext) return;

  // Auto-start timer when document is loaded (if configured)
  if (_timerDuration > 0 && !_timerRunning) {
    setTimeout(timerStartPause, 300);
  }

  // ── Ask AI for a tutor-style nudge about what to focus on ─────────────────
  const isShort = documentContext.length < 2000;
  const prompt  = isShort
    ? `You are a study tutor. Read this document and write ONE short, direct sentence (max 20 words) telling the student what they most need to focus on. Start with "You need to focus on..." or "Pay attention to...". Be specific — name the actual topic.`
    : `You are a study tutor. Read this document and write ONE short, direct sentence (max 25 words) telling the student the single most important thing to focus on. Start with "You need to focus on..." or similar. Name the specific topic or concept.`;

  const fullPrompt = isShort
    ? `Read this document and give me a focused study breakdown:\n1. The single most important concept (1 sentence)\n2. 3–4 key topics to understand\n3. One tip for studying this material effectively`
    : `Read this document and give me a focused study breakdown:\n1. The single most important concept to master (1 sentence)\n2. Top 5 key topics in order of importance\n3. One practical tip for studying this material`;

  let teaser   = '';  // short bubble text
  let fullMsg  = '';  // full chat message

  try {
    // Two parallel calls: teaser (fast, short) + full breakdown
    const [teaserRes, fullRes] = await Promise.all([
      api.post('/brainstorm/chat', { message: prompt,      context: documentContext, history: [], model: _selectedModel }),
      api.post('/brainstorm/chat', { message: fullPrompt,  context: documentContext, history: [], model: _selectedModel }),
    ]);

    // Teaser: strip markdown, keep to ~80 chars
    teaser  = (teaserRes.reply || '').replace(/\*\*/g, '').replace(/\n.*/s, '').trim().slice(0, 100);
    fullMsg = fullRes.reply || '';
  } catch (_) {
    teaser  = 'Your document is ready — tap to see what to focus on!';
    fullMsg = _setupGoal
      ? `📄 Document loaded! Let's focus on **"${_setupGoal}"**. What would you like to start with?`
      : "📄 I've read your document. Ask me anything about it!";
  }

  // ── Show full breakdown in chat ───────────────────────────────────────────
  const goalNote = _setupGoal ? `\n\n🎯 **Your goal:** "${_setupGoal}"` : '';
  const chatMsg  = fullMsg + goalNote;
  const welcomeMsgEl = appendBubble('assistant', chatMsg);
  chatHistory.push({ role: 'assistant', content: chatMsg });
  _appendMessageActions(welcomeMsgEl, _extractTopic('', fullMsg));
  document.getElementById('chat-messages').scrollTop = 9999;

  // ── Show teaser in greeting bubble ────────────────────────────────────────
  // Always show the document bubble regardless of whether user dismissed the setup one
  _fabGreetingDismissed = false;
  const bubbleHtml =
    `<span style="font-size:0.8rem;color:var(--text-muted);display:block;margin-bottom:3px">📄 UrPadi has read your doc</span>` +
    `<strong style="font-size:0.84rem">${escHtml(teaser)}</strong>` +
    `<span style="display:block;margin-top:5px;color:var(--primary);font-size:0.76rem;font-weight:600">Open chat to see full breakdown 👀</span>`;
  _showGreeting(bubbleHtml);
}

window.startGuidedMode = function () {
  _guidedModeActive = true;
  _guidedStep = 0;
  document.getElementById('guided-prompt-btns')?.remove();
  _runGuidedStep();
};

window.dismissGuidedPrompt = function () {
  document.getElementById('guided-prompt-btns')?.remove();
  appendBubble('assistant', "No problem! I'm here whenever you have a question. Happy studying! 📚");
  chatHistory.push({ role: 'assistant', content: "No problem! I'm here whenever you have a question. Happy studying! 📚" });
};

function _runGuidedStep() {
  const steps = [
    { mins: 5,  prompt: "Let's start! **Read the first section** of your document. I'll check in with you in 5 minutes." },
    { mins: 10, prompt: "Great progress! **Continue reading** — take your time. I'll check back in 10 minutes." },
    { mins: 10, prompt: "You're doing well! Keep going with the next section. Check in coming up in 10 minutes." },
  ];

  const step = steps[Math.min(_guidedStep, steps.length - 1)];
  appendBubble('assistant', step.prompt);
  chatHistory.push({ role: 'assistant', content: step.prompt });

  if (_guidedTimer) clearTimeout(_guidedTimer);
  _guidedTimer = setTimeout(() => {
    _guidedStep++;
    _guidedCheckIn();
  }, step.mins * 60 * 1000);
}

function _guidedCheckIn() {
  if (!_guidedModeActive) return;
  const questions = [
    "⏱️ Check-in time! **What is the main idea** you've read so far? Type your answer below.",
    "💡 Quick check: **Name one key concept** from what you just read.",
    "🧠 Reflection: **In your own words**, summarise what you've covered so far.",
  ];
  const q = questions[Math.min(_guidedStep - 1, questions.length - 1)];
  appendBubble('assistant', q);
  chatHistory.push({ role: 'assistant', content: q });

  // Schedule next step only if more remain
  if (_guidedStep < 5) {
    setTimeout(_runGuidedStep, 2000);
  } else {
    _guidedModeActive = false;
    setTimeout(() => {
      appendBubble('assistant',
        "🎓 Excellent study session! You've worked through the material thoroughly.\n\n" +
        'Ready to test yourself? **[Generate a quiz →](ai-generate.html)**'
      );
    }, 3000);
  }
}

// ── Mobile chat toggle (FAB + bottom-sheet) ───────────────────────────────────
let _mobileChatOpen = false;

// CSS open-panel style — setAttribute overwrites the whole inline style attribute,
// so NO CSS rule (including !important) can interfere.
const _CHAT_OPEN_STYLE = [
  'display:flex',
  'position:fixed',
  'bottom:0', 'left:0', 'right:0', 'top:auto',
  'width:100%', 'height:74%',
  'z-index:9999',
  'flex-direction:column',
  'overflow:hidden',
  'border-radius:18px 18px 0 0',
  'box-shadow:0 -8px 40px rgba(0,0,0,0.3)',
  'background:#F0F6FF',
  'animation:bs-chat-slide-up 0.3s cubic-bezier(0.4,0,0.2,1) both',
].join(';');

const _BACKDROP_OPEN_STYLE = [
  'display:block',
  'position:fixed',
  'inset:0',
  'background:rgba(0,0,0,0.5)',
  'z-index:9000',
].join(';');

function _setMobileChatOpen(open) {
  // No-op on desktop — inline styles set here would override desktop CSS
  if (!window.matchMedia('(max-width: 768px)').matches) return;
  _mobileChatOpen = open;
  const chatPanel = document.getElementById('chat-panel');
  const badge     = document.getElementById('mobile-fab-badge');
  const backdrop  = document.getElementById('mobile-chat-backdrop');
  const layout    = document.getElementById('bs-layout');

  if (open) {
    if (chatPanel) chatPanel.setAttribute('style', _CHAT_OPEN_STYLE);
    if (backdrop)  backdrop.setAttribute('style',  _BACKDROP_OPEN_STYLE);
    layout?.classList.add('mobile-chat-active');
    if (badge) badge.classList.remove('visible');
    // Hide greeting bubble when chat opens
    dismissGreeting();
  } else {
    // Instant hide — set display:none inline (overrides any CSS display:flex)
    if (chatPanel) chatPanel.setAttribute('style', 'display:none');
    if (backdrop)  backdrop.setAttribute('style',  'display:none');
    layout?.classList.remove('mobile-chat-active');
  }
}

window.switchMobileTab = function (tab) {
  _setMobileChatOpen(tab === 'chat');
};

window.toggleMobileChat = function () {
  _setMobileChatOpen(!_mobileChatOpen);
};

// ── Greeting bubble (desktop: beside restore tab; mobile: beside FAB) ────────

window.dismissGreeting = function () {
  _fabGreetingDismissed = true;
  const el = document.getElementById('chat-greeting');
  if (el) el.style.display = 'none';
};

// Keep old name as alias so any existing callers still work
window.dismissFabGreeting = window.dismissGreeting;

function _showGreeting(customText) {
  if (_fabGreetingDismissed) return;
  const el   = document.getElementById('chat-greeting');
  const span = document.getElementById('chat-greeting-text');
  if (!el) return;
  if (customText && span) span.innerHTML = customText;
  // Force re-animation
  el.style.display = 'none';
  void el.offsetWidth;
  el.style.display = 'block';
  // Auto-dismiss after 10 s if chat hasn't been opened
  setTimeout(() => {
    const isMobile   = window.matchMedia('(max-width:768px)').matches;
    const chatClosed = isMobile ? !_mobileChatOpen
      : document.getElementById('bs-layout')?.classList.contains('chat-collapsed');
    if (chatClosed) dismissGreeting();
  }, 10000);
};

// Keep old name as alias
function _showFabGreeting(text) { _showGreeting(text); }

// ── Chat panel collapse / restore (desktop) ───────────────────────────────────
window.toggleChatPanel = function () {
  if (window.innerWidth <= 768) {
    toggleMobileChat();
    return;
  }
  // Remove any inline styles left by the mobile toggle so desktop CSS takes over
  document.getElementById('chat-panel')?.removeAttribute('style');
  document.getElementById('mobile-chat-backdrop')?.removeAttribute('style');

  const layout      = document.getElementById('bs-layout');
  const restoreBtn  = document.getElementById('chat-restore-btn');
  const isCollapsed = layout.classList.toggle('chat-collapsed');
  restoreBtn.classList.toggle('visible', isCollapsed);
  if (!isCollapsed) {
    // Chat is opening — dismiss the greeting bubble
    dismissGreeting();
    setTimeout(() => document.getElementById('chat-input')?.focus(), 350);
  }
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
  _setupCompleted = false;
  _setupDuration  = 1800;
  _setupGoal      = '';
  // Reset duration pills to 30 min default
  document.querySelectorAll('.setup-duration-pill').forEach(p => {
    p.classList.toggle('active', parseInt(p.dataset.secs) === 1800);
  });
  document.getElementById('setup-goal-input') && (document.getElementById('setup-goal-input').value = '');
  const _customMins = document.getElementById('setup-custom-mins');
  if (_customMins) _customMins.value = '';
  _showSetupOverlay();
};

async function loadHistory() {
  const list      = document.getElementById('history-list');
  const slotCount = document.getElementById('history-slot-count');
  list.innerHTML = `
    <div class="history-empty">
      <div class="empty-icon">⏳</div>
      <p>Loading sessions…</p>
    </div>`;
  if (slotCount) slotCount.innerHTML = '';
  try {
    const sessions = await api.get('/brainstorm/sessions');
    if (!sessions.length) {
      list.innerHTML = `
        <div class="history-empty">
          <div class="empty-icon">🕐</div>
          <p>No recent sessions yet.<br>Upload a document or paste text to start!</p>
        </div>`;
      if (slotCount) slotCount.innerHTML = '0 of 5 slots used';
      return;
    }
    // Render items as DOM nodes (not HTML strings) so we can attach event refs
    list.innerHTML = '';
    sessions.forEach(s => {
      const el = document.createElement('div');
      el.outerHTML; // dummy — we use insertAdjacentHTML
      list.insertAdjacentHTML('beforeend', renderSessionItem(s));
    });
    const count = sessions.length;
    if (slotCount) {
      slotCount.innerHTML = `<span>${count}</span> of 5 slots used${count === 5 ? ' — oldest auto-removed on new upload' : ''}`;
    }
  } catch (e) {
    list.innerHTML = `
      <div class="history-empty">
        <div class="empty-icon">⚠️</div>
        <p>Failed to load sessions.<br>${escHtml(e.message)}</p>
      </div>`;
  }
}

function renderSessionItem(s) {
  const icon   = fileTypeIcon(s.document?.file_type);
  const date   = formatRelativeDate(s.updated_at);
  const msgs   = s.message_count;
  const title  = escHtml(s.title);
  const active = s.id === currentSessionId ? ' active' : '';
  const size   = s.document?.file_size_bytes
    ? ' &middot; ' + _fmtBytes(s.document.file_size_bytes)
    : '';
  return `
    <div class="history-item${active}" id="hist-item-${s.id}" onclick="openSession('${s.id}')">
      <div class="history-item-icon">${icon}</div>
      <div class="history-item-body">
        <div class="history-item-title" id="hist-title-${s.id}">${title}</div>
        <div class="history-item-meta">${date} &middot; ${msgs} message${msgs !== 1 ? 's' : ''}${size}</div>
      </div>
      <div class="history-item-actions">
        <button class="history-action-btn" title="Rename session"
          onclick="event.stopPropagation();startRenameSession('${s.id}','${title}')">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
        <button class="history-action-btn danger" title="Delete session & all data"
          onclick="event.stopPropagation();confirmDeleteSession('${s.id}')">
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

function _fmtBytes(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function _loadSessionIntoView(sessionId, closeHistoryPanel = true) {
  const session = await api.get(`/brainstorm/sessions/${sessionId}`);

  if (closeHistoryPanel) {
    document.getElementById('doc-history-area').classList.remove('visible');
  }

  if (session.document) {
    documentContext = session.document.extracted_text;

    const scroll   = document.getElementById('doc-viewer-scroll');
    const fileType = session.document.file_type;
    const isTxt    = fileType === 'txt' || fileType === 'paste';

    scroll.style.padding = '0';

    if (isTxt) {
      // Plain text — no PDF viewer, just render the extracted text
      scroll.style.height = '';
      scroll.innerHTML = `<pre class="txt-render">${escHtml(documentContext)}</pre>`;
    } else if (fileType === 'pdf') {
      // PDF: try IDB first (fast local cache), then fall back to backend PDF endpoint
      let buf = await _IDB.get(sessionId).catch(() => null);
      if (!buf && sessionId === localStorage.getItem('pritis_bs_session_id')) {
        buf = await _IDB.get('current_pdf').catch(() => null);
        if (buf) await _IDB.save(sessionId, buf).catch(() => {});
      }
      if (buf) {
        if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
        currentBlobUrl = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }));
        _renderPdfInScroll(scroll, currentBlobUrl, documentContext);
      } else {
        // No IDB cache — use the unified PDF polling path (pdf_ready=true for PDFs, will succeed fast)
        _renderWithPdfPolling(sessionId, scroll, documentContext);
      }
    } else {
      // DOCX, PPTX, images — all converted to PDF by the backend
      _renderWithPdfPolling(sessionId, scroll, documentContext);
    }

    const sizeLabel = session.document.file_size_bytes
      ? `${(session.document.file_size_bytes / 1024).toFixed(1)} KB`
      : `${documentContext.length.toLocaleString()} chars`;

    document.getElementById('doc-meta-bar').innerHTML = `
      <strong>${escHtml(session.document.filename)}</strong>
      <span class="badge-sm">${session.document.file_type.toUpperCase()}</span>
      <span style="margin-left:auto">${sizeLabel}</span>`;
    _setDesktopFileBadge(session.document.filename, session.document.file_type.toUpperCase());

    currentFilename = session.document?.filename || 'Document';
    document.getElementById('doc-upload-area').style.display = 'none';
    document.getElementById('doc-viewer-area').classList.add('visible');
    document.getElementById('doc-controls').innerHTML = _docControlsHtml('Change');
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
  localStorage.setItem('pritis_bs_session_id', sessionId);
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

// Step 1 — show inline confirmation row below the item
window.confirmDeleteSession = function (sessionId) {
  // Remove any other open confirms first
  document.querySelectorAll('.history-delete-confirm').forEach(el => el.remove());

  const item = document.getElementById(`hist-item-${sessionId}`);
  if (!item) return;

  const confirm = document.createElement('div');
  confirm.className = 'history-delete-confirm';
  confirm.id = `hist-confirm-${sessionId}`;
  confirm.innerHTML = `
    <span>Delete this session and all its data?</span>
    <button class="confirm-no"
      onclick="event.stopPropagation();document.getElementById('hist-confirm-${sessionId}')?.remove()">
      Cancel
    </button>
    <button class="confirm-yes"
      onclick="event.stopPropagation();deleteSession('${sessionId}')">
      Delete
    </button>`;
  item.after(confirm);
};

// Step 2 — actually delete after confirmation
window.deleteSession = async function (sessionId) {
  const item    = document.getElementById(`hist-item-${sessionId}`);
  const confirm = document.getElementById(`hist-confirm-${sessionId}`);

  if (item) { item.style.opacity = '0.4'; item.style.pointerEvents = 'none'; }
  if (confirm) confirm.remove();

  try {
    await api.del(`/brainstorm/sessions/${sessionId}`);
    item?.remove();

    if (currentSessionId === sessionId) {
      currentSessionId = null;
      localStorage.removeItem('pritis_bs_session_id');
    }

    // Update slot count
    const list      = document.getElementById('history-list');
    const remaining = list.querySelectorAll('.history-item').length;
    const slotEl    = document.getElementById('history-slot-count');

    if (remaining === 0) {
      list.innerHTML = `
        <div class="history-empty">
          <div class="empty-icon">🕐</div>
          <p>No recent sessions yet.<br>Upload a document or paste text to start!</p>
        </div>`;
      if (slotEl) slotEl.innerHTML = '0 of 5 slots used';
    } else {
      if (slotEl) slotEl.innerHTML = `<span>${remaining}</span> of 5 slots used`;
    }
  } catch (e) {
    if (item) { item.style.opacity = ''; item.style.pointerEvents = ''; }
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
  const icons = { pdf: '📕', docx: '📘', pptx: '📊', txt: '📄', paste: '📝',
                  png: '🖼️', jpg: '🖼️', jpeg: '🖼️', webp: '🖼️', gif: '🖼️' };
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

// Ensure mobile chat is closed on every page load (never auto-open)
_setMobileChatOpen(false);

// Initialise: restore session or show setup overlay + greeting bubble
(async () => {
  await _bsRestore().catch(console.error);
  if (!documentContext) {
    // New / empty session — show setup overlay
    setTimeout(_showSetupOverlay, 120);
  } else {
    // Returning session with a document — show greeting bubble only
    setTimeout(() => _showGreeting(), 600);
  }
})();


// ══════════════════════════════════════════════════════════════════════════════
// ── Ask Your Friends ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════

const _WS_BASE = (() => {
  const isLocal = ['localhost', '127.0.0.1', '0.0.0.0'].includes(window.location.hostname);
  return isLocal ? 'ws://127.0.0.1:8000/api' : 'wss://www.pritis.name.ng/api';
})();

// State
let _friendsOpen        = false;
let _activeConvId       = null;
let _activeOtherUser    = null;
let _friendsWs          = null;
let _friendsConvs       = [];
let _friendsOnline      = false;
let _friendsTypingTimer = null;
let _typingSent         = false;
let _friendsMsgPage     = [];
let _wsReconnectTimer   = null;
let _wsIntentionalClose = false;
let _wsReconnectDelay   = 1500;  // ms; doubles on each failure, capped at 30 s

// ── DOM helper ────────────────────────────────────────────────────────────────
function _friendsEl(id) { return document.getElementById(id); }

function _friendsAvatar(user, size) {
  const sz = (size || 36) + 'px';
  const fs = Math.round((size || 36) * 0.33) + 'px';
  if (user && user.profile_picture_url) {
    return '<div class="friends-user-avatar" style="width:' + sz + ';height:' + sz + '">' +
           '<img src="' + escHtml(user.profile_picture_url) + '" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:50%"></div>';
  }
  const initials = ((user && (user.full_name || user.username)) || '?').charAt(0).toUpperCase();
  return '<div class="friends-user-avatar" style="width:' + sz + ';height:' + sz + ';font-size:' + fs + '">' + escHtml(initials) + '</div>';
}

function _fmtMsgTime(iso) {
  const d = new Date(iso), now = new Date();
  if (d.toDateString() === now.toDateString())
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
         d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function _fmtConvTime(iso) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date();
  if (d.toDateString() === now.toDateString())
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const diff = Math.floor((now - d) / 86400000);
  if (diff < 7) return d.toLocaleDateString([], { weekday: 'short' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// ── Open / close ──────────────────────────────────────────────────────────────

window.openFriendsModal = async function () {
  const user = getUser();
  if (!user || !user.profile_completed) {
    _showFriendsLockState();
    _friendsEl('friends-modal').classList.add('open');
    _friendsOpen = true;
    return;
  }
  _friendsEl('friends-modal').classList.add('open');
  _friendsOpen = true;
  await _loadConversations();
};

window.closeFriendsModal = function () {
  _friendsEl('friends-modal').classList.remove('open');
  _friendsOpen = false;
  _disconnectFriendsWs();
};

window.onFriendsModalBackdrop = function (e) {
  if (e.target === _friendsEl('friends-modal')) closeFriendsModal();
};

// ── Profile lock ──────────────────────────────────────────────────────────────

function _showFriendsLockState() {
  var listPanel = _friendsEl('friends-list-panel');
  var chatPanel = _friendsEl('friends-chat-panel');
  listPanel.innerHTML =
    '<div class="friends-modal-head"><h2>Ask Your Friends</h2>' +
    '<button class="friends-modal-close" onclick="closeFriendsModal()">×</button></div>' +
    '<div class="friends-lock-state">' +
    '<div class="friends-lock-icon">🔒</div>' +
    '<div class="friends-lock-title">Profile Incomplete</div>' +
    '<div class="friends-lock-desc">Complete your profile and set a username so classmates can find you!</div>' +
    '<a href="settings.html?redirect=' + encodeURIComponent(window.location.pathname) + '&setup=1"' +
    ' class="btn btn-primary btn-sm" style="text-decoration:none;margin-top:4px">Complete Profile →</a>' +
    '</div>';
  chatPanel.style.display = 'none';
}

// ── Conversations list ────────────────────────────────────────────────────────

async function _loadConversations() {
  var listEl = _friendsEl('friends-conv-list');
  try {
    _friendsConvs = await api.get('/friends/conversations');
    _renderConvList();
  } catch (err) {
    listEl.innerHTML = '<div class="friends-empty-state"><p style="color:var(--danger)">' + escHtml(err.message) + '</p></div>';
  }
}

function _renderConvList() {
  var listEl = _friendsEl('friends-conv-list');
  if (!_friendsConvs.length) {
    listEl.innerHTML = '<div class="friends-empty-state"><div class="fe-icon">💬</div><p>No conversations yet.<br>Search for a friend above!</p></div>';
    return;
  }
  var html = '<div class="friends-conv-section-label">Recent</div>';
  for (var i = 0; i < _friendsConvs.length; i++) {
    var conv = _friendsConvs[i];
    var other = conv.other_user;
    var latest = conv.latest_message;
    var unread = conv.unread_count || 0;
    var isActive = conv.id === _activeConvId;
    var preview = latest
      ? escHtml(latest.content.slice(0, 55) + (latest.content.length > 55 ? '…' : ''))
      : '<em style="color:var(--text-muted)">No messages yet</em>';
    var convData = escHtml(JSON.stringify(conv)).replace(/&quot;/g, '"');
    html += '<div class="friends-conv-item' + (isActive ? ' active' : '') + '"' +
      ' id="friends-conv-item-' + conv.id + '"' +
      ' onclick=\'openConversation(' + JSON.stringify(conv).replace(/'/g, "\\'") + ')\'>' +
      '<div class="friends-conv-avatar-wrap">' + _friendsAvatar(other, 38) +
      '<span class="friends-conv-online-dot" id="friends-online-dot-' + conv.id + '" style="display:none"></span></div>' +
      '<div class="friends-conv-body">' +
      '<div class="friends-conv-name">' + escHtml(other.full_name) + '</div>' +
      '<div class="friends-conv-preview">' + preview + '</div></div>' +
      '<div class="friends-conv-meta">' +
      '<span class="friends-conv-time">' + _fmtConvTime((latest && latest.created_at) || conv.created_at) + '</span>' +
      (unread ? '<span class="friends-unread-badge">' + unread + '</span>' : '') +
      '</div></div>';
  }
  listEl.innerHTML = html;
}

function _updateFriendsUnreadDot() {
  var total = _friendsConvs.reduce(function(s, c) { return s + (c.unread_count || 0); }, 0);
  var dot = _friendsEl('ask-friends-unread-dot');
  if (dot) dot.classList.toggle('visible', total > 0);
}

// ── Search ────────────────────────────────────────────────────────────────────

window.searchFriend = async function () {
  var input    = _friendsEl('friends-search-input');
  var resultEl = _friendsEl('friends-search-result');
  var btn      = _friendsEl('friends-search-btn');
  var username = (input ? input.value : '').trim();
  if (!username) return;
  btn.disabled = true; btn.textContent = '…';
  resultEl.innerHTML = '';
  try {
    var user = await api.get('/friends/search?username=' + encodeURIComponent(username));
    var schoolMeta = user.school ? ' · ' + escHtml(user.school) : '';
    resultEl.innerHTML =
      '<div class="friends-user-card" onclick=\'startConversationWith(' + JSON.stringify(user).replace(/'/g, "\\'") + ')\'>' +
      _friendsAvatar(user, 36) +
      '<div class="friends-user-info">' +
      '<div class="friends-user-name">' + escHtml(user.full_name) + '</div>' +
      '<div class="friends-user-meta">@' + escHtml(user.username) + schoolMeta + '</div></div>' +
      '<button class="friends-start-btn">Chat →</button></div>';
  } catch (err) {
    resultEl.innerHTML = '<div class="friends-search-error">' + escHtml(err.message) + '</div>';
  } finally {
    btn.disabled = false; btn.textContent = 'Find';
  }
};

window.startConversationWith = async function (user) {
  var resultEl = _friendsEl('friends-search-result');
  resultEl.innerHTML = '<div style="font-size:0.78rem;color:var(--text-muted);padding:4px 2px">Starting conversation…</div>';
  try {
    var conv = await api.post('/friends/conversations', { username: user.username });
    var exists = _friendsConvs.find(function(c) { return c.id === conv.id; });
    if (!exists) _friendsConvs.unshift(conv);
    _renderConvList();
    resultEl.innerHTML = '';
    var inp = _friendsEl('friends-search-input');
    if (inp) inp.value = '';
    openConversation(conv);
  } catch (err) {
    resultEl.innerHTML = '<div class="friends-search-error">' + escHtml(err.message) + '</div>';
  }
};

// ── Open conversation ─────────────────────────────────────────────────────────

window.openConversation = async function (conv) {
  _activeConvId    = conv.id;
  _activeOtherUser = conv.other_user;
  _renderConvList();

  _friendsEl('friends-no-chat').style.display = 'none';
  var activeChat = _friendsEl('friends-active-chat');
  activeChat.style.display = 'flex';

  // Header avatar
  var avatarEl = _friendsEl('friends-chat-avatar');
  if (_activeOtherUser.profile_picture_url) {
    avatarEl.innerHTML = '<img src="' + escHtml(_activeOtherUser.profile_picture_url) + '" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:50%">';
    avatarEl.style.background = 'transparent';
  } else {
    avatarEl.textContent = (_activeOtherUser.full_name || '?').charAt(0).toUpperCase();
    avatarEl.style.background = 'var(--primary)';
    avatarEl.style.color = '#fff';
  }
  _friendsEl('friends-chat-user-name').textContent = _activeOtherUser.full_name;
  _setFriendsOnlineStatus(false);

  // Mobile: swap panels
  if (window.innerWidth <= 600) {
    _friendsEl('friends-list-panel').classList.add('hidden');
    _friendsEl('friends-chat-panel').classList.add('panel-active');
  }

  // Load messages
  var msgsEl = _friendsEl('friends-chat-messages');
  msgsEl.innerHTML = '<div style="text-align:center;font-size:0.78rem;color:var(--text-muted);padding:20px">Loading…</div>';
  try {
    var msgs = await api.get('/friends/conversations/' + conv.id + '/messages?limit=50');
    _friendsMsgPage = msgs;
    _renderFriendsMsgs(msgs);
    _scrollFriendsChat();
  } catch (e) {
    msgsEl.innerHTML = '<div style="text-align:center;font-size:0.78rem;color:var(--danger);padding:20px">Failed to load messages</div>';
  }

  // Mark read
  api.patch('/friends/conversations/' + conv.id + '/read').catch(function() {});
  var c = _friendsConvs.find(function(x) { return x.id === conv.id; });
  if (c) { c.unread_count = 0; _renderConvList(); }
  _updateFriendsUnreadDot();

  _connectFriendsWs(conv.id);
  setTimeout(function() { var inp = _friendsEl('friends-chat-input'); if (inp) inp.focus(); }, 80);
};

window.backToFriendsList = function () {
  _disconnectFriendsWs();
  _activeConvId = null; _activeOtherUser = null;
  _friendsEl('friends-no-chat').style.display = 'flex';
  _friendsEl('friends-active-chat').style.display = 'none';
  if (window.innerWidth <= 600) {
    _friendsEl('friends-list-panel').classList.remove('hidden');
    _friendsEl('friends-chat-panel').classList.remove('panel-active');
  }
};

// ── Render messages ───────────────────────────────────────────────────────────

function _renderFriendsMsgs(msgs) {
  var msgsEl = _friendsEl('friends-chat-messages');
  var me = getUser();
  if (!msgs.length) {
    msgsEl.innerHTML = '<div style="text-align:center;font-size:0.78rem;color:var(--text-muted);padding:30px 10px">No messages yet. Say hello! 👋</div>';
    return;
  }
  var html = '', lastDate = '';
  for (var i = 0; i < msgs.length; i++) {
    var msg = msgs[i];
    var isMe = String(msg.sender_id) === String(me && me.id);
    var dStr = new Date(msg.created_at).toDateString();
    if (dStr !== lastDate) {
      lastDate = dStr;
      var label = dStr === new Date().toDateString() ? 'Today'
        : new Date(msg.created_at).toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
      html += '<div class="friends-msg-date-sep">' + label + '</div>';
    }
    html += _buildMsgHtml(msg, isMe);
  }
  msgsEl.innerHTML = html;
}

function _buildMsgHtml(msg, isMe) {
  var tick = isMe ? '<span class="friends-msg-read-tick" title="' + (msg.is_read ? 'Read' : 'Sent') + '">' + (msg.is_read ? '✓✓' : '✓') + '</span>' : '';
  return '<div class="friends-msg ' + (isMe ? 'me' : 'them') + '" id="friends-msg-' + msg.id + '">' +
    '<div class="friends-msg-bubble">' + escHtml(msg.content) + '</div>' +
    '<div class="friends-msg-time">' + _fmtMsgTime(msg.created_at) + ' ' + tick + '</div></div>';
}

function _appendFriendsMsg(msg) {
  var msgsEl = _friendsEl('friends-chat-messages');
  if (!msgsEl) return;
  var me = getUser();
  var isMe = String(msg.sender_id) === String(me && me.id);
  var ph = msgsEl.querySelector('div[style*="padding:30px"]');
  if (ph) ph.remove();
  var tmp = document.createElement('div');
  tmp.innerHTML = _buildMsgHtml(msg, isMe);
  msgsEl.appendChild(tmp.firstElementChild);
  _scrollFriendsChat();
}

function _scrollFriendsChat() {
  var el = _friendsEl('friends-chat-messages');
  if (el) el.scrollTop = el.scrollHeight;
}

function _markFriendsMsgsRead() {
  var me = getUser();
  for (var i = 0; i < _friendsMsgPage.length; i++) {
    var m = _friendsMsgPage[i];
    if (String(m.sender_id) === String(me && me.id) && !m.is_read) {
      var el = document.getElementById('friends-msg-' + m.id);
      if (el) {
        var tick = el.querySelector('.friends-msg-read-tick');
        if (tick) { tick.textContent = '✓✓'; tick.title = 'Read'; }
        m.is_read = true;
      }
    }
  }
}

// ── Online status ─────────────────────────────────────────────────────────────

function _setFriendsOnlineStatus(online) {
  _friendsOnline = online;
  var dot   = _friendsEl('friends-status-dot');
  var label = _friendsEl('friends-status-label');
  if (dot)   dot.className  = online ? 'online-dot'  : 'offline-dot';
  if (label) label.textContent = online ? 'Online' : 'Offline';
  if (_activeConvId) {
    var d = _friendsEl('friends-online-dot-' + _activeConvId);
    if (d) d.style.display = online ? 'block' : 'none';
  }
}

// ── Typing indicator ──────────────────────────────────────────────────────────

window.sendFriendsTyping = function () {
  if (!_friendsWs || _friendsWs.readyState !== WebSocket.OPEN) return;
  if (!_typingSent) {
    _friendsWs.send(JSON.stringify({ type: 'typing', is_typing: true }));
    _typingSent = true;
  }
  clearTimeout(_friendsTypingTimer);
  _friendsTypingTimer = setTimeout(function() {
    if (_friendsWs && _friendsWs.readyState === WebSocket.OPEN)
      _friendsWs.send(JSON.stringify({ type: 'typing', is_typing: false }));
    _typingSent = false;
  }, 2000);
};

// ── WebSocket ─────────────────────────────────────────────────────────────────

function _connectFriendsWs(convId) {
  _disconnectFriendsWs();
  _wsIntentionalClose = false;
  _wsReconnectDelay   = 1500;

  function _doConnect() {
    var token = getToken();
    if (!token || _wsIntentionalClose || _activeConvId !== convId) return;

    var url = _WS_BASE + '/friends/ws/' + convId + '?token=' + encodeURIComponent(token);
    try { _friendsWs = new WebSocket(url); } catch (e) { _scheduleReconnect(); return; }

    _friendsWs.addEventListener('open', function () {
      _wsReconnectDelay = 1500; // reset backoff on successful connect
      _friendsWs.send(JSON.stringify({ type: 'read' }));
      _setFriendsWsStatus(true);
    });

    _friendsWs.addEventListener('message', function (e) {
      var data;
      try { data = JSON.parse(e.data); } catch (_) { return; }
      var me = getUser();

      if (data.type === 'message') {
        if (String(data.sender_id) === String(me && me.id)) {
          // Server echo for our own sent message — replace the optimistic temp bubble
          _replaceTempMsg(data);
        } else {
          // Incoming message from the other person
          _appendFriendsMsg(data);
          // Immediately send read receipt over WS
          if (_friendsWs && _friendsWs.readyState === WebSocket.OPEN)
            _friendsWs.send(JSON.stringify({ type: 'read' }));
          api.patch('/friends/conversations/' + convId + '/read').catch(function () {});
        }
        var c = _friendsConvs.find(function (x) { return x.id === convId; });
        if (c) { c.latest_message = data; _renderConvList(); }
      }

      if (data.type === 'presence') {
        if (_activeOtherUser && String(data.user_id) === String(_activeOtherUser.id))
          _setFriendsOnlineStatus(data.online);
      }

      if (data.type === 'typing') {
        if (_activeOtherUser && String(data.user_id) === String(_activeOtherUser.id)) {
          var el = _friendsEl('friends-typing-indicator');
          if (el) el.classList.toggle('visible', data.is_typing);
        }
      }

      if (data.type === 'read_receipt') { _markFriendsMsgsRead(); }
    });

    _friendsWs.addEventListener('close', function () {
      _setFriendsOnlineStatus(false);
      _setFriendsWsStatus(false);
      if (!_wsIntentionalClose && _activeConvId === convId) _scheduleReconnect();
    });

    _friendsWs.addEventListener('error', function () {
      // 'close' fires right after 'error' — reconnect is handled there
    });
  }

  function _scheduleReconnect() {
    clearTimeout(_wsReconnectTimer);
    _wsReconnectTimer = setTimeout(function () {
      if (!_wsIntentionalClose && _activeConvId === convId) _doConnect();
    }, _wsReconnectDelay);
    _wsReconnectDelay = Math.min(_wsReconnectDelay * 2, 30000); // exponential back-off, max 30 s
  }

  _doConnect();
}

function _disconnectFriendsWs() {
  _wsIntentionalClose = true;
  clearTimeout(_wsReconnectTimer);
  if (_friendsWs) { try { _friendsWs.close(); } catch (_) {} _friendsWs = null; }
}

// Replace the last optimistic temp bubble with the confirmed server message
function _replaceTempMsg(realMsg) {
  var msgsEl = _friendsEl('friends-chat-messages');
  if (msgsEl) {
    var temps = Array.from(msgsEl.querySelectorAll('.friends-msg.me[id^="friends-msg-tmp-"]'));
    if (temps.length) {
      // Replace the most recent temp element's id so read-ticks work
      temps[temps.length - 1].id = 'friends-msg-' + realMsg.id;
    }
  }
  // Sync _friendsMsgPage: replace last temp entry with real message
  for (var i = _friendsMsgPage.length - 1; i >= 0; i--) {
    if (String(_friendsMsgPage[i].id).startsWith('tmp-')) {
      _friendsMsgPage[i] = realMsg;
      break;
    }
  }
}

// Show a subtle reconnecting hint in the chat header status area
function _setFriendsWsStatus(connected) {
  var label = _friendsEl('friends-status-label');
  if (!label) return;
  if (!connected && _friendsOnline) {
    // Other user was online but WS dropped — keep Online badge but add hint
    label.textContent = 'Online · reconnecting…';
  } else if (!connected && !_friendsOnline) {
    label.textContent = 'Offline';
  }
  // When reconnected, presence events from the server will restore the correct status
}

// ── Send message ──────────────────────────────────────────────────────────────

window.sendFriendMessage = function () {
  var input   = _friendsEl('friends-chat-input');
  var content = input ? input.value.trim() : '';
  if (!content || !_activeConvId) return;

  // Clear input immediately so the user can type the next message
  if (input) { input.value = ''; input.style.height = ''; }
  clearTimeout(_friendsTypingTimer);
  _typingSent = false;

  var convId = _activeConvId;
  var me = getUser();

  if (_friendsWs && _friendsWs.readyState === WebSocket.OPEN) {
    // ── WebSocket path ──
    // Show optimistic bubble immediately; server echo will replace it with the real ID
    var tmp = {
      id: 'tmp-' + Date.now(),
      sender_id: me && me.id,
      content: content,
      is_read: false,
      created_at: new Date().toISOString(),
    };
    _appendFriendsMsg(tmp);
    _friendsMsgPage.push(tmp);
    var c = _friendsConvs.find(function (x) { return x.id === convId; });
    if (c) { c.latest_message = tmp; _renderConvList(); }

    // Send over WS — server will persist and echo back the real message
    _friendsWs.send(JSON.stringify({ type: 'message', content: content }));
    // Stop typing indicator
    _friendsWs.send(JSON.stringify({ type: 'typing', is_typing: false }));
  } else {
    // ── REST fallback (WS unavailable / reconnecting) ──
    api.post('/friends/conversations/' + convId + '/messages', { content: content })
      .then(function (msg) {
        _appendFriendsMsg(msg);
        _friendsMsgPage.push(msg);
        var c2 = _friendsConvs.find(function (x) { return x.id === convId; });
        if (c2) { c2.latest_message = msg; _renderConvList(); }
      })
      .catch(function (err) {
        var msgsEl = _friendsEl('friends-chat-messages');
        if (msgsEl) {
          var d = document.createElement('div');
          d.style.cssText = 'text-align:center;font-size:0.75rem;color:var(--danger);padding:6px';
          d.textContent = 'Failed to send: ' + err.message;
          msgsEl.appendChild(d);
          msgsEl.scrollTop = msgsEl.scrollHeight;
        }
      });
  }
};

window.onFriendsChatKey = function (e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendFriendMessage(); }
};

window.autoResizeFriendsInput = function (el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 100) + 'px';
};

// ── Background polling for unread counts ──────────────────────────────────────

var _friendsPollTimer = null;

async function _pollFriends() {
  try {
    var user = getUser();
    if (!user || !user.profile_completed) return;
    var convs = await api.get('/friends/conversations');
    _friendsConvs = convs;
    _updateFriendsUnreadDot();
    if (_friendsOpen) _renderConvList();
  } catch (e) {}
}

function _startFriendsPoll() {
  clearInterval(_friendsPollTimer);
  _friendsPollTimer = setInterval(_pollFriends, _friendsOpen ? 10000 : 30000);
}

// Kick off after 3 s so it doesn't compete with page init
setTimeout(function() {
  var user = getUser();
  if (user && user.profile_completed) { _pollFriends(); _startFriendsPoll(); }
}, 3000);
