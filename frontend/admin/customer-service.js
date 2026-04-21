/**
 * Customer Service Dashboard
 * Three-column layout: Conversations | Chat Window | Agent Panel
 * Real-time via WebSocket.
 */

import { getToken, getUser } from '../js/api.js';

const _isLocal = ['localhost', '127.0.0.1'].includes(location.hostname);
const BASE    = _isLocal ? 'http://127.0.0.1:8000/api' : 'https://www.pritis.name.ng/api';
const WS_BASE = _isLocal ? 'ws://127.0.0.1:8000/api'  : 'wss://www.pritis.name.ng/api';

/* ── State ─────────────────────────────────────────────────────────────────── */
const _st = {
  conversations: [],
  agents:        [],
  activeConvId:  null,
  messages:      {},   // convId → Message[]
  ws:            null,
  wsRetry:       0,
  totalUnread:   0,
  filterStatus:  'all',
  searchText:    '',
};

/* ── Bootstrap ──────────────────────────────────────────────────────────────── */
export function initCustomerService() {
  const root = document.getElementById('cs-root');
  if (!root || root.dataset.loaded) return;
  root.dataset.loaded = '1';

  _injectStyles();
  root.innerHTML = _buildHTML();
  _bindEvents();
  _load();
  _connectWs();
}

/* ── Styles ─────────────────────────────────────────────────────────────────── */
function _injectStyles() {
  if (document.getElementById('cs-styles')) return;
  const s = document.createElement('style');
  s.id = 'cs-styles';
  s.textContent = `
/* ── Layout ── */
#cs-layout {
  display: grid;
  grid-template-columns: 300px 1fr 260px;
  height: 100%;
  overflow: hidden;
  background: var(--bg, #f8fafc);
}
@media (max-width: 900px) {
  #cs-layout { grid-template-columns: 260px 1fr; }
  #cs-agent-panel { display: none; }
}
@media (max-width: 640px) {
  #cs-layout { grid-template-columns: 1fr; }
  #cs-conv-list-col { display: none; }
  #cs-conv-list-col.cs-mob-show { display: flex; }
}

/* ── Stats bar ── */
#cs-stats {
  display: flex; gap: 10px; padding: 12px 14px;
  background: var(--bg-card); border-bottom: 1px solid var(--border);
  flex-wrap: wrap; flex-shrink: 0;
  grid-column: 1 / -1;
}
.cs-stat { text-align: center; flex: 1; min-width: 70px; }
.cs-stat-val { font-size: 1.3rem; font-weight: 800; color: var(--primary); }
.cs-stat-lbl { font-size: .68rem; color: var(--text-muted); margin-top: 1px; }

/* ── Conversations column ── */
#cs-conv-list-col {
  display: flex; flex-direction: column;
  border-right: 1px solid var(--border);
  background: var(--bg-card);
  overflow: hidden;
}
.cs-col-head {
  padding: 12px 12px 8px;
  border-bottom: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 8px;
  flex-shrink: 0;
}
.cs-col-title { font-size: .82rem; font-weight: 700; color: var(--text); }
.cs-search {
  display: flex; align-items: center; gap: 6px;
  background: var(--bg); border: 1.5px solid var(--border);
  border-radius: 8px; padding: 6px 10px;
}
.cs-search input {
  flex: 1; border: none; background: none; outline: none;
  font-size: .8rem; color: var(--text);
}
.cs-filter-row { display: flex; gap: 4px; flex-wrap: wrap; }
.cs-filter-pill {
  padding: 3px 10px; border-radius: 20px; font-size: .72rem; font-weight: 600;
  border: 1.5px solid var(--border); background: none; cursor: pointer;
  color: var(--text-muted); transition: all .15s;
}
.cs-filter-pill.active, .cs-filter-pill:hover {
  background: var(--primary); color: #fff; border-color: var(--primary);
}
#cs-conv-list { flex: 1; overflow-y: auto; }
#cs-conv-list::-webkit-scrollbar { width: 3px; }
#cs-conv-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* Conversation item */
.cs-conv-item {
  padding: 11px 13px; cursor: pointer;
  border-bottom: 1px solid var(--border);
  transition: background .12s;
  display: flex; align-items: center; gap: 10px;
}
.cs-conv-item:hover { background: rgba(0,119,255,.04); }
.cs-conv-item.active { background: rgba(0,119,255,.08); border-left: 3px solid var(--primary); }
.cs-conv-avatar {
  width: 38px; height: 38px; border-radius: 50%; flex-shrink: 0;
  background: linear-gradient(135deg,#0077ff,#005acc);
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 700; font-size: .85rem;
}
.cs-conv-meta { flex: 1; min-width: 0; }
.cs-conv-phone { font-size: .82rem; font-weight: 600; color: var(--text); }
.cs-conv-preview { font-size: .74rem; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 1px; }
.cs-conv-right { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; flex-shrink: 0; }
.cs-conv-time { font-size: .67rem; color: var(--text-muted); }
.cs-unread-dot {
  width: 18px; height: 18px; border-radius: 50%;
  background: var(--primary); color: #fff;
  font-size: .62rem; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}

/* Status pills */
.cs-status { padding: 2px 8px; border-radius: 20px; font-size: .65rem; font-weight: 700; text-transform: uppercase; }
.cs-status-ai      { background: #dbeafe; color: #1d4ed8; }
.cs-status-human   { background: #dcfce7; color: #166534; }
.cs-status-waiting { background: #fef3c7; color: #b45309; }
.cs-status-closed  { background: var(--bg); color: var(--text-muted); }

/* ── Chat window ── */
#cs-chat-col {
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--bg, #f8fafc);
}
#cs-chat-header {
  padding: 10px 14px; background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px; flex-shrink: 0;
}
#cs-chat-phone { font-size: .9rem; font-weight: 700; color: var(--text); flex: 1; }
.cs-hdr-actions { display: flex; gap: 6px; }
.cs-hdr-btn {
  padding: 4px 12px; border-radius: 7px; font-size: .74rem; font-weight: 600;
  border: 1.5px solid var(--border); background: none; cursor: pointer;
  color: var(--text-muted); transition: all .15s;
}
.cs-hdr-btn:hover { border-color: var(--primary); color: var(--primary); }
.cs-hdr-btn.danger:hover { border-color: #ef4444; color: #ef4444; }

#cs-messages {
  flex: 1; overflow-y: auto; padding: 14px;
  display: flex; flex-direction: column; gap: 10px;
}
#cs-messages::-webkit-scrollbar { width: 4px; }
#cs-messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

.cs-msg { display: flex; gap: 8px; align-items: flex-end; }
.cs-msg.cs-msg-user { flex-direction: row-reverse; }
.cs-msg-bubble {
  max-width: 68%; padding: 9px 13px; border-radius: 16px;
  font-size: .83rem; line-height: 1.5; word-break: break-word;
}
.cs-msg.cs-msg-user .cs-msg-bubble  { background: #e0edff; color: #1e3a6e; border-bottom-right-radius: 4px; }
.cs-msg.cs-msg-ai .cs-msg-bubble    { background: #f0fdf4; color: #14532d; border-bottom-left-radius: 4px; }
.cs-msg.cs-msg-agent .cs-msg-bubble { background: var(--primary); color: #fff; border-bottom-left-radius: 4px; }
.cs-msg-avatar {
  width: 26px; height: 26px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: .68rem; font-weight: 700; color: #fff; flex-shrink: 0;
}
.cs-msg.cs-msg-user .cs-msg-avatar  { background: #64748b; }
.cs-msg.cs-msg-ai .cs-msg-avatar    { background: #16a34a; }
.cs-msg.cs-msg-agent .cs-msg-avatar { background: var(--primary); }
.cs-msg-time { font-size: .65rem; color: var(--text-muted); margin-top: 2px; }
.cs-msg-label { font-size: .68rem; color: var(--text-muted); margin-bottom: 2px; }

.cs-empty-chat {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: var(--text-muted); gap: 10px;
}
.cs-empty-chat svg { opacity: .3; }

/* Input bar */
#cs-input-bar {
  padding: 10px 12px; background: var(--bg-card);
  border-top: 1px solid var(--border);
  display: flex; gap: 8px; align-items: flex-end; flex-shrink: 0;
}
#cs-input {
  flex: 1; background: var(--bg); border: 1.5px solid var(--border);
  border-radius: 12px; padding: 8px 13px;
  font-size: .84rem; font-family: inherit; color: var(--text);
  resize: none; outline: none; min-height: 36px; max-height: 100px; line-height: 1.5;
  transition: border-color .15s;
}
#cs-input:focus { border-color: var(--primary); }
#cs-input::placeholder { color: var(--text-muted); }
#cs-send-btn {
  width: 38px; height: 38px; border-radius: 50%; border: none;
  background: var(--primary); color: #fff; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background .15s; flex-shrink: 0;
}
#cs-send-btn:hover { background: #005fcc; }
#cs-send-btn:disabled { opacity: .5; cursor: default; }

/* ── Agent panel ── */
#cs-agent-panel {
  display: flex; flex-direction: column;
  border-left: 1px solid var(--border);
  background: var(--bg-card);
  overflow-y: auto;
}
.cs-ap-section { padding: 12px 13px; border-bottom: 1px solid var(--border); }
.cs-ap-title { font-size: .75rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 10px; }

.cs-agent-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 0;
}
.cs-agent-av {
  width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
  background: linear-gradient(135deg, #7c3aed, #5b21b6);
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: .72rem; font-weight: 700;
}
.cs-agent-name { font-size: .82rem; font-weight: 600; color: var(--text); flex: 1; }
.cs-agent-convs { font-size: .7rem; color: var(--text-muted); }
.cs-status-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.cs-dot-online  { background: #22c55e; }
.cs-dot-busy    { background: #f59e0b; }
.cs-dot-offline { background: #94a3b8; }

.cs-ap-select {
  width: 100%; padding: 7px 10px; border-radius: 8px;
  border: 1.5px solid var(--border); background: var(--bg);
  font-size: .8rem; color: var(--text); cursor: pointer; margin-top: 6px;
}
.cs-ap-action-btn {
  width: 100%; padding: 8px; border-radius: 8px; font-size: .8rem; font-weight: 600;
  border: 1.5px solid var(--border); background: none; cursor: pointer;
  color: var(--text); margin-top: 6px; transition: all .15s;
}
.cs-ap-action-btn:hover { background: rgba(0,119,255,.08); border-color: var(--primary); color: var(--primary); }
.cs-ap-action-btn.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.cs-ap-action-btn.primary:hover { background: #005fcc; }

/* Add agent form */
#cs-add-agent-form { display: none; flex-direction: column; gap: 8px; margin-top: 8px; }
#cs-add-agent-form.visible { display: flex; }
.cs-input-sm {
  padding: 7px 10px; border-radius: 8px; font-size: .8rem;
  border: 1.5px solid var(--border); background: var(--bg);
  color: var(--text); outline: none; transition: border-color .15s;
}
.cs-input-sm:focus { border-color: var(--primary); }

/* WS status indicator */
#cs-ws-status {
  display: flex; align-items: center; gap: 5px;
  font-size: .72rem; color: var(--text-muted); padding: 6px 14px;
  border-bottom: 1px solid var(--border); background: var(--bg-card);
  grid-column: 1 / -1; flex-shrink: 0;
}
#cs-ws-dot { width: 7px; height: 7px; border-radius: 50%; background: #94a3b8; }
#cs-ws-dot.connected { background: #22c55e; animation: cs-pulse 2s infinite; }
@keyframes cs-pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  `;
  document.head.appendChild(s);
}

/* ── HTML template ───────────────────────────────────────────────────────────── */
function _buildHTML() {
  return `
<div id="cs-layout" style="display:grid;grid-template-rows:auto auto 1fr;height:100%">

  <!-- Stats row -->
  <div id="cs-stats">
    <div class="cs-stat"><div class="cs-stat-val" id="cs-s-total">–</div><div class="cs-stat-lbl">Total</div></div>
    <div class="cs-stat"><div class="cs-stat-val" id="cs-s-ai" style="color:#1d4ed8">–</div><div class="cs-stat-lbl">AI</div></div>
    <div class="cs-stat"><div class="cs-stat-val" id="cs-s-waiting" style="color:#b45309">–</div><div class="cs-stat-lbl">Waiting</div></div>
    <div class="cs-stat"><div class="cs-stat-val" id="cs-s-human" style="color:#166534">–</div><div class="cs-stat-lbl">Human</div></div>
    <div class="cs-stat"><div class="cs-stat-val" id="cs-s-closed" style="color:var(--text-muted)">–</div><div class="cs-stat-lbl">Closed</div></div>
    <div class="cs-stat"><div class="cs-stat-val" id="cs-s-agents">–</div><div class="cs-stat-lbl">Agents</div></div>
    <div class="cs-stat"><div class="cs-stat-val" id="cs-s-online" style="color:#22c55e">–</div><div class="cs-stat-lbl">Online</div></div>
  </div>

  <!-- WS status -->
  <div id="cs-ws-status">
    <span id="cs-ws-dot"></span>
    <span id="cs-ws-label">Connecting…</span>
  </div>

  <!-- 3-column body -->
  <div style="display:grid;grid-template-columns:300px 1fr 260px;overflow:hidden;min-height:0;grid-column:1/-1">

    <!-- Col 1: Conversations -->
    <div id="cs-conv-list-col">
      <div class="cs-col-head">
        <span class="cs-col-title">Conversations</span>
        <div class="cs-search">
          <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
          <input type="text" placeholder="Search phone…" id="cs-search-input" oninput="csSearch(this.value)">
        </div>
        <div class="cs-filter-row">
          <button class="cs-filter-pill active" onclick="csFilter('all', this)">All</button>
          <button class="cs-filter-pill" onclick="csFilter('ai', this)">AI</button>
          <button class="cs-filter-pill" onclick="csFilter('waiting', this)">Waiting</button>
          <button class="cs-filter-pill" onclick="csFilter('human', this)">Human</button>
          <button class="cs-filter-pill" onclick="csFilter('closed', this)">Closed</button>
        </div>
      </div>
      <div id="cs-conv-list"><div style="padding:24px;text-align:center;color:var(--text-muted);font-size:.82rem">Loading…</div></div>
    </div>

    <!-- Col 2: Chat -->
    <div id="cs-chat-col">
      <div id="cs-chat-header" style="display:none">
        <div>
          <div id="cs-chat-phone" style="font-size:.9rem;font-weight:700"></div>
          <div id="cs-chat-status-row" style="display:flex;align-items:center;gap:6px;margin-top:2px"></div>
        </div>
        <div class="cs-hdr-actions">
          <select class="cs-hdr-btn" id="cs-status-select" onchange="csUpdateStatus(this.value)" style="padding:4px 8px;border-radius:7px;font-size:.74rem;cursor:pointer;border:1.5px solid var(--border);background:var(--bg-card);color:var(--text)">
            <option value="ai">AI Mode</option>
            <option value="human">Human</option>
            <option value="waiting">Waiting</option>
            <option value="closed">Close</option>
          </select>
          <button class="cs-hdr-btn primary" onclick="csTakeOver()" style="background:var(--primary);color:#fff;border-color:var(--primary)">Take Over</button>
          <button class="cs-hdr-btn danger" onclick="csCloseConv()">Close</button>
        </div>
      </div>

      <div id="cs-messages">
        <div class="cs-empty-chat">
          <svg width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
          <p style="font-size:.88rem">Select a conversation to start</p>
        </div>
      </div>

      <div id="cs-input-bar">
        <textarea id="cs-input" placeholder="Type a reply…" rows="1" onkeydown="csInputKey(event)" oninput="csInputGrow(this)"></textarea>
        <button id="cs-send-btn" onclick="csSend()">
          <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </button>
      </div>
    </div>

    <!-- Col 3: Agent panel -->
    <div id="cs-agent-panel">
      <div class="cs-ap-section">
        <div class="cs-ap-title">Active Agents</div>
        <div id="cs-agent-list"><div style="font-size:.78rem;color:var(--text-muted)">Loading…</div></div>
        <button class="cs-ap-action-btn" onclick="csToggleAddAgent()">+ Add Agent</button>
        <div id="cs-add-agent-form">
          <input class="cs-input-sm" id="cs-ag-name" placeholder="Agent name *">
          <input class="cs-input-sm" id="cs-ag-email" placeholder="Email (optional)">
          <button class="cs-ap-action-btn primary" onclick="csAddAgent()">Create Agent</button>
          <button class="cs-ap-action-btn" onclick="csToggleAddAgent()">Cancel</button>
        </div>
      </div>

      <div class="cs-ap-section" id="cs-conv-detail-section" style="display:none">
        <div class="cs-ap-title">This Conversation</div>
        <div id="cs-conv-detail-body" style="font-size:.8rem;color:var(--text);line-height:1.7"></div>
        <div class="cs-ap-title" style="margin-top:12px">Assign Agent</div>
        <select class="cs-ap-select" id="cs-assign-select" onchange="csAssign(this.value)">
          <option value="">— Unassigned —</option>
        </select>
        <button class="cs-ap-action-btn primary" onclick="csTakeOver()">
          Take Over as Agent
        </button>
      </div>

      <!-- Test / Simulate panel -->
      <div class="cs-ap-section">
        <div class="cs-ap-title" style="display:flex;align-items:center;gap:6px">
          <span>🧪</span> Simulate Message
        </div>
        <div style="font-size:.73rem;color:var(--text-muted);margin-bottom:8px;line-height:1.5">
          Send a fake incoming message to test the dashboard without WhatsApp.
        </div>
        <input class="cs-input-sm" id="cs-sim-phone" placeholder="Phone e.g. +2348012345678" style="margin-bottom:6px;width:100%;box-sizing:border-box">
        <input class="cs-input-sm" id="cs-sim-name" placeholder="Name (optional)" style="margin-bottom:6px;width:100%;box-sizing:border-box">
        <textarea class="cs-input-sm" id="cs-sim-msg" placeholder="Message text…" rows="2" style="width:100%;box-sizing:border-box;resize:vertical"></textarea>
        <button class="cs-ap-action-btn primary" onclick="csSimulate()" id="cs-sim-btn" style="margin-top:6px">
          Send Test Message
        </button>
        <div id="cs-sim-result" style="font-size:.73rem;margin-top:6px;display:none"></div>
      </div>

    </div>

  </div><!-- /3-col -->
</div><!-- #cs-layout -->
  `;
}

/* ── Events ─────────────────────────────────────────────────────────────────── */
function _bindEvents() {
  // expose globals for inline handlers
  window.csSearch      = _onSearch;
  window.csFilter      = _onFilter;
  window.csSend        = _sendMsg;
  window.csInputKey    = _inputKey;
  window.csInputGrow   = _inputGrow;
  window.csUpdateStatus = _updateStatus;
  window.csTakeOver    = _takeOver;
  window.csCloseConv   = _closeConv;
  window.csAssign      = _assignAgent;
  window.csAddAgent    = _addAgent;
  window.csToggleAddAgent = _toggleAddAgentForm;
  window.csSimulate    = _simulateIncoming;
}

/* ── Load data ───────────────────────────────────────────────────────────────── */
async function _load() {
  await Promise.all([_loadConversations(), _loadAgents(), _loadStats()]);
}

async function _loadConversations() {
  const params = new URLSearchParams();
  if (_st.filterStatus !== 'all') params.set('status', _st.filterStatus);
  if (_st.searchText) params.set('search', _st.searchText);

  const data = await _api(`/support/conversations?${params}`);
  if (!data) return;
  _st.conversations = data;
  _renderConvList();
}

async function _loadAgents() {
  const data = await _api('/support/agents');
  if (!data) return;
  _st.agents = data;
  _renderAgentPanel();
  _renderAssignSelect();
}

async function _loadStats() {
  const data = await _api('/support/stats');
  if (!data) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '–'; };
  set('cs-s-total',   data.total_conversations);
  set('cs-s-ai',      data.ai);
  set('cs-s-waiting', data.waiting);
  set('cs-s-human',   data.human);
  set('cs-s-closed',  data.closed);
  set('cs-s-agents',  data.total_agents);
  set('cs-s-online',  data.online_agents);
}

/* ── Render ──────────────────────────────────────────────────────────────────── */
function _renderConvList() {
  const el = document.getElementById('cs-conv-list');
  if (!el) return;
  const convs = _st.conversations;
  if (!convs.length) {
    el.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-muted);font-size:.82rem">No conversations yet.</div>`;
    return;
  }
  el.innerHTML = convs.map(c => {
    const initials = (c.user_name || c.user_phone || '?')[0].toUpperCase();
    const time     = c.last_message_at ? _relTime(c.last_message_at) : '';
    const preview  = _esc(c.last_message_text || 'No messages yet');
    const unread   = c.unread_count > 0
      ? `<div class="cs-unread-dot">${c.unread_count > 9 ? '9+' : c.unread_count}</div>` : '';
    return `
      <div class="cs-conv-item${c.id === _st.activeConvId ? ' active' : ''}"
           onclick="csOpenConv('${c.id}')">
        <div class="cs-conv-avatar">${initials}</div>
        <div class="cs-conv-meta">
          <div class="cs-conv-phone">${_esc(c.user_name || c.user_phone)}</div>
          <div class="cs-conv-preview">${preview}</div>
        </div>
        <div class="cs-conv-right">
          <span class="cs-status cs-status-${c.status}">${c.status}</span>
          <div class="cs-conv-time">${time}</div>
          ${unread}
        </div>
      </div>`;
  }).join('');

  // expose open handler globally
  window.csOpenConv = _openConv;
}

function _renderAgentPanel() {
  const el = document.getElementById('cs-agent-list');
  if (!el) return;
  if (!_st.agents.length) {
    el.innerHTML = `<div style="font-size:.78rem;color:var(--text-muted)">No agents yet.</div>`;
    return;
  }
  el.innerHTML = _st.agents.map(a => `
    <div class="cs-agent-item">
      <div class="cs-agent-av">${a.name[0].toUpperCase()}</div>
      <div style="flex:1;min-width:0">
        <div class="cs-agent-name">${_esc(a.name)}</div>
        <div class="cs-agent-convs">${a.open_conversations} open</div>
      </div>
      <span class="cs-status-dot cs-dot-${a.status}" title="${a.status}"></span>
      <select onchange="csAgentStatus('${a.id}', this.value)"
        style="font-size:.7rem;border:1px solid var(--border);border-radius:6px;padding:2px 6px;background:var(--bg);cursor:pointer">
        <option value="online"  ${a.status==='online'?'selected':''}>Online</option>
        <option value="busy"    ${a.status==='busy'?'selected':''}>Busy</option>
        <option value="offline" ${a.status==='offline'?'selected':''}>Offline</option>
      </select>
    </div>
  `).join('');
  window.csAgentStatus = _updateAgentStatus;
}

function _renderAssignSelect() {
  const sel = document.getElementById('cs-assign-select');
  if (!sel) return;
  const current = _st.activeConvId
    ? _st.conversations.find(c => c.id === _st.activeConvId)?.assigned_agent_id
    : null;
  sel.innerHTML = `<option value="">— Unassigned —</option>` +
    _st.agents.map(a =>
      `<option value="${a.id}" ${a.id === current ? 'selected' : ''}>${_esc(a.name)} (${a.status})</option>`
    ).join('');
}

async function _openConv(convId) {
  _st.activeConvId = convId;

  // Update active class
  document.querySelectorAll('.cs-conv-item').forEach(el => {
    el.classList.toggle('active', el.onclick?.toString().includes(convId));
  });
  _renderConvList();

  // Fetch messages
  const data = await _api(`/support/conversations/${convId}`);
  if (!data) return;
  _st.messages[convId] = data.messages || [];

  // Mark unread = 0 locally
  const conv = _st.conversations.find(c => c.id === convId);
  if (conv) { conv.unread_count = 0; _updateTabBadge(); }

  _renderChatHeader(data);
  _renderMessages(data.messages);
  _renderConvDetail(data);
}

function _renderChatHeader(conv) {
  const header = document.getElementById('cs-chat-header');
  const phone  = document.getElementById('cs-chat-phone');
  const status = document.getElementById('cs-chat-status-row');
  const sel    = document.getElementById('cs-status-select');
  if (!header) return;
  header.style.display = 'flex';
  phone.textContent    = conv.user_name || conv.user_phone;

  const agent = conv.assigned_agent ? ` · ${conv.assigned_agent}` : '';
  const ch    = conv.channel === 'whatsapp' ? '📱 WhatsApp' : '🌐 Web';
  status.innerHTML = `
    <span class="cs-status cs-status-${conv.status}">${conv.status}</span>
    <span style="font-size:.7rem;color:var(--text-muted)">${ch}${agent}</span>`;

  if (sel) sel.value = conv.status;
}

function _renderMessages(msgs) {
  const el = document.getElementById('cs-messages');
  if (!el) return;
  if (!msgs.length) {
    el.innerHTML = `<div class="cs-empty-chat"><p style="font-size:.82rem">No messages yet.</p></div>`;
    return;
  }
  el.innerHTML = msgs.map(m => {
    const cls   = `cs-msg-${m.sender_type}`;
    const init  = m.sender_name ? m.sender_name[0].toUpperCase() : (m.sender_type === 'user' ? 'U' : 'A');
    const label = m.sender_name || (m.sender_type === 'ai' ? 'Pritis AI' : m.sender_type === 'user' ? 'User' : 'Agent');
    const time  = _fmtTime(m.created_at);
    return `
      <div class="cs-msg ${cls}">
        <div class="cs-msg-avatar">${init}</div>
        <div>
          <div class="cs-msg-label">${_esc(label)} · ${time}</div>
          <div class="cs-msg-bubble">${_esc(m.content).replace(/\n/g,'<br>')}</div>
        </div>
      </div>`;
  }).join('');
  el.scrollTop = el.scrollHeight;
}

function _renderConvDetail(conv) {
  const section = document.getElementById('cs-conv-detail-section');
  const body    = document.getElementById('cs-conv-detail-body');
  if (!section || !body) return;
  section.style.display = '';
  body.innerHTML = `
    <div><strong>Phone:</strong> ${_esc(conv.user_phone)}</div>
    <div><strong>Channel:</strong> ${_esc(conv.channel)}</div>
    <div><strong>Status:</strong> <span class="cs-status cs-status-${conv.status}">${conv.status}</span></div>
    <div><strong>Agent:</strong> ${conv.assigned_agent ? _esc(conv.assigned_agent) : '—'}</div>
    <div><strong>Messages:</strong> ${(conv.messages || []).length}</div>
    <div><strong>Started:</strong> ${_fmtTime(conv.created_at)}</div>`;
  _renderAssignSelect();
}

/* ── Actions ─────────────────────────────────────────────────────────────────── */
async function _sendMsg() {
  if (!_st.activeConvId) return;
  const input = document.getElementById('cs-input');
  const text  = input.value.trim();
  if (!text) return;
  const btn = document.getElementById('cs-send-btn');
  btn.disabled = true;
  input.value = ''; input.style.height = '36px';

  try {
    await _api(`/support/conversations/${_st.activeConvId}/send`, 'POST', { content: text });
  } catch (e) {
    input.value = text;
    alert('Failed to send: ' + e.message);
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

async function _updateStatus(newStatus) {
  if (!_st.activeConvId) return;
  await _api(`/support/conversations/${_st.activeConvId}/status`, 'PATCH', { status: newStatus });
  const conv = _st.conversations.find(c => c.id === _st.activeConvId);
  if (conv) { conv.status = newStatus; _renderConvList(); }
}

async function _takeOver() {
  if (!_st.activeConvId) return;
  await _api(`/support/conversations/${_st.activeConvId}/status`, 'PATCH', { status: 'human' });
  const conv = _st.conversations.find(c => c.id === _st.activeConvId);
  if (conv) { conv.status = 'human'; _renderConvList(); }
}

async function _closeConv() {
  if (!_st.activeConvId) return;
  if (!confirm('Close this conversation?')) return;
  await _api(`/support/conversations/${_st.activeConvId}/status`, 'PATCH', { status: 'closed' });
  const conv = _st.conversations.find(c => c.id === _st.activeConvId);
  if (conv) { conv.status = 'closed'; _renderConvList(); }
}

async function _assignAgent(agentId) {
  if (!_st.activeConvId) return;
  await _api(`/support/conversations/${_st.activeConvId}/assign`, 'PATCH', { agent_id: agentId || null });
  await _openConv(_st.activeConvId);
}

async function _updateAgentStatus(agentId, newStatus) {
  await _api(`/support/agents/${agentId}/status`, 'PATCH', { status: newStatus });
  const a = _st.agents.find(x => x.id === agentId);
  if (a) { a.status = newStatus; _renderAgentPanel(); }
}

async function _addAgent() {
  const name  = document.getElementById('cs-ag-name')?.value.trim();
  const email = document.getElementById('cs-ag-email')?.value.trim();
  if (!name) { alert('Agent name is required.'); return; }
  const data = await _api('/support/agents', 'POST', { name, email: email || null });
  if (data) {
    _st.agents.push(data);
    _renderAgentPanel();
    _toggleAddAgentForm();
    document.getElementById('cs-ag-name').value = '';
    document.getElementById('cs-ag-email').value = '';
  }
}

function _toggleAddAgentForm() {
  document.getElementById('cs-add-agent-form')?.classList.toggle('visible');
}

function _onSearch(val) {
  _st.searchText = val;
  _loadConversations();
}

function _onFilter(val, btn) {
  _st.filterStatus = val;
  document.querySelectorAll('.cs-filter-pill').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _loadConversations();
}

function _inputKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _sendMsg(); }
}

function _inputGrow(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 100) + 'px';
}

/* ── Simulate incoming message (dev/test) ────────────────────────────────────── */
async function _simulateIncoming() {
  const phone = document.getElementById('cs-sim-phone')?.value.trim();
  const name  = document.getElementById('cs-sim-name')?.value.trim();
  const msg   = document.getElementById('cs-sim-msg')?.value.trim();
  const result = document.getElementById('cs-sim-result');
  const btn    = document.getElementById('cs-sim-btn');

  if (!phone) { _simResult('Enter a phone number.', 'error'); return; }
  if (!msg)   { _simResult('Enter a message.', 'error'); return; }

  btn.disabled = true;
  btn.textContent = 'Sending…';

  try {
    const res = await fetch(`${BASE}/support/conversations/web`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, name: name || undefined, message: msg }),
    });
    if (!res.ok) throw new Error(`Error ${res.status}`);
    _simResult('✓ Message sent — check the conversation list!', 'ok');
    document.getElementById('cs-sim-msg').value = '';
    // Reload conversations to show the new one
    setTimeout(_loadConversations, 400);
  } catch (e) {
    _simResult('Failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Send Test Message';
  }
}

function _simResult(text, type) {
  const el = document.getElementById('cs-sim-result');
  if (!el) return;
  el.style.display = 'block';
  el.style.color   = type === 'ok' ? '#16a34a' : '#ef4444';
  el.textContent   = text;
  setTimeout(() => { el.style.display = 'none'; }, 4000);
}

/* ── WebSocket ───────────────────────────────────────────────────────────────── */
function _connectWs() {
  const token = getToken();
  if (!token) return;

  const wsUrl = `${WS_BASE}/support/ws?token=${token}`;
  const ws    = new WebSocket(wsUrl);
  _st.ws      = ws;

  ws.onopen = () => {
    _st.wsRetry = 0;
    _wsStatus(true);
    // Keepalive ping every 25 s
    ws._pingInterval = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({type:'ping'})), 25000);
  };

  ws.onclose = () => {
    _wsStatus(false);
    clearInterval(ws._pingInterval);
    const delay = Math.min(1000 * 2 ** _st.wsRetry, 30000);
    _st.wsRetry++;
    setTimeout(_connectWs, delay);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = e => {
    try { _handleWsEvent(JSON.parse(e.data)); } catch {}
  };
}

function _handleWsEvent(ev) {
  switch (ev.type) {
    case 'new_message': {
      const { conversation_id: cid, message, conv_preview } = ev;
      // Update or insert conversation
      const idx = _st.conversations.findIndex(c => c.id === cid);
      if (idx >= 0) {
        _st.conversations[idx] = { ..._st.conversations[idx], ...conv_preview };
        // If not active, increment unread
        if (cid !== _st.activeConvId) {
          _st.conversations[idx].unread_count = conv_preview.unread_count;
          _updateTabBadge();
        }
      } else {
        _st.conversations.unshift(conv_preview);
      }
      _renderConvList();

      // Append to open chat
      if (cid === _st.activeConvId) {
        const msgs = document.getElementById('cs-messages');
        if (msgs) {
          const row  = document.createElement('div');
          const cls  = `cs-msg-${message.sender_type}`;
          const init = message.sender_name ? message.sender_name[0].toUpperCase() : (message.sender_type === 'user' ? 'U' : 'A');
          const label = message.sender_name || message.sender_type;
          row.className = `cs-msg ${cls}`;
          row.innerHTML = `
            <div class="cs-msg-avatar">${init}</div>
            <div>
              <div class="cs-msg-label">${_esc(label)} · ${_fmtTime(message.created_at)}</div>
              <div class="cs-msg-bubble">${_esc(message.content).replace(/\n/g,'<br>')}</div>
            </div>`;
          // Remove empty state if present
          msgs.querySelector('.cs-empty-chat')?.remove();
          msgs.appendChild(row);
          msgs.scrollTop = msgs.scrollHeight;
        }
      }

      // Load stats in background
      _loadStats();
      break;
    }

    case 'status_changed': {
      const conv = _st.conversations.find(c => c.id === ev.conversation_id);
      if (conv) { conv.status = ev.status; _renderConvList(); }
      if (ev.conversation_id === _st.activeConvId) {
        const sel = document.getElementById('cs-status-select');
        if (sel) sel.value = ev.status;
      }
      break;
    }

    case 'agent_status': {
      const agent = _st.agents.find(a => a.id === ev.agent_id);
      if (agent) { agent.status = ev.status; _renderAgentPanel(); }
      _loadStats();
      break;
    }
  }
}

function _wsStatus(connected) {
  const dot   = document.getElementById('cs-ws-dot');
  const label = document.getElementById('cs-ws-label');
  if (dot)   dot.className   = connected ? 'connected' : '';
  if (label) label.textContent = connected ? 'Live · Real-time connected' : 'Reconnecting…';
}

/* ── Tab unread badge ────────────────────────────────────────────────────────── */
function _updateTabBadge() {
  const total  = _st.conversations.reduce((n, c) => n + (c.unread_count || 0), 0);
  const badge  = document.getElementById('cs-tab-badge');
  if (!badge) return;
  if (total > 0) {
    badge.textContent = total > 99 ? '99+' : String(total);
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

/* ── API helper ──────────────────────────────────────────────────────────────── */
async function _api(path, method = 'GET', body = null) {
  const token   = getToken();
  const headers = { Authorization: `Bearer ${token}` };
  if (body) headers['Content-Type'] = 'application/json';
  try {
    const res = await fetch(`${BASE}${path}`, {
      method, headers, body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Error ${res.status}`);
    }
    if (res.status === 204) return null;
    return res.json();
  } catch (e) {
    console.error('CS API error', e);
    return null;
  }
}

/* ── Utilities ───────────────────────────────────────────────────────────────── */
function _esc(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _relTime(iso) {
  const diff = (Date.now() - new Date(iso)) / 1000;
  if (diff < 60)   return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
