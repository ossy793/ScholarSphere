import { api, requireAuth, showUpgradeModal } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('unauthenticated');
renderLayout('AI Generate Questions', 'AI Generate');

// ── Output format / type sync ──────────────────────────────────────────────────
function _syncOutputFormat() {
  const type = document.querySelector('input[name="question-type"]:checked')?.value || 'mcq';
  const fmtInteractive = document.getElementById('fmt-interactive');
  const pdfRadio = document.querySelector('input[name="rag-format"][value="pdf"]');
  const answerGroup = document.getElementById('answer-placement-group');

  if (type !== 'mcq') {
    // Short answer / Theory — PDF only
    if (fmtInteractive) fmtInteractive.style.display = 'none';
    if (pdfRadio) pdfRadio.checked = true;
    if (answerGroup) answerGroup.style.display = 'block';
  } else {
    if (fmtInteractive) fmtInteractive.style.display = '';
    const isPdf = document.querySelector('input[name="rag-format"]:checked')?.value === 'pdf';
    if (answerGroup) answerGroup.style.display = isPdf ? 'block' : 'none';
  }
}

document.querySelectorAll('input[name="question-type"]').forEach(r => {
  r.addEventListener('change', _syncOutputFormat);
});

document.querySelectorAll('input[name="rag-format"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const type = document.querySelector('input[name="question-type"]:checked')?.value || 'mcq';
    if (type === 'mcq') {
      const isPdf = document.querySelector('input[name="rag-format"]:checked')?.value === 'pdf';
      document.getElementById('answer-placement-group').style.display = isPdf ? 'block' : 'none';
    }
  });
});

let activeTab = 'text';

// ── State persistence ──────────────────────────────────────────────────────────
const _AIG_KEYS = ['pritis_aig_tab','pritis_aig_course','pritis_aig_title',
                   'pritis_aig_num','pritis_aig_type','pritis_aig_text'];

function _aigSave() {
  try {
    const type = document.querySelector('input[name="question-type"]:checked')?.value || 'mcq';
    localStorage.setItem('pritis_aig_tab',    activeTab);
    localStorage.setItem('pritis_aig_course', document.getElementById('course-select')?.value || '');
    localStorage.setItem('pritis_aig_title',  document.getElementById('quiz-title')?.value || '');
    localStorage.setItem('pritis_aig_num',    document.getElementById('num-questions')?.value || '10');
    localStorage.setItem('pritis_aig_type',   type);
    localStorage.setItem('pritis_aig_text',   document.getElementById('text-content')?.value || '');
  } catch (e) {}
}

function _aigClear() { _AIG_KEYS.forEach(k => localStorage.removeItem(k)); }

function _aigRestore() {
  const tab = localStorage.getItem('pritis_aig_tab');
  if (tab && tab !== 'text') {
    activeTab = tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    const btn = [...document.querySelectorAll('.tab-btn')]
      .find(b => (b.getAttribute('onclick') || '').includes(`'${tab}'`));
    if (btn) btn.classList.add('active');
    const panel = document.getElementById(`tab-${tab}`);
    if (panel) panel.classList.add('active');
  }
  const title = localStorage.getItem('pritis_aig_title');
  if (title) document.getElementById('quiz-title').value = title;
  const num = localStorage.getItem('pritis_aig_num');
  if (num) document.getElementById('num-questions').value = num;
  const savedType = localStorage.getItem('pritis_aig_type');
  if (savedType) {
    // migrate old 'essay' key to 'theory'
    const resolvedType = savedType === 'essay' ? 'theory' : savedType;
    const radio = document.querySelector(`input[name="question-type"][value="${resolvedType}"]`);
    if (radio) { radio.checked = true; _syncOutputFormat(); }
  }
  const text = localStorage.getItem('pritis_aig_text');
  if (text) document.getElementById('text-content').value = text;
}

async function loadCourses() {
  try {
    const courses = await api.get('/courses');
    const select = document.getElementById('course-select');
    select.innerHTML = '<option value="">Select a course</option>';
    courses.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = c.name;
      select.appendChild(opt);
    });
    const saved = localStorage.getItem('pritis_aig_course');
    if (saved) select.value = saved;
  } catch (err) {
    console.error(err);
  }
}

loadCourses();

window.switchTab = function(tab, btn) {
  activeTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(`tab-${tab}`).classList.add('active');
};

window.onDragOver = function(e) {
  e.preventDefault();
  e.currentTarget.classList.add('drag-over');
};

const _MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 MB

function _checkFileSize(file) {
  if (file.size > _MAX_FILE_SIZE) {
    const alertEl = document.getElementById('gen-alert');
    alertEl.textContent = 'Cannot upload file, it exceeds the 50MB limit.';
    alertEl.classList.remove('hidden');
    return false;
  }
  return true;
}

window.onDrop = function(e, type) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (!file) return;
  if (!_checkFileSize(file)) return;
  const input = document.getElementById(`${type}-file`);
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  document.getElementById(`${type}-filename`).textContent = file.name;
  document.getElementById('gen-alert').classList.add('hidden');
};

window.onFileSelect = function(type) {
  const input = document.getElementById(`${type}-file`);
  const label = document.getElementById(`${type}-filename`);
  const file = input.files[0];
  if (!file) return;
  if (!_checkFileSize(file)) { input.value = ''; label.textContent = ''; return; }
  label.textContent = file.name;
  document.getElementById('gen-alert').classList.add('hidden');
};

function getSelectedType() {
  return document.querySelector('input[name="question-type"]:checked')?.value || 'mcq';
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function _getRagSettings() {
  return {
    qtype:           getSelectedType(),
    difficulty:      document.querySelector('input[name="rag-difficulty"]:checked')?.value || 'medium',
    outputFormat:    document.querySelector('input[name="rag-format"]:checked')?.value || 'interactive',
    answerPlacement: document.getElementById('answer-placement').value,
  };
}

function _getFileForTab(tab) {
  return document.getElementById(`${tab}-file`)?.files[0] || null;
}

window.generate = async function() {
  const courseId = document.getElementById('course-select').value;
  const title    = document.getElementById('quiz-title').value.trim();
  const numQ     = Math.min(50, Math.max(1, parseInt(document.getElementById('num-questions').value) || 10));
  const alertEl  = document.getElementById('gen-alert');
  const successEl= document.getElementById('gen-success');

  alertEl.classList.add('hidden');
  successEl.classList.add('hidden');

  if (!courseId) { alertEl.textContent = 'Please select a course.'; alertEl.classList.remove('hidden'); return; }
  if (!title)    { alertEl.textContent = 'Quiz title is required.';  alertEl.classList.remove('hidden'); return; }

  const rag = _getRagSettings();
  // Smart Generation is always enabled for file tabs
  const isRag = activeTab !== 'text';

  const btn     = document.getElementById('generate-btn');
  const spinner = document.getElementById('gen-spinner');
  const status  = document.getElementById('gen-status');
  const wrap    = document.getElementById('gen-progress-wrap');
  const fill    = document.getElementById('gen-progress-fill');
  const label   = document.getElementById('gen-progress-label');
  const pctEl   = document.getElementById('gen-progress-pct');

  const setProgress = (pct, msg) => {
    fill.style.width  = Math.max(4, Math.min(100, Math.round(pct))) + '%';
    label.textContent = msg;
    pctEl.textContent = Math.round(pct) + '%';
  };

  const simProgress = (from, to, durationMs, cb) => {
    const interval = 80;
    const steps    = Math.ceil(durationMs / interval);
    let   step     = 0;
    const id = setInterval(() => {
      step = Math.min(step + 1, steps);
      const eased = 1 - Math.pow(1 - step / steps, 2.5);
      cb(Math.round(from + (to - from) * eased));
      if (step >= steps) clearInterval(id);
    }, interval);
    return id;
  };

  btn.disabled = true;
  spinner.classList.remove('hidden');
  status.textContent = '';
  wrap.classList.remove('hidden');
  setProgress(2, 'Starting…');

  let simTimer = null;

  try {
    // ── RAG path ──────────────────────────────────────────────────────────────
    if (isRag) {
      const file = _getFileForTab(activeTab);
      if (!file) throw new Error(`Please select a ${activeTab.toUpperCase()} file.`);
      if (file.size > _MAX_FILE_SIZE) throw new Error('Cannot upload file, it exceeds the 50MB limit.');

      const form = new FormData();
      form.append('quiz_title',       title);
      form.append('question_type',    rag.qtype);
      form.append('difficulty',       rag.difficulty);
      form.append('count',            String(numQ));
      form.append('output_format',    rag.outputFormat);
      form.append('answer_placement', rag.answerPlacement);
      form.append('course_id',        courseId);
      form.append('file',             file);

      setProgress(8, 'Uploading document…');

      if (rag.outputFormat === 'pdf') {
        simTimer = simProgress(8, 88, 22000, (p) => {
          const msg = p < 30 ? `Uploading… ${p}%`
                    : p < 55 ? `Analysing document (RAG)… ${p}%`
                    : p < 80 ? `Generating questions… ${p}%`
                    : `Building PDF… ${p}%`;
          setProgress(p, msg);
        });

        const blob = await api.postFormBlob('/generate/rag', form);
        if (simTimer) { clearInterval(simTimer); simTimer = null; }
        setProgress(100, 'PDF ready!');

        // Trigger browser download
        const url = URL.createObjectURL(blob);
        const a   = document.createElement('a');
        a.href     = url;
        a.download = title.replace(/[^a-zA-Z0-9 _-]/g, '_') + '.pdf';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 5000);

        successEl.innerHTML = `
          <strong>PDF downloaded!</strong> Your quiz "<em>${escHtml(title)}</em>"
          has been saved to your Downloads folder.
        `;
        successEl.classList.remove('hidden');

      } else {
        // Interactive RAG
        simTimer = simProgress(8, 88, 20000, (p) => {
          const msg = p < 30 ? `Uploading… ${p}%`
                    : p < 55 ? `Analysing document (RAG)… ${p}%`
                    : p < 80 ? `Generating questions… ${p}%`
                    : `Finalizing… ${p}%`;
          setProgress(p, msg);
        });

        const result = await api.postForm('/generate/rag', form);
        if (simTimer) { clearInterval(simTimer); simTimer = null; }
        setProgress(100, 'Done!');

        const questions = result.questions || [];
        renderRagPreview(questions, title, rag.difficulty);
        successEl.textContent = `Generated ${questions.length} questions from your document!`;
        successEl.classList.remove('hidden');
      }

    // ── Standard path (text tab only) ─────────────────────────────────────────
    } else {
      let quiz;

      const content = document.getElementById('text-content').value.trim();
      if (!content) throw new Error('Please paste some content first.');
      simTimer = simProgress(2, 85, 12000, (p) => {
        const msg = p < 40 ? `Sending content… ${p}%`
                  : p < 75 ? `Generating questions… ${p}%`
                  : `Finalizing… ${p}%`;
        setProgress(p, msg);
      });
      quiz = await api.post('/generate/from-text', {
        course_id: courseId, quiz_title: title,
        content, num_questions: numQ, question_types: [getSelectedType()],
      });

      if (simTimer) { clearInterval(simTimer); simTimer = null; }
      setProgress(95, 'Saving quiz…');
      const questions = await api.get(`/quizzes/${quiz.id}/questions`);
      setProgress(100, 'Done!');
      renderPreview(quiz, questions);
      successEl.textContent = `Quiz "${quiz.title}" created with ${questions.length} questions!`;
      successEl.classList.remove('hidden');
    }

  } catch (err) {
    if (err.upgradeRequired) { showUpgradeModal(err.upgradeInfo); }
    else { alertEl.textContent = err.message; alertEl.classList.remove('hidden'); }
  } finally {
    if (simTimer) clearInterval(simTimer);
    btn.disabled = false;
    spinner.classList.add('hidden');
    status.textContent = '';
    setTimeout(() => wrap.classList.add('hidden'), 1500);
  }
};

function renderPreview(quiz, questions) {
  document.getElementById('preview-section').classList.remove('hidden');
  document.getElementById('gen-count').textContent = `${questions.length} questions`;
  document.getElementById('view-quiz-btn').href = `question-bank.html`;

  const list = document.getElementById('preview-list');
  list.innerHTML = questions.map((q, i) => `
    <div class="card mb-3" style="padding:16px">
      <div class="flex items-center gap-2 mb-2">
        <span class="badge badge-primary">Q${i + 1}</span>
        <span class="badge badge-muted">${q.question_type.replace('_', ' ')}</span>
      </div>
      <p class="font-semibold text-sm">${escHtml(q.question_text)}</p>
      ${q.options ? `
        <div class="mt-2" style="display:flex;flex-direction:column;gap:4px">
          ${q.options.map((opt, idx) => `
            <span class="text-xs ${opt === q.correct_answer ? 'font-bold' : 'text-muted'}"
              style="${opt === q.correct_answer ? 'color:var(--success)' : ''}">
              ${String.fromCharCode(65 + idx)}. ${escHtml(opt)} ${opt === q.correct_answer ? '✓' : ''}
            </span>
          `).join('')}
        </div>
      ` : `<p class="text-xs text-muted mt-1">Answer: <strong>${escHtml(q.correct_answer)}</strong></p>`}
      ${q.explanation ? `<p class="text-xs mt-2" style="color:var(--success)">💡 ${escHtml(q.explanation)}</p>` : ''}
    </div>
  `).join('');
}

function renderRagPreview(questions, title, difficulty) {
  document.getElementById('preview-section').classList.remove('hidden');
  document.getElementById('gen-count').textContent = `${questions.length} questions`;
  document.getElementById('view-quiz-btn').href = 'question-bank.html';

  const diffColour = { easy: '#16a34a', medium: '#d97706', hard: '#dc2626' };

  const list = document.getElementById('preview-list');
  list.innerHTML = questions.map((q, i) => {
    const diff   = (q.difficulty || difficulty || '').toLowerCase();
    const colour = diffColour[diff] || 'var(--primary)';

    return `
      <div class="card mb-3" style="padding:16px">
        <div class="flex items-center gap-2 mb-2" style="flex-wrap:wrap">
          <span class="badge badge-primary">Q${i + 1}</span>
          <span class="badge badge-muted">${escHtml(q.question_type?.replace('_', ' ') || '')}</span>
          ${diff ? `<span style="font-size:0.72rem;font-weight:700;color:${colour};background:${colour}18;padding:2px 8px;border-radius:10px;text-transform:uppercase">${diff}</span>` : ''}
        </div>
        <p class="font-semibold text-sm">${escHtml(q.question_text)}</p>
        ${q.options ? `
          <div class="mt-2" style="display:flex;flex-direction:column;gap:4px">
            ${q.options.map((opt, idx) => `
              <span class="text-xs ${opt === q.correct_answer ? 'font-bold' : 'text-muted'}"
                style="${opt === q.correct_answer ? 'color:var(--success)' : ''}">
                ${String.fromCharCode(65 + idx)}. ${escHtml(opt)}${opt === q.correct_answer ? ' ✓' : ''}
              </span>
            `).join('')}
          </div>
        ` : `<p class="text-xs text-muted mt-1">Answer: <strong>${escHtml(q.correct_answer)}</strong></p>`}
        ${q.explanation ? `<p class="text-xs mt-2" style="color:var(--success)">💡 ${escHtml(q.explanation)}</p>` : ''}
      </div>
    `;
  }).join('');
}

window.resetGenerate = function() {
  _aigClear();
  document.getElementById('preview-section').classList.add('hidden');
  document.getElementById('quiz-title').value = '';
  document.getElementById('text-content').value = '';
  ['pdf','docx','pptx'].forEach(t => {
    const inp = document.getElementById(`${t}-file`);
    const lbl = document.getElementById(`${t}-filename`);
    if (inp) inp.value = '';
    if (lbl) lbl.textContent = '';
  });
  document.getElementById('gen-alert').classList.add('hidden');
  document.getElementById('gen-success').classList.add('hidden');
};

// ── Course Modal ──
window.openCourseModal  = () => document.getElementById('course-modal').classList.remove('hidden');
window.closeCourseModal = () => document.getElementById('course-modal').classList.add('hidden');

window.createCourse = async function() {
  const name = document.getElementById('new-course-name').value.trim();
  const desc = document.getElementById('new-course-desc').value.trim();
  const alertEl = document.getElementById('course-modal-alert');
  alertEl.classList.add('hidden');
  if (!name) { alertEl.textContent = 'Course name is required.'; alertEl.classList.remove('hidden'); return; }
  try {
    const course = await api.post('/courses', { name, description: desc || null });
    closeCourseModal();
    await loadCourses();
    document.getElementById('course-select').value = course.id;
    document.getElementById('new-course-name').value = '';
    document.getElementById('new-course-desc').value = '';
  } catch (err) {
    alertEl.textContent = err.message;
    alertEl.classList.remove('hidden');
  }
};

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Restore saved state on page load ──────────────────────────────────────────
window.addEventListener('beforeunload', _aigSave);
_aigRestore();
