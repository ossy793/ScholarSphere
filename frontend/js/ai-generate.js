import { api, requirePremium } from './api.js';
import { renderLayout } from './layout.js';

if (!requirePremium()) throw new Error('not premium');
renderLayout('AI Generate Questions', 'AI Generate');

let activeTab = 'text';

// ── State persistence ──────────────────────────────────────────────────────────
const _AIG_KEYS = ['pritis_aig_tab','pritis_aig_course','pritis_aig_title',
                   'pritis_aig_num','pritis_aig_mcq','pritis_aig_short',
                   'pritis_aig_essay','pritis_aig_text'];

function _aigSave() {
  try {
    localStorage.setItem('pritis_aig_tab',    activeTab);
    localStorage.setItem('pritis_aig_course', document.getElementById('course-select')?.value || '');
    localStorage.setItem('pritis_aig_title',  document.getElementById('quiz-title')?.value || '');
    localStorage.setItem('pritis_aig_num',    document.getElementById('num-questions')?.value || '10');
    localStorage.setItem('pritis_aig_mcq',    document.getElementById('type-mcq')?.checked ? '1' : '0');
    localStorage.setItem('pritis_aig_short',  document.getElementById('type-short')?.checked ? '1' : '0');
    localStorage.setItem('pritis_aig_essay',  document.getElementById('type-essay')?.checked ? '1' : '0');
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
  const mcq = localStorage.getItem('pritis_aig_mcq');
  if (mcq !== null) document.getElementById('type-mcq').checked = mcq !== '0';
  const short = localStorage.getItem('pritis_aig_short');
  if (short !== null) document.getElementById('type-short').checked = short !== '0';
  const essay = localStorage.getItem('pritis_aig_essay');
  if (essay !== null) document.getElementById('type-essay').checked = essay !== '0';
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

function getSelectedTypes() {
  const types = [];
  if (document.getElementById('type-mcq').checked)   types.push('mcq');
  if (document.getElementById('type-short').checked) types.push('short_answer');
  if (document.getElementById('type-essay').checked) types.push('essay');
  return types;
}

window.generate = async function() {
  const courseId = document.getElementById('course-select').value;
  const title = document.getElementById('quiz-title').value.trim();
  const numQ = parseInt(document.getElementById('num-questions').value) || 10;
  const alertEl = document.getElementById('gen-alert');
  const successEl = document.getElementById('gen-success');
  const typesAlertEl = document.getElementById('types-alert');

  alertEl.classList.add('hidden');
  successEl.classList.add('hidden');
  typesAlertEl.classList.add('hidden');

  if (!courseId) { alertEl.textContent = 'Please select a course.'; alertEl.classList.remove('hidden'); return; }
  if (!title)    { alertEl.textContent = 'Quiz title is required.';  alertEl.classList.remove('hidden'); return; }

  const questionTypes = getSelectedTypes();
  if (questionTypes.length === 0) {
    typesAlertEl.classList.remove('hidden');
    typesAlertEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }

  const btn     = document.getElementById('generate-btn');
  const spinner = document.getElementById('gen-spinner');
  const status  = document.getElementById('gen-status');
  const wrap    = document.getElementById('gen-progress-wrap');
  const fill    = document.getElementById('gen-progress-fill');
  const label   = document.getElementById('gen-progress-label');
  const pctEl   = document.getElementById('gen-progress-pct');

  // Show progress bar
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
    let quiz;

    if (activeTab === 'text') {
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
        content, num_questions: numQ,
        question_types: questionTypes,
      });

    } else if (activeTab === 'pdf') {
      const file = document.getElementById('pdf-file').files[0];
      if (!file) throw new Error('Please select a PDF file.');
      if (file.size > _MAX_FILE_SIZE) throw new Error('Cannot upload file, it exceeds the 50MB limit.');
      const form = new FormData();
      form.append('course_id', courseId);
      form.append('quiz_title', title);
      form.append('num_questions', String(numQ));
      form.append('question_types', questionTypes.join(','));
      form.append('file', file);
      setProgress(4, 'Preparing upload…');
      quiz = await api.postFormProgress('/generate/from-pdf', form, {
        onProgress: (r) => { if (!simTimer) setProgress(r * 35, `Uploading… ${Math.round(r * 35)}%`); },
        onUploadComplete: () => {
          simTimer = simProgress(35, 88, 18000, (p) => {
            const msg = p < 55 ? `Reading PDF… ${p}%`
                      : p < 75 ? `Generating questions… ${p}%`
                      : `Finalizing… ${p}%`;
            setProgress(p, msg);
          });
        },
      });

    } else if (activeTab === 'docx') {
      const file = document.getElementById('docx-file').files[0];
      if (!file) throw new Error('Please select a DOCX file.');
      if (file.size > _MAX_FILE_SIZE) throw new Error('Cannot upload file, it exceeds the 50MB limit.');
      const form = new FormData();
      form.append('course_id', courseId);
      form.append('quiz_title', title);
      form.append('num_questions', String(numQ));
      form.append('question_types', questionTypes.join(','));
      form.append('file', file);
      setProgress(4, 'Preparing upload…');
      quiz = await api.postFormProgress('/generate/from-docx', form, {
        onProgress: (r) => { if (!simTimer) setProgress(r * 35, `Uploading… ${Math.round(r * 35)}%`); },
        onUploadComplete: () => {
          simTimer = simProgress(35, 88, 14000, (p) => {
            const msg = p < 55 ? `Reading document… ${p}%`
                      : p < 75 ? `Generating questions… ${p}%`
                      : `Finalizing… ${p}%`;
            setProgress(p, msg);
          });
        },
      });

    } else {
      const file = document.getElementById('pptx-file').files[0];
      if (!file) throw new Error('Please select a PowerPoint file.');
      if (file.size > _MAX_FILE_SIZE) throw new Error('Cannot upload file, it exceeds the 50MB limit.');
      const form = new FormData();
      form.append('course_id', courseId);
      form.append('quiz_title', title);
      form.append('num_questions', String(numQ));
      form.append('question_types', questionTypes.join(','));
      form.append('file', file);
      setProgress(4, 'Preparing upload…');
      quiz = await api.postFormProgress('/generate/from-pptx', form, {
        onProgress: (r) => { if (!simTimer) setProgress(r * 35, `Uploading… ${Math.round(r * 35)}%`); },
        onUploadComplete: () => {
          simTimer = simProgress(35, 88, 12000, (p) => {
            const msg = p < 55 ? `Reading slides… ${p}%`
                      : p < 75 ? `Generating questions… ${p}%`
                      : `Finalizing… ${p}%`;
            setProgress(p, msg);
          });
        },
      });
    }

    if (simTimer) { clearInterval(simTimer); simTimer = null; }
    setProgress(95, 'Saving quiz…');

    // Load questions for preview
    const questions = await api.get(`/quizzes/${quiz.id}/questions`);
    setProgress(100, 'Done!');
    renderPreview(quiz, questions);
    successEl.textContent = `Quiz "${quiz.title}" created with ${questions.length} questions!`;
    successEl.classList.remove('hidden');

  } catch (err) {
    alertEl.textContent = err.message;
    alertEl.classList.remove('hidden');
  } finally {
    if (simTimer) clearInterval(simTimer);
    btn.disabled = false;
    spinner.classList.add('hidden');
    status.textContent = '';
    // Hide progress bar after a short delay
    setTimeout(() => wrap.classList.add('hidden'), 1500);
  }
};

function renderPreview(quiz, questions) {
  document.getElementById('preview-section').classList.remove('hidden');
  document.getElementById('gen-count').textContent = `${questions.length} questions`;
  document.getElementById('view-quiz-btn').href = `my-questions.html`;

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

window.resetGenerate = function() {
  _aigClear();
  document.getElementById('preview-section').classList.add('hidden');
  document.getElementById('quiz-title').value = '';
  document.getElementById('text-content').value = '';
  document.getElementById('pdf-file').value = '';
  document.getElementById('docx-file').value = '';
  document.getElementById('pdf-filename').textContent = '';
  document.getElementById('docx-filename').textContent = '';
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
