import { api, getToken, getUser, requireAuth } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('Not authenticated');
renderLayout('Challenge Friends', 'Challenge');

const _isLocal = ['localhost', '127.0.0.1', '0.0.0.0'].includes(window.location.hostname);
const WS_BASE  = _isLocal ? 'ws://127.0.0.1:8000/api' : 'wss://www.pritis.name.ng/api';

// ── State ─────────────────────────────────────────────────────────────────────
let _ch          = null;   // {id, code, is_host, duration_seconds, question_count, quiz_title}
let _ws          = null;
let _timerInt    = null;
let _myScore     = 0;
let _questionStartTs = 0;
let _currentQ    = null;   // {id, text, type, options}
let _currentQIdx = 0;
let _answered    = false;
let _totalQ      = 0;

const _me   = getUser();
const _myId = _me ? String(_me.id) : '';

// ── Screen helpers ─────────────────────────────────────────────────────────────
function _show(screenId) {
  document.querySelectorAll('.ch-screen').forEach(s => s.classList.remove('active'));
  document.getElementById(screenId).classList.add('active');
}

window.showLanding = function () {
  _closeWs();
  _ch = null;
  _show('screen-landing');
  _loadRecent();
};

window.showHostCreate = function () {
  _show('screen-host-create');
  _loadQuizzes();
};

window.showJoin = function () {
  _show('screen-join');
  document.getElementById('join-code-input').value = '';
};

// ── Load quizzes for host form ─────────────────────────────────────────────────
async function _loadQuizzes() {
  const sel = document.getElementById('host-quiz-select');
  sel.innerHTML = '<option value="">Loading…</option>';
  try {
    const data    = await api.get('/quizzes');
    const quizzes = Array.isArray(data) ? data : (data.quizzes || []);
    if (!quizzes.length) {
      sel.innerHTML = '<option value="">You have no quizzes yet</option>';
      return;
    }
    sel.innerHTML = quizzes.map(q =>
      `<option value="${q.id}">${_esc(q.title)}</option>`
    ).join('');
  } catch {
    sel.innerHTML = '<option value="">Failed to load quizzes</option>';
  }
}

// ── Create challenge ───────────────────────────────────────────────────────────
window.createChallenge = async function () {
  const quizId    = document.getElementById('host-quiz-select').value;
  const timerMode = document.getElementById('host-timer-mode').value;
  const duration  = parseInt(document.getElementById('host-duration').value, 10) || 30;
  const maxPVal   = document.getElementById('host-max-p').value;
  const maxP      = maxPVal ? (parseInt(maxPVal, 10) || null) : null;

  if (!quizId) { _toast('Please select a quiz'); return; }

  const btn = document.getElementById('host-create-btn');
  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    const ch = await api.post('/challenges', {
      quiz_id:          quizId,
      duration_seconds: duration,
      timer_mode:       timerMode,
      max_participants: maxP,
    });
    _ch = ch; _myScore = 0;
    _enterWaiting();
  } catch (e) {
    _toast(e.message || 'Failed to create challenge');
  } finally {
    btn.disabled = false; btn.textContent = 'Create Challenge';
  }
};

// ── Join challenge ─────────────────────────────────────────────────────────────
window.joinChallenge = async function () {
  const code = document.getElementById('join-code-input').value.trim().toUpperCase();
  if (code.length < 3) { _toast('Enter a valid code'); return; }

  const btn = document.getElementById('join-btn');
  btn.disabled = true; btn.textContent = 'Joining…';
  try {
    const ch = await api.post('/challenges/join', { code });
    _ch = ch; _myScore = 0;
    _enterWaiting();
  } catch (e) {
    _toast(e.message || 'Could not join challenge');
  } finally {
    btn.disabled = false; btn.textContent = 'Join';
  }
};

// ── Waiting room ───────────────────────────────────────────────────────────────
function _enterWaiting() {
  _show('screen-waiting');

  // Code box (host only)
  const codeSection = document.getElementById('wr-code-section');
  if (_ch.is_host) {
    codeSection.innerHTML = `
      <div class="ch-code-box">
        <div class="ch-code-lbl">Challenge Code</div>
        <div class="ch-code-val">${_esc(_ch.code)}</div>
        <div class="ch-code-copy" onclick="copyCode()">Click to copy</div>
      </div>
    `;
  } else {
    codeSection.innerHTML = '';
  }

  document.getElementById('wr-quiz-info').textContent =
    `Quiz: ${_ch.quiz_title}  ·  ${_ch.question_count} questions  ·  ${_ch.duration_seconds}s per question`;

  document.getElementById('wr-host-actions').style.display    = _ch.is_host ? 'block' : 'none';
  document.getElementById('wr-participant-msg').style.display = _ch.is_host ? 'none'  : 'block';
  document.getElementById('wr-start-btn').disabled = false;
  document.getElementById('wr-start-btn').textContent = '▶ Start Challenge';

  document.getElementById('wr-participants').innerHTML = `
    <div style="font-size:.82rem;color:var(--text-muted);text-align:center;padding:16px">
      Waiting for participants…
    </div>
  `;
  document.getElementById('wr-p-count').textContent = '0 joined';
  document.getElementById('wr-ws-status').innerHTML = `
    <span class="ch-dot"></span><span class="ch-dot"></span><span class="ch-dot"></span>
    <span>Connecting…</span>
  `;

  _connectWs();
}

window.copyCode = function () {
  if (!_ch) return;
  navigator.clipboard.writeText(_ch.code).then(() => _toast('Code copied!'));
};

// ── Start (host) ───────────────────────────────────────────────────────────────
window.startChallenge = async function () {
  const btn = document.getElementById('wr-start-btn');
  btn.disabled = true; btn.textContent = 'Starting…';
  try {
    await api.post(`/challenges/${_ch.id}/start`, {});
    // WS challenge_started event triggers screen change
  } catch (e) {
    _toast(e.message || 'Failed to start');
    btn.disabled = false; btn.textContent = '▶ Start Challenge';
  }
};

// ── WebSocket ──────────────────────────────────────────────────────────────────
let _pingInterval = null;

function _connectWs() {
  _closeWs();
  const url = `${WS_BASE}/challenges/${_ch.id}/ws?token=${encodeURIComponent(getToken())}`;
  _ws = new WebSocket(url);

  _ws.onopen = () => {
    document.getElementById('wr-ws-status').innerHTML =
      `<span style="color:#22c55e;font-size:.8rem;font-weight:600">✓ Connected</span>`;
    _pingInterval = setInterval(() => {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify({ type: 'ping' }));
      } else {
        clearInterval(_pingInterval);
      }
    }, 25000);
  };

  _ws.onmessage = (e) => {
    try { _handle(JSON.parse(e.data)); } catch {}
  };

  _ws.onerror = () => {};  // errors always precede onclose; handled there

  _ws.onclose = (e) => {
    clearInterval(_pingInterval);
    const statusEl = document.getElementById('wr-ws-status');
    if (statusEl) statusEl.innerHTML =
      `<span style="color:#ef4444;font-size:.8rem">Disconnected (code ${e.code})</span>`;
  };
}

function _closeWs() {
  clearInterval(_pingInterval);
  _clearTimer();
  if (_ws) { try { _ws.close(); } catch {} _ws = null; }
}

// ── Message dispatcher ─────────────────────────────────────────────────────────
function _handle(msg) {
  switch (msg.type) {
    case 'participant_joined': _onJoined(msg);       break;
    case 'challenge_started':  _onStarted(msg);      break;
    case 'question':           _onQuestion(msg);     break;
    case 'answer_result':      _onAnswerResult(msg); break;
    case 'question_ended':     _onQEnded(msg);       break;
    case 'challenge_ended':    _onEnded(msg);        break;
  }
}

// ── participant_joined ─────────────────────────────────────────────────────────
function _onJoined(msg) {
  const list  = document.getElementById('wr-participants');
  const count = document.getElementById('wr-p-count');
  if (!list) return;

  // Remove "waiting" placeholder
  const placeholder = list.querySelector('div[style]');
  if (placeholder) placeholder.remove();

  const isYou = msg.user_id === _myId;
  const div   = document.createElement('div');
  div.className = 'ch-participant-item';
  div.id        = `wr-p-${msg.user_id}`;
  div.innerHTML = `
    <div class="ch-p-avatar">${_esc((msg.avatar || msg.name || '?')[0].toUpperCase())}</div>
    <span class="ch-p-name">${_esc(msg.name)}</span>
    ${msg.is_host ? '<span class="ch-p-badge host">HOST</span>' : ''}
    ${isYou       ? '<span class="ch-p-badge you">YOU</span>'   : ''}
  `;

  const existing = document.getElementById(`wr-p-${msg.user_id}`);
  if (existing) existing.replaceWith(div);
  else list.appendChild(div);

  if (count) count.textContent = `${msg.count} joined`;
}

// ── challenge_started ──────────────────────────────────────────────────────────
function _onStarted(msg) {
  _totalQ  = msg.total_questions || _ch.question_count || 0;
  _myScore = 0;
  _show('screen-quiz');
  document.getElementById('q-my-score').textContent = '0';
  if (_ch.is_host) document.getElementById('ch-live-scores').style.display = 'block';
  _toast('Quiz starting!');
}

// ── question ───────────────────────────────────────────────────────────────────
function _onQuestion(msg) {
  _clearTimer();
  _currentQ        = msg.question;
  _currentQIdx     = msg.idx;
  _answered        = false;
  _questionStartTs = Date.now();
  _totalQ          = msg.total || _totalQ;

  const durSec = Math.round((msg.duration_ms || _ch.duration_seconds * 1000) / 1000);

  document.getElementById('q-counter').textContent = `Question ${msg.idx + 1} / ${_totalQ}`;
  document.getElementById('q-progress').style.width = `${(msg.idx / _totalQ) * 100}%`;
  document.getElementById('q-text').textContent = _currentQ.text;

  const fb = document.getElementById('q-feedback');
  fb.className = 'ch-feedback'; fb.textContent = '';

  const optWrap = document.getElementById('q-options');
  optWrap.innerHTML = '';

  if (_currentQ.type === 'short_answer') {
    optWrap.innerHTML = `
      <input type="text" class="ch-short-input" id="q-short-input" placeholder="Type your answer…">
      <div style="margin-top:10px">
        <button class="ch-btn primary" onclick="submitShort()">Submit Answer</button>
      </div>
    `;
    setTimeout(() => document.getElementById('q-short-input')?.focus(), 100);
  } else {
    const letters = ['A', 'B', 'C', 'D'];
    (_currentQ.options || []).forEach((opt, i) => {
      const btn      = document.createElement('button');
      btn.className  = 'ch-option';
      btn.dataset.val = opt;
      btn.innerHTML   = `<span class="ch-option-letter">${letters[i] || i + 1}</span>${_esc(opt)}`;
      btn.onclick     = () => submitAnswer(opt);
      optWrap.appendChild(btn);
    });
  }

  _startTimer(durSec);
}

window.submitShort = function () {
  const inp = document.getElementById('q-short-input');
  if (!inp || !inp.value.trim()) return;
  submitAnswer(inp.value.trim());
};

function submitAnswer(answer) {
  if (_answered || !_currentQ) return;
  _answered = true;

  const responseMs = Date.now() - _questionStartTs;

  document.querySelectorAll('.ch-option').forEach(btn => {
    btn.disabled = true;
    if (btn.dataset.val === answer) btn.classList.add('selected');
  });
  const shortInp = document.getElementById('q-short-input');
  if (shortInp) shortInp.disabled = true;

  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({
      type:             'answer',
      question_id:      _currentQ.id,
      question_idx:     _currentQIdx,
      answer,
      response_time_ms: responseMs,
    }));
  }
}

// ── answer_result ──────────────────────────────────────────────────────────────
function _onAnswerResult(msg) {
  _myScore = msg.your_score || 0;
  document.getElementById('q-my-score').textContent = _myScore;

  const fb = document.getElementById('q-feedback');
  if (msg.correct) {
    fb.className  = 'ch-feedback correct';
    fb.textContent = `✓ Correct! +${msg.points} points`;
  } else {
    fb.className  = 'ch-feedback wrong';
    fb.textContent = `✗ Wrong. +0 points`;
    document.querySelectorAll('.ch-option.selected').forEach(el => {
      el.classList.remove('selected');
      el.classList.add('wrong');
    });
  }
}

// ── question_ended ─────────────────────────────────────────────────────────────
function _onQEnded(msg) {
  _clearTimer();
  const correct = msg.correct_answer;

  document.querySelectorAll('.ch-option').forEach(btn => {
    btn.disabled = true;
    if (btn.dataset.val === correct) {
      btn.classList.remove('wrong', 'selected');
      btn.classList.add('correct');
    } else if (btn.classList.contains('selected')) {
      btn.classList.remove('selected');
      btn.classList.add('wrong');
    }
  });

  // Time's up feedback (only if not answered)
  if (!_answered) {
    const fb = document.getElementById('q-feedback');
    fb.className  = 'ch-feedback wrong';
    fb.textContent = `⏱ Time's up! The answer was: ${_esc(correct)}`;
  }

  if (_ch.is_host && msg.scores) _updateLiveScores(msg.scores);
}

// ── challenge_ended ────────────────────────────────────────────────────────────
function _onEnded(msg) {
  _clearTimer();
  _show('screen-leaderboard');

  const rankEmojis = ['🥇', '🥈', '🥉'];
  const topCls     = ['top1', 'top2', 'top3'];

  if (msg.is_host) {
    document.getElementById('lb-subtitle').textContent   = 'Final leaderboard';
    document.getElementById('lb-your-result').style.display = 'none';
    document.getElementById('lb-section-title').textContent = 'Final Rankings';
    document.getElementById('lb-list').innerHTML = (msg.leaderboard || []).map((e, i) => {
      const isYou = e.user_id === _myId;
      return `
        <div class="ch-lb-item ${topCls[i] || ''} ${isYou ? 'you' : ''}">
          <div class="ch-lb-rank">${rankEmojis[i] || e.rank}</div>
          <div class="ch-lb-av">${_esc((e.name || '?')[0].toUpperCase())}</div>
          <div class="ch-lb-name">${_esc(e.name)}${isYou ? ' <span style="color:var(--primary);font-size:.7rem">(you)</span>' : ''}</div>
          <div class="ch-lb-score">${e.score}</div>
        </div>
      `;
    }).join('');
  } else {
    document.getElementById('lb-subtitle').textContent   = 'Quiz complete!';
    document.getElementById('lb-your-result').style.display = 'block';
    document.getElementById('lb-your-rank').textContent  = `#${msg.your_rank || '?'}`;
    document.getElementById('lb-your-score').textContent = `${msg.your_score || 0} points`;
    document.getElementById('lb-section-title').textContent = 'Top 3';
    document.getElementById('lb-list').innerHTML = (msg.top3 || []).map((e, i) => {
      const isYou = e.user_id === _myId;
      return `
        <div class="ch-lb-item ${topCls[i] || ''} ${isYou ? 'you' : ''}">
          <div class="ch-lb-rank">${rankEmojis[i] || e.rank}</div>
          <div class="ch-lb-av">${_esc((e.name || '?')[0].toUpperCase())}</div>
          <div class="ch-lb-name">${_esc(e.name)}${isYou ? ' <span style="color:var(--primary);font-size:.7rem">(you)</span>' : ''}</div>
          <div class="ch-lb-score">${e.score}</div>
        </div>
      `;
    }).join('');
  }
}

// ── View full results (button) ─────────────────────────────────────────────────
window.reloadChallenge = async function () {
  if (!_ch) return;
  try {
    const lb = await api.get(`/challenges/${_ch.id}/leaderboard`);
    _onEnded({ ...lb, is_host: _ch.is_host });
  } catch (e) {
    _toast(e.message || 'Failed to load results');
  }
};

// ── Timer countdown ────────────────────────────────────────────────────────────
function _startTimer(seconds) {
  let remaining   = seconds;
  const timerEl   = document.getElementById('q-timer-val');
  const timerWrap = document.getElementById('q-timer');

  const _tick = () => {
    if (timerEl)   timerEl.textContent = remaining;
    if (timerWrap) timerWrap.classList.toggle('danger', remaining <= 5);
  };
  _tick();
  _timerInt = setInterval(() => {
    remaining--;
    _tick();
    if (remaining <= 0) _clearTimer();
  }, 1000);
}

function _clearTimer() {
  if (_timerInt) { clearInterval(_timerInt); _timerInt = null; }
}

// ── Live scores sidebar (host) ─────────────────────────────────────────────────
function _updateLiveScores(scores) {
  const list = document.getElementById('ch-ls-list');
  if (!list) return;
  list.innerHTML = scores.slice(0, 10).map(s => `
    <div class="ch-ls-item">
      <div class="ch-ls-av">${_esc((s.name || '?')[0].toUpperCase())}</div>
      <div class="ch-ls-name">${_esc(s.name)}</div>
      <div class="ch-ls-score">${s.score}</div>
    </div>
  `).join('');
}

// ── Recent challenges ──────────────────────────────────────────────────────────
async function _loadRecent() {
  const section = document.getElementById('ch-recent-section');
  const list    = document.getElementById('ch-recent-list');
  try {
    const data = await api.get('/challenges/my');
    if (!data || !data.length) { section.style.display = 'none'; return; }
    section.style.display = 'block';
    const statusCls = { waiting: 'ch-status-waiting', active: 'ch-status-active', completed: 'ch-status-completed' };
    list.innerHTML = data.map(ch => `
      <div class="ch-recent-item" onclick="openRecent('${ch.id}')">
        <div class="ch-ri-info">
          <div class="ch-ri-title">${_esc(ch.quiz_title)}</div>
          <div class="ch-ri-sub">${ch.is_host ? 'Host' : 'Participant'} · ${ch.participants} joined · Code: ${ch.code}</div>
        </div>
        <span class="ch-status-pill ${statusCls[ch.status] || ''}">${ch.status}</span>
      </div>
    `).join('');
  } catch {
    section.style.display = 'none';
  }
}

window.openRecent = async function (id) {
  try {
    const ch = await api.get(`/challenges/${id}`);
    if (ch.status === 'completed') {
      _ch = { id: ch.id, is_host: ch.is_host, quiz_title: ch.quiz_title, code: ch.code };
      const lb = await api.get(`/challenges/${id}/leaderboard`);
      _onEnded({ ...lb, is_host: ch.is_host });
    } else if (ch.status === 'waiting') {
      _ch = {
        id:               ch.id,
        code:             ch.code,
        is_host:          ch.is_host,
        quiz_title:       ch.quiz_title,
        duration_seconds: ch.duration_seconds,
        question_count:   ch.question_count,
      };
      _myScore = 0;
      _enterWaiting();
    } else {
      _toast('Challenge is already active — cannot rejoin mid-game.');
    }
  } catch (e) {
    _toast(e.message || 'Failed to open challenge');
  }
};

// ── Utilities ──────────────────────────────────────────────────────────────────
function _toast(msg) {
  const el = document.getElementById('ch-toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2800);
}

function _esc(str) {
  return String(str || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Boot ───────────────────────────────────────────────────────────────────────
_loadRecent();
