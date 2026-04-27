import { api, getToken, getUser, requireAuth } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('Not authenticated');
renderLayout('Challenge Friends', 'Challenge');

const _isLocal = ['localhost', '127.0.0.1', '0.0.0.0'].includes(window.location.hostname);
const WS_BASE  = _isLocal ? 'ws://127.0.0.1:8000/api' : 'wss://www.pritis.name.ng/api';

// ── State ─────────────────────────────────────────────────────────────────────
let _ch          = null;   // {id, code, is_host, host_mode, duration_seconds, question_count, quiz_title}
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

// ── Host participation toggle ──────────────────────────────────────────────────
document.getElementById('host-join-as-participant')?.addEventListener('change', function () {
  const label = document.getElementById('host-join-label');
  if (label) {
    label.textContent = this.checked ? "Yes — I'll play too" : "No — I'll watch as spectator";
  }
});

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
  const joinAsP   = document.getElementById('host-join-as-participant')?.checked !== false;
  const hostMode  = joinAsP ? 'participant' : 'spectator';

  if (!quizId) { _toast('Please select a quiz'); return; }

  const btn = document.getElementById('host-create-btn');
  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    const ch = await api.post('/challenges', {
      quiz_id:          quizId,
      duration_seconds: duration,
      timer_mode:       timerMode,
      max_participants: maxP,
      host_mode:        hostMode,
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

  // Spectator mode banner
  const specNote = document.getElementById('wr-spectator-note');
  if (specNote) {
    specNote.style.display = (_ch.is_host && _ch.host_mode === 'spectator') ? 'block' : 'none';
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

  _ws.onerror = () => {};

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
    case 'participant_joined':   _onJoined(msg);            break;
    case 'challenge_started':    _onStarted(msg);           break;
    case 'question':             _onQuestion(msg);          break;
    case 'answer_result':        _onAnswerResult(msg);      break;
    case 'question_ended':       _onQEnded(msg);            break;
    case 'question_responses':   _onQuestionResponses(msg); break;
    case 'challenge_ended':      _onEnded(msg);             break;
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
    ${msg.is_host && msg.is_spectator ? '<span class="ch-p-badge spectator">SPECTATOR</span>' : ''}
    ${msg.is_host && !msg.is_spectator ? '<span class="ch-p-badge host">HOST</span>' : ''}
    ${isYou && !msg.is_spectator ? '<span class="ch-p-badge you">YOU</span>' : ''}
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

  // Merge host_mode from server broadcast in case it wasn't set yet
  if (msg.host_mode && _ch) _ch.host_mode = msg.host_mode;

  const isSpectator = _ch && _ch.is_host && _ch.host_mode === 'spectator';

  if (isSpectator) {
    _show('screen-spectator');
    _resetSpectator();
  } else {
    _show('screen-quiz');
    document.getElementById('q-my-score').textContent = '0';
    if (_ch && _ch.is_host) document.getElementById('ch-live-scores').style.display = 'block';
  }
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

  const durSec = Math.round((msg.duration_ms || (_ch && _ch.duration_seconds ? _ch.duration_seconds * 1000 : 30000)) / 1000);

  if (_ch && _ch.is_host && _ch.host_mode === 'spectator') {
    _onQuestionSpectator(msg.idx, durSec);
  } else {
    _onQuestionParticipant(msg.idx, durSec);
  }
}

function _onQuestionParticipant(idx, durSec) {
  document.getElementById('q-counter').textContent = `Question ${idx + 1} / ${_totalQ}`;
  document.getElementById('q-progress').style.width = `${(idx / _totalQ) * 100}%`;
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

  _startTimer(durSec, 'q-timer-val', 'q-timer');
}

function _onQuestionSpectator(idx, durSec) {
  // Reset for new question
  document.getElementById('sp-responses').style.display = 'none';
  document.getElementById('sp-waiting').style.display = 'block';

  document.getElementById('sp-q-counter').textContent = `Question ${idx + 1} / ${_totalQ}`;
  document.getElementById('sp-progress').style.width = `${(idx / _totalQ) * 100}%`;
  document.getElementById('sp-q-text').textContent = _currentQ.text;

  // Render options read-only
  const optWrap = document.getElementById('sp-q-options');
  optWrap.innerHTML = '';
  const letters = ['A', 'B', 'C', 'D'];
  if (_currentQ.type === 'mcq') {
    (_currentQ.options || []).forEach((opt, i) => {
      const div = document.createElement('div');
      div.className  = 'ch-sp-option';
      div.id         = `sp-opt-${i}`;
      div.dataset.val = opt;
      div.innerHTML  = `<span class="ch-sp-option-letter">${letters[i] || i + 1}</span>${_esc(opt)}`;
      optWrap.appendChild(div);
    });
  }

  _startTimer(durSec, 'sp-timer-val', 'sp-q-timer');
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

  const isSpectator = _ch && _ch.is_host && _ch.host_mode === 'spectator';

  if (isSpectator) {
    // Highlight correct option in spectator view
    document.querySelectorAll('.ch-sp-option').forEach(el => {
      if (el.dataset.val === correct) el.classList.add('correct');
    });
    document.getElementById('sp-waiting').style.display = 'none';
    // Responses will arrive via question_responses message shortly
    if (msg.scores) _updateSpectatorLeaderboard(msg.scores);
  } else {
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

    if (!_answered) {
      const fb = document.getElementById('q-feedback');
      fb.className  = 'ch-feedback wrong';
      fb.textContent = `⏱ Time's up! The answer was: ${_esc(correct)}`;
    }

    if (_ch && _ch.is_host && msg.scores) _updateLiveScores(msg.scores);
  }
}

// ── question_responses (spectator host only) ───────────────────────────────────
function _onQuestionResponses(msg) {
  const panel   = document.getElementById('sp-responses');
  const list    = document.getElementById('sp-resp-list');
  const correctTag = document.getElementById('sp-correct-tag');
  if (!panel || !list) return;

  if (correctTag) correctTag.textContent = `✓ Correct: ${_esc(msg.correct_answer || '')}`;

  const sorted = (msg.responses || []).slice().sort((a, b) => {
    // Correct first, then by response time
    if (a.is_correct && !b.is_correct) return -1;
    if (!a.is_correct && b.is_correct) return 1;
    if (a.answer == null && b.answer != null) return 1;
    if (a.answer != null && b.answer == null) return -1;
    return (a.response_time_ms || Infinity) - (b.response_time_ms || Infinity);
  });

  list.innerHTML = sorted.map(r => {
    const cls      = r.answer == null ? 'no-ans' : (r.is_correct ? 'correct' : 'wrong');
    const icon     = r.answer == null ? '—' : (r.is_correct ? '✓' : '✗');
    const timeStr  = r.response_time_ms != null ? `${(r.response_time_ms / 1000).toFixed(1)}s` : 'No answer';
    const ansDisp  = r.answer != null ? _esc(r.answer) : 'No answer';
    return `
      <div class="ch-resp-item ${cls}">
        <div class="ch-resp-av">${_esc((r.name || '?')[0].toUpperCase())}</div>
        <div class="ch-resp-name">${_esc(r.name)}</div>
        <div class="ch-resp-ans">${icon} ${ansDisp}</div>
        <div class="ch-resp-time">${timeStr}</div>
        <div class="ch-resp-pts">+${r.points || 0}</div>
      </div>
    `;
  }).join('');

  panel.style.display = 'block';
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
function _startTimer(seconds, timerValId = 'q-timer-val', timerWrapperId = 'q-timer') {
  let remaining   = seconds;
  const timerEl   = document.getElementById(timerValId);
  const timerWrap = document.getElementById(timerWrapperId);

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

// ── Live scores sidebar (participant host) ─────────────────────────────────────
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

// ── Spectator leaderboard sidebar ─────────────────────────────────────────────
function _updateSpectatorLeaderboard(scores) {
  const list = document.getElementById('sp-lb-list');
  if (!list) return;
  list.innerHTML = scores.slice(0, 15).map((s, i) => `
    <div class="ch-sp-ls-item">
      <div class="ch-sp-ls-rank">${i + 1}</div>
      <div class="ch-sp-ls-av">${_esc((s.name || '?')[0].toUpperCase())}</div>
      <div class="ch-sp-ls-name">${_esc(s.name)}</div>
      <div class="ch-sp-ls-score">${s.score}</div>
    </div>
  `).join('');
}

// ── Reset spectator screen ─────────────────────────────────────────────────────
function _resetSpectator() {
  document.getElementById('sp-q-text').textContent = 'Waiting for first question…';
  document.getElementById('sp-q-options').innerHTML = '';
  document.getElementById('sp-responses').style.display = 'none';
  document.getElementById('sp-waiting').style.display = 'none';
  document.getElementById('sp-lb-list').innerHTML = `
    <div style="font-size:.78rem;color:var(--text-muted);text-align:center;padding:16px 0">No scores yet</div>
  `;
  const timerEl = document.getElementById('sp-timer-val');
  if (timerEl) timerEl.textContent = '—';
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
    list.innerHTML = data.map(ch => {
      const roleLabel = ch.is_host ? (ch.host_mode === 'spectator' ? 'Host (Spectator)' : 'Host') : 'Participant';
      return `
        <div class="ch-recent-item" onclick="openRecent('${ch.id}')">
          <div class="ch-ri-info">
            <div class="ch-ri-title">${_esc(ch.quiz_title)}</div>
            <div class="ch-ri-sub">${roleLabel} · ${ch.participants} joined · Code: ${ch.code}</div>
          </div>
          <span class="ch-status-pill ${statusCls[ch.status] || ''}">${ch.status}</span>
        </div>
      `;
    }).join('');
  } catch {
    section.style.display = 'none';
  }
}

window.openRecent = async function (id) {
  try {
    const ch = await api.get(`/challenges/${id}`);
    if (ch.status === 'completed') {
      _ch = { id: ch.id, is_host: ch.is_host, quiz_title: ch.quiz_title, code: ch.code, host_mode: ch.host_mode };
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
        host_mode:        ch.host_mode || 'participant',
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
