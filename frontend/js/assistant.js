/**
 * Pritis AI — Floating Assistant Widget
 * Injected globally via layout.js on every authenticated page.
 * Hidden on: Study Zone (brainstorm.html), active quiz sessions (quiz-practice.html).
 */

import { api, getToken } from './api.js';

/* ── State ────────────────────────────────────────────────────────────────────── */
const _s = {
  open:          false,
  history:       [],       // {role:'user'|'assistant', content:string}[]
  pendingFile:   null,     // File | null
  recording:     false,
  mediaRecorder: null,
  chunks:        [],
  bubbleIdx:     0,
  bubbleTimer:   null,
  idleTimer:     null,
  // Support mode
  supportMode:   false,
  supportConvId: null,
  supportWs:     null,
  supportStatus: null,    // 'ai' | 'waiting' | 'human' | 'closed'
};

const _isLocal = ['localhost', '127.0.0.1', '0.0.0.0'].includes(window.location.hostname);
const _WS_BASE = _isLocal ? 'ws://127.0.0.1:8000/api' : 'wss://www.pritis.name.ng/api';

const _BUBBLES = [
  "Need help with anything? 💬",
  "I can explain any topic for you 📚",
  "Ask me anything about your studies ✏️",
  "Upload a document and I'll analyse it 📄",
  "Struggling with a concept? Just ask!",
  "I'm here whenever you need me 😊",
];

const _PAGE_GREETINGS = {
  'dashboard.html':       "Hi! I'm Pritis AI. What would you like to study today? 🎯",
  'ai-generate.html':     "Hi! I can explain your generated questions or any topic. What do you need? 📝",
  'question-bank.html':   "Hi! Need help understanding any of your quiz questions? 💡",
  'performance.html':     "Hi! Want me to help analyse your performance or suggest improvements? 📊",
  'input-questions.html': "Hi! I can help you craft better questions or explain any topic. ✏️",
  'study-strategy.html':  "Hi! I can help you build a study plan or explain your notes. 📖",
  'settings.html':        "Hi! Need help setting up your profile or anything else? 😊",
  'upgrade.html':         "Hi! Got questions about the Premium plan? I can help! ⭐",
};

/* ── Visibility helpers ───────────────────────────────────────────────────────── */
function _shouldShow() {
  return !window.location.pathname.includes('brainstorm.html');
}

function _isQuizActive() {
  const quiz    = document.getElementById('quiz-screen');
  const results = document.getElementById('results-screen');
  if (!quiz) return false;
  const qVisible = !quiz.classList.contains('hidden') && quiz.style.display !== 'none';
  const rHidden  = !results || results.classList.contains('hidden') || results.style.display === 'none';
  return qVisible && rHidden;
}

/* ── Public entry point (called from layout.js) ───────────────────────────────── */
export function initAssistant() {
  if (!_shouldShow()) return;
  if (document.getElementById('pa-wrap')) return;   // already mounted

  _injectStyles();
  _injectDOM();
  _bindEvents();

  // Quiz-practice: watch DOM for quiz starting/ending
  if (window.location.pathname.includes('quiz-practice.html')) {
    _watchQuizState();
  }

  // Show idle bubble after 7 seconds
  _s.bubbleTimer = setTimeout(_showBubble, 7000);
}

/* ── Styles ────────────────────────────────────────────────────────────────────── */
function _injectStyles() {
  if (document.getElementById('pa-styles')) return;
  const s = document.createElement('style');
  s.id = 'pa-styles';
  s.textContent = `
/* ── Wrapper (button + bubble only) ── */
#pa-wrap {
  position: fixed; bottom: 24px; right: 24px;
  z-index: 8901;
  display: flex; flex-direction: column; align-items: flex-end;
  gap: 10px; pointer-events: none;
}

/* ── Idle bubble ── */
#pa-bubble {
  background: var(--bg-card, #fff);
  border: 1.5px solid var(--border, #e2e8f0);
  border-radius: 14px 14px 4px 14px;
  padding: 9px 10px 9px 14px;
  font-size: 0.81rem; line-height: 1.4;
  color: var(--text, #0f172a);
  max-width: 224px;
  box-shadow: 0 4px 18px rgba(0,0,0,.11);
  pointer-events: all; cursor: pointer;
  display: flex; align-items: center; gap: 8px;
  opacity: 0; transform: translateY(10px) scale(.94);
  transition: opacity .3s ease, transform .3s ease;
  user-select: none;
}
#pa-bubble.pa-vis { opacity: 1; transform: translateY(0) scale(1); }
#pa-bdismiss {
  flex-shrink: 0; background: none; border: none;
  font-size: 1rem; line-height: 1;
  color: var(--text-muted, #94a3b8); cursor: pointer;
  padding: 0 3px; border-radius: 4px;
  transition: color .15s;
}
#pa-bdismiss:hover { color: var(--text, #0f172a); }

/* ── Floating button ── */
#pa-btn {
  width: 56px; height: 56px; border-radius: 50%;
  background: var(--primary, #0077ff);
  border: none; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 22px rgba(0,119,255,.45);
  transition: transform .22s cubic-bezier(.34,1.56,.64,1), box-shadow .2s;
  pointer-events: all; color: #fff; position: relative; overflow: hidden;
}
#pa-btn:hover { transform: scale(1.09); box-shadow: 0 6px 28px rgba(0,119,255,.55); }
#pa-btn svg { position: absolute; transition: opacity .2s, transform .25s cubic-bezier(.34,1.56,.64,1); }
.pa-ico-chat  { opacity: 1;  transform: scale(1)   rotate(0deg); }
.pa-ico-close { opacity: 0;  transform: scale(.6)  rotate(-90deg); }
#pa-btn.pa-open .pa-ico-chat  { opacity: 0;  transform: scale(.6)  rotate(90deg); }
#pa-btn.pa-open .pa-ico-close { opacity: 1;  transform: scale(1)   rotate(0deg); }
#pa-dot {
  position: absolute; top: 4px; right: 4px;
  width: 11px; height: 11px; border-radius: 50%;
  background: #ff4136; border: 2px solid #fff; display: none;
}

/* ── Panel — fixed independently from wrap ── */
#pa-panel {
  position: fixed;
  bottom: 90px;           /* 56px btn + 10px gap + 24px base */
  right: 24px;
  width: 360px;
  max-height: calc(100vh - 106px);   /* 90px bottom + 16px top clearance */
  background: var(--bg-card, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 18px;
  box-shadow: 0 12px 48px rgba(0,0,0,.16);
  display: flex; flex-direction: column; overflow: hidden;
  pointer-events: none;
  transform: scale(.88) translateY(20px);
  transform-origin: bottom right;
  opacity: 0;
  z-index: 8900;
  transition: transform .35s cubic-bezier(.34,1.25,.64,1), opacity .25s ease;
}
#pa-panel.pa-open { transform: scale(1) translateY(0); opacity: 1; pointer-events: all; }

/* Fullscreen mode */
#pa-panel.pa-fs {
  top: 16px !important; bottom: 16px !important;
  left: 16px !important; right: 16px !important;
  width: auto !important; max-height: none !important;
  border-radius: 18px !important;
  z-index: 9100;
  transform: none !important;
}
body.pa-fs-active #pa-wrap { pointer-events: none; }
body.pa-fs-active #pa-panel { pointer-events: all; }

/* Panel header */
#pa-head {
  background: linear-gradient(135deg,#0077ff 0%,#005acc 100%);
  padding: 12px 12px 12px 14px;
  display: flex; align-items: center; gap: 10px; flex-shrink: 0;
}
#pa-av {
  width: 36px; height: 36px; border-radius: 50%;
  background: rgba(255,255,255,.22);
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 1rem; font-weight: 800; flex-shrink: 0;
}
#pa-info { flex: 1; min-width: 0; }
#pa-name  { font-size: .87rem; font-weight: 700; color: #fff; }
#pa-stat  { font-size: .7rem; color: rgba(255,255,255,.75); display: flex; align-items: center; gap: 5px; margin-top: 1px; }
.pa-sdot  { width: 6px; height: 6px; border-radius: 50%; background: #4ade80; animation: pa-pulse 2s infinite; }
@keyframes pa-pulse { 0%,100%{opacity:1} 50%{opacity:.45} }
.pa-hbtn {
  width: 30px; height: 30px; border-radius: 50%;
  background: rgba(255,255,255,.15); border: none; color: #fff;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; flex-shrink: 0;
  transition: background .15s;
}
.pa-hbtn:hover { background: rgba(255,255,255,.28); }
#pa-expand svg { transition: transform .25s ease; }
#pa-panel.pa-fs #pa-expand svg { transform: rotate(180deg); }

/* Messages scroll area */
#pa-msgs {
  flex: 1; overflow-y: auto; padding: 14px 12px 6px;
  display: flex; flex-direction: column; gap: 10px;
  scroll-behavior: smooth;
}
#pa-msgs::-webkit-scrollbar { width: 4px; }
#pa-msgs::-webkit-scrollbar-thumb { background: var(--border, #e2e8f0); border-radius: 4px; }

/* Bubbles */
.pa-row { display: flex; gap: 7px; align-items: flex-end; }
.pa-row.pa-u { flex-direction: row-reverse; }
.pa-bbl {
  max-width: 80%; padding: 9px 12px; font-size: .82rem;
  line-height: 1.55; word-break: break-word; border-radius: 16px;
}
.pa-row.pa-ai .pa-bbl { background: var(--bg, #f1f5f9); color: var(--text,#0f172a); border-bottom-left-radius: 4px; }
.pa-row.pa-u  .pa-bbl { background: var(--primary,#0077ff); color: #fff; border-bottom-right-radius: 4px; }
.pa-rav {
  width: 26px; height: 26px; border-radius: 50%;
  background: linear-gradient(135deg,#0077ff,#005acc);
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: .68rem; font-weight: 800; flex-shrink: 0;
}

/* Typing dots */
#pa-typing { display: none; align-items: flex-end; gap: 7px; padding: 0 2px; }
#pa-typing.pa-show { display: flex; }
.pa-tdots { background: var(--bg,#f1f5f9); padding: 10px 14px; border-radius: 16px; border-bottom-left-radius: 4px; display: flex; gap: 4px; align-items: center; }
.pa-tdots span { width: 6px; height: 6px; background: var(--text-muted,#94a3b8); border-radius: 50%; animation: pa-bounce 1.2s infinite ease-in-out; }
.pa-tdots span:nth-child(1){animation-delay:0s}
.pa-tdots span:nth-child(2){animation-delay:.2s}
.pa-tdots span:nth-child(3){animation-delay:.4s}
@keyframes pa-bounce { 0%,60%,100%{transform:translateY(0)} 30%{transform:translateY(-6px)} }

/* File preview */
#pa-fprev {
  display: none; background: rgba(0,119,255,.06);
  border-top: 1px solid var(--border,#e2e8f0);
  padding: 7px 13px; align-items: center; gap: 8px;
  font-size: .77rem; color: var(--text,#0f172a); flex-shrink: 0;
}
#pa-fprev.pa-show { display: flex; }
#pa-fprev svg { flex-shrink: 0; color: var(--primary,#0077ff); }
#pa-fname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#pa-fremove {
  background: none; border: none; cursor: pointer;
  color: var(--text-muted,#94a3b8); font-size: 1rem; line-height: 1;
  padding: 0 3px; border-radius: 4px; transition: color .15s; flex-shrink: 0;
}
#pa-fremove:hover { color: #ef4444; }

/* Input row */
#pa-bar {
  padding: 9px 11px 12px; border-top: 1px solid var(--border,#e2e8f0);
  display: flex; align-items: flex-end; gap: 7px; flex-shrink: 0;
}
.pa-ibtn {
  width: 36px; height: 36px; border-radius: 50%; border: none;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; flex-shrink: 0;
  transition: background .15s, color .15s;
}
#pa-attach, #pa-voice { background: var(--bg,#f1f5f9); color: var(--text-muted,#94a3b8); }
#pa-attach:hover, #pa-voice:hover { background: rgba(0,119,255,.09); color: var(--primary,#0077ff); }
#pa-voice.pa-rec { background: #fef2f2; color: #ef4444; animation: pa-recpulse 1s infinite; }
@keyframes pa-recpulse { 0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.3)} 50%{box-shadow:0 0 0 6px rgba(239,68,68,0)} }
#pa-send { background: var(--primary,#0077ff); color: #fff; }
#pa-send:hover { background: #005fcc; }
#pa-send:disabled { opacity: .5; cursor: default; }
#pa-ta {
  flex: 1; background: var(--bg,#f1f5f9);
  border: 1.5px solid transparent; border-radius: 18px;
  padding: 8px 13px; font-size: .84rem; font-family: inherit;
  color: var(--text,#0f172a); resize: none; outline: none;
  min-height: 36px; max-height: 120px; line-height: 1.5; overflow-y: auto;
  transition: border-color .15s;
}
#pa-ta:focus { border-color: var(--primary,#0077ff); }
#pa-ta::placeholder { color: var(--text-muted,#94a3b8); }

/* Support mode overrides */
#pa-panel.pa-support #pa-head { background: linear-gradient(135deg,#7c3aed 0%,#5b21b6 100%); }
#pa-support-banner {
  display: none; align-items: center; gap: 8px;
  padding: 7px 14px; font-size: .76rem; font-weight: 600;
  background: #f5f3ff; border-bottom: 1px solid #e9d5ff;
  color: #5b21b6; flex-shrink: 0;
}
#pa-support-banner.pa-show { display: flex; }
.pa-support-dot {
  width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
  background: #f59e0b; animation: pa-pulse 1.5s infinite;
}
.pa-support-dot.connected { background: #22c55e; }
#pa-support-end {
  margin-left: auto; background: none; border: 1px solid #a78bfa;
  color: #7c3aed; border-radius: 6px; padding: 3px 9px;
  font-size: .7rem; font-weight: 700; cursor: pointer;
  transition: all .15s;
}
#pa-support-end:hover { background: #7c3aed; color: #fff; border-color: #7c3aed; }
.pa-row.pa-agent .pa-bbl { background: #ede9fe; color: #4c1d95; border-bottom-left-radius: 4px; }
.pa-row.pa-agent .pa-rav { background: linear-gradient(135deg,#7c3aed,#5b21b6); }

/* Mobile */
@media (max-width: 640px) {
  #pa-wrap { bottom: 16px; right: 14px; }
  #pa-panel {
    bottom: 82px; right: 8px; left: 8px;
    width: auto; max-height: calc(100vh - 98px);
    border-radius: 16px;
  }
  #pa-panel.pa-fs {
    top: 8px !important; bottom: 8px !important;
    left: 8px !important; right: 8px !important;
  }
  #pa-bubble { max-width: 190px; font-size: .77rem; }
}
  `;
  document.head.appendChild(s);
}

/* ── DOM ────────────────────────────────────────────────────────────────────────── */
function _injectDOM() {
  // Panel is injected directly on body (position:fixed), NOT inside the wrap,
  // so it never affects the wrap's height or position.
  const panel = document.createElement('div');
  panel.id = 'pa-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Pritis AI Assistant');
  panel.setAttribute('aria-modal', 'false');
  panel.innerHTML = `
    <div id="pa-head">
      <div id="pa-av">P</div>
      <div id="pa-info">
        <div id="pa-name">Pritis AI</div>
        <div id="pa-stat"><span class="pa-sdot"></span> Online · Always here to help</div>
      </div>
      <button class="pa-hbtn" id="pa-support-btn" title="Contact Support"
              style="width:auto;padding:0 10px;border-radius:8px;font-size:.72rem;font-weight:700;letter-spacing:.01em;white-space:nowrap">
        Contact Support
      </button>
      <button class="pa-hbtn" id="pa-expand" title="Expand">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
      </button>
      <button class="pa-hbtn" id="pa-hclose" title="Close">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div id="pa-support-banner">
      <span class="pa-support-dot" id="pa-sdot"></span>
      <span id="pa-support-status-text">Connecting to support…</span>
      <button id="pa-support-end">End Chat</button>
    </div>
    <div id="pa-msgs"></div>
    <div id="pa-typing">
      <div class="pa-rav">P</div>
      <div class="pa-tdots"><span></span><span></span><span></span></div>
    </div>
    <div id="pa-fprev">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      <span id="pa-fname">file.pdf</span>
      <button id="pa-fremove" title="Remove">×</button>
    </div>
    <div id="pa-bar">
      <button class="pa-ibtn" id="pa-attach" title="Upload file">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
      </button>
      <input type="file" id="pa-finput" accept=".pdf,.docx,.pptx,.jpg,.jpeg,.png,.webp" style="display:none">
      <textarea id="pa-ta" placeholder="Ask anything…" rows="1"></textarea>
      <button class="pa-ibtn" id="pa-voice" title="Voice input">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
      </button>
      <button class="pa-ibtn" id="pa-send" title="Send">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
      </button>
    </div>
  `;
  document.body.appendChild(panel);

  // Wrap holds only the bubble + floating button
  const wrap = document.createElement('div');
  wrap.id = 'pa-wrap';
  wrap.innerHTML = `
    <div id="pa-bubble">
      <span id="pa-btxt">Need help with anything? 💬</span>
      <button id="pa-bdismiss" title="Dismiss">×</button>
    </div>
    <button id="pa-btn" title="Pritis AI Assistant" aria-label="Open AI Assistant">
      <svg class="pa-ico-chat" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
        <path d="M8 10h.01M12 10h.01M16 10h.01" stroke-width="2.5"/>
      </svg>
      <svg class="pa-ico-close" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
      <span id="pa-dot"></span>
    </button>
  `;
  document.body.appendChild(wrap);
}

/* ── Event binding ──────────────────────────────────────────────────────────────── */
function _bindEvents() {
  _q('#pa-btn').addEventListener('click', _toggle);
  _q('#pa-hclose').addEventListener('click', _close);
  _q('#pa-expand').addEventListener('click', _toggleFullscreen);
  _q('#pa-support-btn').addEventListener('click', _openSupport);
  _q('#pa-support-end').addEventListener('click', _endSupport);

  // Idle bubble
  _q('#pa-bubble').addEventListener('click', e => {
    if (!e.target.closest('#pa-bdismiss')) _open();
  });
  _q('#pa-bdismiss').addEventListener('click', e => {
    e.stopPropagation();
    _hideBubble();
    // Don't show again for 5 min if user dismisses
    clearTimeout(_s.bubbleTimer);
    _s.bubbleTimer = setTimeout(_showBubble, 5 * 60 * 1000);
  });

  // Textarea
  const ta = _q('#pa-ta');
  ta.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
  });
  ta.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _send(); }
  });

  _q('#pa-send').addEventListener('click', _send);

  // File
  _q('#pa-attach').addEventListener('click', () => _q('#pa-finput').click());
  _q('#pa-finput').addEventListener('change', e => {
    const f = e.target.files[0];
    if (f) _setFile(f);
    e.target.value = '';
  });
  _q('#pa-fremove').addEventListener('click', _clearFile);

  // Voice
  _q('#pa-voice').addEventListener('click', _toggleVoice);
}

/* ── Panel open / close ──────────────────────────────────────────────────────────── */
function _toggle() { _s.open ? _close() : _open(); }

function _open() {
  if (_s.open) return;
  _s.open = true;
  _q('#pa-panel').classList.add('pa-open');
  _q('#pa-btn').classList.add('pa-open');
  _hideBubble();
  clearTimeout(_s.bubbleTimer);
  clearTimeout(_s.idleTimer);

  // Greeting on first open
  if (!_q('#pa-msgs').firstChild) {
    const page     = window.location.pathname.split('/').pop();
    const greeting = _PAGE_GREETINGS[page] || "Hi! I'm Pritis AI. How can I help you today? 😊";
    _addMsg('ai', greeting);
  }

  setTimeout(() => _q('#pa-ta')?.focus(), 320);
}

function _close() {
  if (!_s.open) return;
  _s.open = false;
  _q('#pa-panel').classList.remove('pa-open', 'pa-fs');
  _q('#pa-btn').classList.remove('pa-open');
  document.body.classList.remove('pa-fs-active');

  // Resume idle cycle
  clearTimeout(_s.bubbleTimer);
  _s.bubbleTimer = setTimeout(_showBubble, 45 * 1000);
}

function _toggleFullscreen() {
  const panel = _q('#pa-panel');
  const isFs  = panel.classList.toggle('pa-fs');
  document.body.classList.toggle('pa-fs-active', isFs);
  const btn   = _q('#pa-expand');
  btn.title   = isFs ? 'Collapse' : 'Expand';
  // Scroll messages to bottom after resize
  setTimeout(() => { _q('#pa-msgs').scrollTop = _q('#pa-msgs').scrollHeight; }, 320);
}

/* ── Idle bubble ─────────────────────────────────────────────────────────────────── */
function _showBubble() {
  if (_s.open) return;
  const bubble = _q('#pa-bubble');
  const txt    = _q('#pa-btxt');
  if (!bubble || !txt) return;

  txt.textContent = _BUBBLES[_s.bubbleIdx % _BUBBLES.length];
  _s.bubbleIdx++;
  bubble.classList.add('pa-vis');
  _q('#pa-dot').style.display = 'block';

  // Auto-hide after 8 s, then cycle every 45 s
  _s.idleTimer = setTimeout(() => {
    _hideBubble();
    clearTimeout(_s.bubbleTimer);
    _s.bubbleTimer = setTimeout(_showBubble, 45 * 1000);
  }, 8000);
}

function _hideBubble() {
  _q('#pa-bubble')?.classList.remove('pa-vis');
  const dot = _q('#pa-dot');
  if (dot) dot.style.display = 'none';
  clearTimeout(_s.idleTimer);
}

/* ── Messages ──────────────────────────────────────────────────────────────────────── */
function _addMsg(role, text, senderName) {
  const msgs = _q('#pa-msgs');
  const row  = document.createElement('div');
  const isUser  = role === 'user';
  const isAgent = role === 'agent';
  row.className = `pa-row ${isUser ? 'pa-u' : isAgent ? 'pa-agent' : 'pa-ai'}`;
  const body = `<div class="pa-bbl">${_fmt(text)}</div>`;
  const initials = isAgent ? (senderName ? senderName[0].toUpperCase() : 'A') : 'P';
  row.innerHTML  = isUser
    ? body
    : `<div class="pa-rav">${initials}</div><div>${
        senderName && !isUser ? `<div style="font-size:.65rem;color:var(--text-muted);margin-bottom:2px">${_esc(senderName)}</div>` : ''
      }${body}</div>`;
  msgs.appendChild(row);
  msgs.scrollTop = msgs.scrollHeight;

  if (_s.history.length > 0 || role === 'user') {
    _s.history.push({ role: isUser ? 'user' : 'assistant', content: text });
  }
}

function _setTyping(show) {
  const el = _q('#pa-typing');
  el.classList.toggle('pa-show', show);
  if (show) _q('#pa-msgs').scrollTop = _q('#pa-msgs').scrollHeight;
}

/* ── Support mode ────────────────────────────────────────────────────────────────────── */
async function _openSupport() {
  if (_s.supportMode) return;
  if (!_s.open) _open();
  // Show a prompt in the chat
  _addMsg('ai',
    "I'll connect you to a human support agent. **Briefly describe your issue** and press Send, " +
    "or just press the headphone button again to connect without a message."
  );
  _s.supportMode = 'pending';   // pending = waiting for user to type issue
  _q('#pa-support-btn').style.background = 'rgba(124,58,237,.35)';
  _q('#pa-ta').placeholder = 'Describe your issue…';
  _q('#pa-ta').focus();
}

async function _activateSupport(issue) {
  _s.supportMode = true;
  _q('#pa-panel').classList.add('pa-support');
  _q('#pa-name').textContent = 'Support Chat';
  _q('#pa-stat').innerHTML   = '<span class="pa-sdot"></span> Connecting to agent…';
  _q('#pa-support-banner').classList.add('pa-show');
  _q('#pa-support-btn').style.display = 'none';
  _updateSupportBanner('waiting');

  try {
    const data = await api.post('/support/web/start', { issue: issue || null });
    _s.supportConvId = data.id;
    _s.supportStatus = data.status;
    _updateSupportBanner(data.status, data.assigned_agent);
    // Render any messages already on the conversation (AI first response)
    if (data.messages?.length) {
      for (const m of data.messages) {
        if (m.sender_type !== 'user') {
          _addMsg(m.sender_type === 'agent' ? 'agent' : 'ai', m.content, m.sender_name);
        }
      }
    }
    _connectSupportWs(data.id);
  } catch (e) {
    _addMsg('ai', 'Could not connect to support: ' + e.message);
    _endSupport();
  }
}

function _connectSupportWs(convId) {
  const token = getToken();
  if (!token || !convId) return;
  const ws = new WebSocket(`${_WS_BASE}/support/user-ws/${convId}?token=${token}`);
  _s.supportWs = ws;

  ws._ping = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: 'ping' })), 25000);

  ws.onmessage = e => {
    try {
      const ev = JSON.parse(e.data);
      if (ev.type === 'message') {
        const role = ev.sender_type === 'agent' ? 'agent' : 'ai';
        _addMsg(role, ev.content, ev.sender_name);
        if (ev.conv_status) { _s.supportStatus = ev.conv_status; _updateSupportBanner(ev.conv_status); }
      } else if (ev.type === 'status_changed') {
        _s.supportStatus = ev.status;
        _updateSupportBanner(ev.status);
        if (ev.status === 'closed') _endSupport(true);
      }
    } catch {}
  };

  ws.onclose = () => clearInterval(ws._ping);
  ws.onerror = () => ws.close();
}

function _updateSupportBanner(status, agentName) {
  const dot  = _q('#pa-sdot');
  const text = _q('#pa-support-status-text');
  const stat = _q('#pa-stat');
  if (!dot || !text) return;
  if (status === 'human') {
    dot.classList.add('connected');
    const label = agentName ? `Agent ${agentName} is with you` : 'Connected to a human agent';
    text.textContent = label;
    if (stat) stat.innerHTML = `<span class="pa-sdot" style="background:#22c55e"></span> ${label}`;
  } else if (status === 'waiting') {
    dot.classList.remove('connected');
    text.textContent = 'Waiting for an available agent…';
    if (stat) stat.innerHTML = '<span class="pa-sdot" style="background:#f59e0b"></span> Waiting for agent…';
  } else if (status === 'ai') {
    dot.classList.add('connected');
    text.textContent = 'AI Support active';
    if (stat) stat.innerHTML = '<span class="pa-sdot"></span> AI Support';
  }
}

async function _endSupport(silent = false) {
  if (_s.supportWs) { _s.supportWs.close(); _s.supportWs = null; }
  if (_s.supportConvId && !silent) {
    try { await api.post('/support/web/close', {}); } catch {}
  }
  _s.supportMode   = false;
  _s.supportConvId = null;
  _s.supportStatus = null;
  _q('#pa-panel').classList.remove('pa-support');
  _q('#pa-support-banner').classList.remove('pa-show');
  _q('#pa-name').textContent = 'Pritis AI';
  _q('#pa-stat').innerHTML   = '<span class="pa-sdot"></span> Online · Always here to help';
  _q('#pa-support-btn').style.display = '';
  _q('#pa-support-btn').style.background = '';
  _q('#pa-ta').placeholder = 'Ask anything…';
  if (!silent) _addMsg('ai', 'Support chat ended. How else can I help you?');
}

/* ── Send ──────────────────────────────────────────────────────────────────────────── */
async function _send() {
  const ta  = _q('#pa-ta');
  const msg = ta.value.trim();
  if (!msg && !_s.pendingFile) return;

  const sendBtn = _q('#pa-send');
  sendBtn.disabled = true;

  // ── Support mode: pending → activate with user's issue ───────────────────
  if (_s.supportMode === 'pending') {
    _addMsg('user', msg || 'Connect me to support');
    ta.value = ''; ta.style.height = '36px';
    sendBtn.disabled = false;
    _activateSupport(msg);
    return;
  }

  // ── Support mode: active → send message via support API ──────────────────
  if (_s.supportMode === true) {
    if (!msg) { sendBtn.disabled = false; return; }
    _addMsg('user', msg);
    ta.value = ''; ta.style.height = '36px';
    try {
      const res = await api.post('/support/web/message', { content: msg });
      // AI response comes via WebSocket; REST response is a fallback confirmation
      if (res.ai_response && !_s.supportWs) {
        _addMsg('ai', res.ai_response.content);
      }
      if (res.conv_status) { _s.supportStatus = res.conv_status; _updateSupportBanner(res.conv_status); }
    } catch (e) {
      _addMsg('ai', 'Message failed: ' + e.message);
    } finally {
      sendBtn.disabled = false;
      _q('#pa-ta')?.focus();
    }
    return;
  }

  const displayMsg = msg || `Analyse: ${_s.pendingFile?.name}`;
  _addMsg('user', displayMsg);
  ta.value = '';
  ta.style.height = '36px';
  _setTyping(true);

  try {
    let reply;
    if (_s.pendingFile) {
      const form = new FormData();
      form.append('file', _s.pendingFile);
      form.append('message', msg || 'Analyse this file and explain the key points.');
      form.append('history', JSON.stringify(_s.history.slice(0, -1).slice(-8)));
      const data = await api.postForm('/assistant/upload', form);
      reply = data.reply;
      _clearFile();
    } else {
      const data = await api.post('/assistant/chat', {
        message: msg,
        history: _s.history.slice(0, -1).slice(-12),
      });
      reply = data.reply;
    }
    _setTyping(false);
    _addMsg('ai', reply);
  } catch (err) {
    _setTyping(false);
    _addMsg('ai', `Sorry, something went wrong: ${err.message}. Please try again.`);
  } finally {
    sendBtn.disabled = false;
    _q('#pa-ta')?.focus();
  }
}

/* ── File handling ──────────────────────────────────────────────────────────────────── */
function _setFile(file) {
  _s.pendingFile = file;
  _q('#pa-fname').textContent = file.name;
  _q('#pa-fprev').classList.add('pa-show');
}

function _clearFile() {
  _s.pendingFile = null;
  _q('#pa-fprev').classList.remove('pa-show');
}

/* ── Voice ──────────────────────────────────────────────────────────────────────────── */
function _toggleVoice() { _s.recording ? _stopVoice() : _startVoice(); }

async function _startVoice() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    _s.chunks = [];

    // Pick best supported mime type
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';

    _s.mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    _s.mediaRecorder.ondataavailable = e => { if (e.data.size > 0) _s.chunks.push(e.data); };
    _s.mediaRecorder.onstop = _handleVoiceStop;
    _s.mediaRecorder.start();

    _s.recording = true;
    _q('#pa-voice').classList.add('pa-rec');
    _q('#pa-voice').title = 'Stop recording';
  } catch {
    _addMsg('ai', 'Could not access your microphone. Please check browser permissions and try again.');
  }
}

function _stopVoice() {
  _s.mediaRecorder?.stop();
  _s.mediaRecorder?.stream?.getTracks().forEach(t => t.stop());
  _s.mediaRecorder = null;
  _s.recording = false;
  _q('#pa-voice').classList.remove('pa-rec');
  _q('#pa-voice').title = 'Voice input';
}

async function _handleVoiceStop() {
  if (!_s.chunks.length) return;
  const blob = new Blob(_s.chunks, { type: _s.chunks[0]?.type || 'audio/webm' });
  _s.chunks  = [];

  const ta = _q('#pa-ta');
  const origPlaceholder = ta.placeholder;
  ta.placeholder = 'Transcribing…';

  try {
    const form = new FormData();
    form.append('file', blob, 'audio.webm');
    const data = await api.postForm('/assistant/transcribe', form);
    if (data?.text?.trim()) {
      ta.value = data.text.trim();
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
      ta.focus();
    }
  } catch {
    _addMsg('ai', "Couldn't transcribe that recording. Please type your question instead.");
  } finally {
    ta.placeholder = origPlaceholder;
  }
}

/* ── Quiz-practice watcher ──────────────────────────────────────────────────────────── */
function _watchQuizState() {
  function _update() {
    const hide  = _isQuizActive();
    const d     = hide ? 'none' : '';
    const wrap  = _q('#pa-wrap');
    const panel = _q('#pa-panel');
    if (wrap)  wrap.style.display  = d;
    if (panel) panel.style.display = d;
    if (hide && _s.open) _close();
  }

  new MutationObserver(_update).observe(document.body, {
    subtree: true, attributes: true, attributeFilter: ['class', 'style'],
  });
  _update();
  setTimeout(_update, 600);
}

/* ── Helpers ──────────────────────────────────────────────────────────────────────────── */
function _q(sel) { return document.querySelector(sel); }

function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _fmt(text) {
  return _esc(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,     '<em>$1</em>')
    .replace(/`(.+?)`/g,       '<code style="background:rgba(0,0,0,.07);padding:1px 5px;border-radius:4px;font-size:.9em">$1</code>')
    .replace(/\n/g, '<br>');
}
