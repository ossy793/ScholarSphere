import { api, requireAuth, getToken } from './api.js';
import { renderLayout } from './layout.js';

// Support both authenticated and shared (public) quizzes
const params = new URLSearchParams(window.location.search);
const quizId    = params.get('quiz');
const shareToken = params.get('share');
const isShared  = !!shareToken && !quizId;

if (!isShared) {
  if (!requireAuth()) throw new Error('unauthenticated');
  renderLayout('Quiz Practice', 'My Questions');
} else {
  // Minimal layout for shared quizzes
  document.getElementById('sidebar').style.display = 'none';
  document.querySelector('.main-content').style.marginLeft = '0';
}

// ── State ──
let quiz       = null;
let questions  = [];
let answers    = {};     // { questionId: userAnswer }
let currentIdx = 0;
let mode       = 'exam'; // 'exam' | 'practice'
let timerInterval = null;
let secondsLeft  = 0;
let startTime    = null;
let attemptId    = null;

// ── Load quiz ──
async function loadQuiz() {
  try {
    if (isShared) {
      const data = await fetch(`http://localhost:8000/api/quizzes/shared/${shareToken}`).then(r => r.json());
      quiz = data;
      questions = data.questions;
      // Shared quizzes only get practice mode, no score submission
      mode = 'practice';
      document.getElementById('setup-screen').querySelector('[onclick="setMode(\'exam\', this)"]')?.setAttribute('disabled', true);
    } else {
      quiz = await api.get(`/quizzes/${quizId}`);
      const qs = await api.get(`/quizzes/${quizId}/questions`);
      questions = qs;
    }

    if (questions.length === 0) {
      document.getElementById('setup-alert').textContent = 'This quiz has no questions yet.';
      document.getElementById('setup-alert').classList.remove('hidden');
      document.getElementById('start-btn').disabled = true;
      return;
    }

    document.getElementById('quiz-title-display').textContent = quiz.title;
    document.getElementById('quiz-desc-display').textContent = quiz.description || '';
    document.getElementById('q-count-display').textContent = `${questions.length} question${questions.length !== 1 ? 's' : ''}`;

  } catch (err) {
    document.getElementById('setup-alert').textContent = 'Failed to load quiz: ' + err.message;
    document.getElementById('setup-alert').classList.remove('hidden');
  }
}

loadQuiz();

// ── Mode & timer ──
window.setMode = function(m, btn) {
  mode = m;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
};

// ── Start ──
window.startQuiz = async function() {
  if (questions.length === 0) return;

  const timerEnabled = document.getElementById('timer-enabled').checked;
  const timerMins    = parseInt(document.getElementById('timer-minutes').value) || 15;
  secondsLeft = timerEnabled ? timerMins * 60 : 0;

  // Create attempt record (authenticated only)
  if (!isShared) {
    try {
      const attempt = await api.post('/attempts', { quiz_id: quiz.id, mode });
      attemptId = attempt.id;
    } catch (err) {
      console.warn('Could not create attempt record:', err.message);
    }
  }

  answers    = {};
  currentIdx = 0;
  startTime  = Date.now();

  document.getElementById('setup-screen').classList.add('hidden');
  document.getElementById('quiz-screen').classList.remove('hidden');
  document.getElementById('active-quiz-title').textContent = quiz.title;
  document.getElementById('mode-label').textContent = mode === 'exam' ? 'Exam Mode' : 'Practice Mode';

  if (timerEnabled && secondsLeft > 0) {
    document.getElementById('timer-container').classList.remove('hidden');
    updateTimerDisplay();
    timerInterval = setInterval(tickTimer, 1000);
  } else {
    document.getElementById('timer-container').classList.add('hidden');
  }

  renderQuestion();
};

function tickTimer() {
  secondsLeft--;
  updateTimerDisplay();
  const timerEl = document.getElementById('timer-container');
  if (secondsLeft <= 60)  timerEl.classList.add('warning');
  if (secondsLeft <= 10)  timerEl.classList.add('danger');
  if (secondsLeft <= 0)   { clearInterval(timerInterval); submitQuiz(); }
}

function updateTimerDisplay() {
  const m = Math.floor(secondsLeft / 60).toString().padStart(2, '0');
  const s = (secondsLeft % 60).toString().padStart(2, '0');
  document.getElementById('timer-text').textContent = `${m}:${s}`;
}

// ── Render question ──
function renderQuestion() {
  const q    = questions[currentIdx];
  const total = questions.length;
  const pct  = ((currentIdx + 1) / total) * 100;

  document.getElementById('progress-fill').style.width = `${pct}%`;
  document.getElementById('progress-label').textContent = `${currentIdx + 1} / ${total}`;
  document.getElementById('q-progress-label').textContent = `Q${currentIdx + 1} of ${total}`;

  // Nav buttons
  document.getElementById('prev-btn').disabled = currentIdx === 0;
  const isLast = currentIdx === total - 1;
  document.getElementById('next-btn').classList.toggle('hidden', isLast);
  document.getElementById('skip-btn').classList.toggle('hidden', !isLast);
  document.getElementById('submit-btn').classList.toggle('hidden', !isLast);

  const container = document.getElementById('question-container');
  const savedAnswer = answers[q.id];
  const isPracticeAnswered = mode === 'practice' && savedAnswer !== undefined;

  let optionsHtml = '';

  if (q.question_type === 'mcq' || q.question_type === 'true_false') {
    const opts = q.options || (q.question_type === 'true_false' ? ['True', 'False'] : []);
    optionsHtml = `<div class="options-list">
      ${opts.map((opt, i) => {
        let cls = '';
        if (isPracticeAnswered) {
          if (opt === q.correct_answer) cls = 'correct';
          else if (opt === savedAnswer) cls = 'wrong';
        } else if (opt === savedAnswer) {
          cls = 'selected';
        }
        const letter = String.fromCharCode(65 + i);
        return `<div class="option-item ${cls}" onclick="${isPracticeAnswered ? '' : `selectOption('${escJs(q.id)}', '${escJs(opt)}')`}">
          <div class="option-letter">${letter}</div>
          <span>${escHtml(opt)}</span>
        </div>`;
      }).join('')}
    </div>`;

    if (isPracticeAnswered && q.explanation) {
      optionsHtml += `<div class="explanation-box visible">💡 ${escHtml(q.explanation)}</div>`;
    }

  } else {
    // Short answer
    const val = savedAnswer || '';
    const disabled = isPracticeAnswered ? 'disabled' : '';
    optionsHtml = `
      <input type="text" class="short-answer-input" id="sa-input-${q.id}"
        value="${escHtml(val)}" placeholder="Type your answer…" ${disabled}
        oninput="saveSAAnswer('${escJs(q.id)}', this.value)">
      ${isPracticeAnswered ? `
        <div class="explanation-box visible" style="margin-top:10px">
          ✅ Expected: <strong>${escHtml(q.correct_answer)}</strong>
          ${q.explanation ? `<br>💡 ${escHtml(q.explanation)}` : ''}
        </div>` : ''}
    `;
  }

  container.innerHTML = `
    <div class="question-card">
      <div class="question-number">Question ${currentIdx + 1} of ${total}</div>
      <div class="question-text">${escHtml(q.question_text)}</div>
      ${optionsHtml}
    </div>
  `;
}

window.selectOption = function(qId, opt) {
  answers[qId] = opt;
  if (mode === 'practice') renderQuestion(); // show instant feedback
  else renderQuestion(); // re-render to show selection
};

window.saveSAAnswer = function(qId, val) {
  answers[qId] = val;
};

window.navigate = function(dir) {
  // In practice mode: checking short answer on next
  const q = questions[currentIdx];
  if (mode === 'practice' && q.question_type === 'short_answer' && answers[q.id] === undefined) {
    const input = document.getElementById(`sa-input-${q.id}`);
    if (input) answers[q.id] = input.value.trim();
    renderQuestion(); // show feedback
    return;
  }
  const newIdx = currentIdx + dir;
  if (newIdx < 0 || newIdx >= questions.length) return;
  currentIdx = newIdx;
  renderQuestion();
};

// ── Submit ──
window.submitQuiz = async function() {
  clearInterval(timerInterval);

  // Capture any short answer that hasn't been saved
  const q = questions[currentIdx];
  if (q && q.question_type === 'short_answer') {
    const input = document.getElementById(`sa-input-${q.id}`);
    if (input && answers[q.id] === undefined) answers[q.id] = input.value.trim();
  }

  const timeTaken = Math.floor((Date.now() - startTime) / 1000);

  const answerPayload = questions.map(q => ({
    question_id: q.id,
    user_answer: answers[q.id] ?? null,
  }));

  let score = null;
  let correctCount = 0;

  if (!isShared && attemptId) {
    try {
      const result = await api.put(`/attempts/${attemptId}/submit`, {
        answers: answerPayload,
        time_taken_seconds: timeTaken,
      });
      score = result.score;
      correctCount = result.correct_answers;
    } catch (err) {
      console.warn('Submit error:', err.message);
      // Calculate locally as fallback
      correctCount = questions.filter(q =>
        (answers[q.id] || '').trim().toLowerCase() === q.correct_answer.trim().toLowerCase()
      ).length;
      score = Math.round((correctCount / questions.length) * 100);
    }
  } else {
    // Local scoring
    correctCount = questions.filter(q =>
      (answers[q.id] || '').trim().toLowerCase() === q.correct_answer.trim().toLowerCase()
    ).length;
    score = Math.round((correctCount / questions.length) * 100);
  }

  showResults(score, correctCount, timeTaken);
};

function showResults(score, correctCount, timeTaken) {
  document.getElementById('quiz-screen').classList.add('hidden');
  document.getElementById('results-screen').classList.remove('hidden');

  const pct = score ?? 0;
  const scoreEl = document.getElementById('results-score-display');
  scoreEl.textContent = `${pct}%`;
  scoreEl.className = 'results-score';
  if (pct >= 80) scoreEl.classList.add('excellent');
  else if (pct >= 60) scoreEl.classList.add('good');
  else if (pct >= 40) scoreEl.classList.add('average');
  else scoreEl.classList.add('poor');

  const labels = { excellent: 'Excellent!', good: 'Good job!', average: 'Keep practicing!', poor: 'Need more practice.' };
  const cls = pct >= 80 ? 'excellent' : pct >= 60 ? 'good' : pct >= 40 ? 'average' : 'poor';
  document.getElementById('results-label-text').textContent = labels[cls];

  document.getElementById('res-correct').textContent = correctCount;
  document.getElementById('res-wrong').textContent = questions.length - correctCount;
  const m = Math.floor(timeTaken / 60);
  const s = timeTaken % 60;
  document.getElementById('res-time').textContent = m > 0 ? `${m}m ${s}s` : `${s}s`;

  // Answer review
  if (mode === 'exam') {
    const reviewHtml = questions.map((q, i) => {
      const userAns = answers[q.id] ?? '(no answer)';
      const correct = userAns.trim().toLowerCase() === q.correct_answer.trim().toLowerCase();
      return `
        <div class="card mb-3" style="padding:14px;border-left:4px solid ${correct ? 'var(--success)' : 'var(--danger)'}">
          <p class="text-sm font-semibold mb-2">${i + 1}. ${escHtml(q.question_text)}</p>
          <p class="text-xs">Your answer: <strong style="color:${correct ? 'var(--success)' : 'var(--danger)'}">${escHtml(userAns)}</strong></p>
          ${!correct ? `<p class="text-xs">Correct: <strong style="color:var(--success)">${escHtml(q.correct_answer)}</strong></p>` : ''}
          ${q.explanation ? `<p class="text-xs text-muted mt-1">💡 ${escHtml(q.explanation)}</p>` : ''}
        </div>
      `;
    }).join('');
    document.getElementById('answer-review').innerHTML = `
      <h3 class="font-bold mb-3">Answer Review</h3>${reviewHtml}`;
  }
}

window.restartQuiz = function() {
  answers    = {};
  currentIdx = 0;
  attemptId  = null;
  clearInterval(timerInterval);
  document.getElementById('results-screen').classList.add('hidden');
  document.getElementById('setup-screen').classList.remove('hidden');
};

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function escJs(str) {
  return String(str).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
}
