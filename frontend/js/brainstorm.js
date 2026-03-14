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
let currentFilename  = 'Document'; // used for summary PDF filename

function _docControlsHtml(changeLabel = 'Change') {
  return `
    <button class="btn btn-outline btn-sm" id="summary-btn" onclick="generateSummary()"
      style="display:flex;align-items:center;gap:5px;padding:5px 10px;font-size:0.78rem;font-weight:600">
      📄 Summary
    </button>
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
  if (layout?.classList.contains('chat-collapsed')) return;
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
    }
  }

  document.getElementById('doc-upload-area').style.display = 'none';
  document.getElementById('doc-viewer-area').classList.add('visible');
  document.getElementById('doc-controls').innerHTML = _docControlsHtml('Change');

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
  document.getElementById('doc-controls').innerHTML = _docControlsHtml('Change');

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

function _renderPdfInScroll(scroll, blobUrl, extractedText) {
  scroll.style.padding = '0';
  if (!_isMobile()) {
    // Desktop: native iframe PDF viewer
    scroll.style.height = '100%';
    scroll.innerHTML = `<iframe src="${blobUrl}#toolbar=1&navpanes=0"
      title="Document viewer" style="width:100%;height:100%;border:none;display:block;"></iframe>`;
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
    input.focus();
    _startReadingTimers(); // begin proactive engagement countdown
  }
}

function disableChat() {
  const input = document.getElementById('chat-input');
  input.disabled    = true;
  input.placeholder = 'Upload a document to start chatting with UrPadi…';
  document.getElementById('send-btn').disabled = true;
  _clearReadingTimers(); // stop timers when no document is active
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
    _resetReadingTimers(); // user interacted — restart the inactivity clock
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
    <div class="msg-avatar">${role === 'user' ? _initials : 'UP'}</div>
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

// ── Chat panel collapse / restore ────────────────────────────────────────────
window.toggleChatPanel = function () {
  const layout      = document.getElementById('bs-layout');
  const restoreBtn  = document.getElementById('chat-restore-btn');
  const isCollapsed = layout.classList.toggle('chat-collapsed');
  restoreBtn.classList.toggle('visible', isCollapsed);
  if (!isCollapsed) {
    // Focus chat input when panel opens
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

window.deleteSession = async function (sessionId, btn) {
  const item = btn.closest('.history-item');
  item.style.opacity = '0.4';
  item.style.pointerEvents = 'none';
  try {
    await api.del(`/brainstorm/sessions/${sessionId}`);
    item.remove();
    if (currentSessionId === sessionId) {
      currentSessionId = null;
      localStorage.removeItem('pritis_bs_session_id');
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
_bsRestore().catch(console.error);
