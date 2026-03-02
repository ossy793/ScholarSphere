import { api, requirePremium } from './api.js';
import { renderLayout } from './layout.js';

if (!requirePremium()) throw new Error('not premium');
renderLayout('Input Questions', 'Input Questions');

let currentQuizId = null;
let reviewItems = []; // [{ question_text, question_type, options, correct_answer, explanation }]

// ── State persistence ──────────────────────────────────────────────────────────
const _IQ_KEYS = ['ossyquiz_iq_quiz_id','ossyquiz_iq_step','ossyquiz_iq_course',
                  'ossyquiz_iq_title','ossyquiz_iq_desc','ossyquiz_iq_content','ossyquiz_iq_items'];

function _iqSave() {
  try {
    const step = !document.getElementById('review-section').classList.contains('hidden') ? 3
               : !document.getElementById('bulk-input-section').classList.contains('hidden') ? 2 : 1;
    localStorage.setItem('ossyquiz_iq_quiz_id', currentQuizId || '');
    localStorage.setItem('ossyquiz_iq_step',    String(step));
    localStorage.setItem('ossyquiz_iq_course',  document.getElementById('course-select')?.value || '');
    localStorage.setItem('ossyquiz_iq_title',   document.getElementById('quiz-title')?.value || '');
    localStorage.setItem('ossyquiz_iq_desc',    document.getElementById('quiz-desc')?.value || '');
    localStorage.setItem('ossyquiz_iq_content', document.getElementById('content-textarea')?.value || '');
    localStorage.setItem('ossyquiz_iq_items',   JSON.stringify(reviewItems));
  } catch (e) {}
}

function _iqClear() { _IQ_KEYS.forEach(k => localStorage.removeItem(k)); }

function _iqRestore() {
  const quizId = localStorage.getItem('ossyquiz_iq_quiz_id');
  if (!quizId) return;
  currentQuizId = quizId;

  const step    = parseInt(localStorage.getItem('ossyquiz_iq_step') || '1');
  const title   = localStorage.getItem('ossyquiz_iq_title')   || '';
  const desc    = localStorage.getItem('ossyquiz_iq_desc')    || '';
  const content = localStorage.getItem('ossyquiz_iq_content') || '';
  let   items   = [];
  try { items = JSON.parse(localStorage.getItem('ossyquiz_iq_items') || '[]'); } catch {}

  if (title)   document.getElementById('quiz-title').value = title;
  if (desc)    document.getElementById('quiz-desc').value  = desc;
  if (content) document.getElementById('content-textarea').value = content;

  if (step >= 2) {
    const card = document.getElementById('quiz-details-card');
    card.style.opacity = '0.55';
    card.style.pointerEvents = 'none';
    const btn = document.getElementById('create-quiz-btn');
    btn.disabled = true;
    btn.textContent = '✓ Quiz Created';

    if (step === 3 && items.length > 0) {
      showReviewSection(items);
    } else {
      setStep(2);
      document.getElementById('bulk-input-section').classList.remove('hidden');
    }
  }
}

// ── Load Courses ──────────────────────────────────────────────────────────────
async function loadCourses() {
  try {
    const courses = await api.get('/courses');
    const select = document.getElementById('course-select');
    select.innerHTML = '<option value="">Select a course</option>';
    courses.forEach(c => {
      select.innerHTML += `<option value="${c.id}">${escHtml(c.name)}</option>`;
    });
    const saved = localStorage.getItem('ossyquiz_iq_course');
    if (saved) select.value = saved;
  } catch (err) {
    console.error('Failed to load courses:', err);
  }
}

loadCourses();

// ── Step indicator helpers ────────────────────────────────────────────────────
function setStep(n) {
  for (let i = 1; i <= 3; i++) {
    const el = document.getElementById(`si-${i}`);
    el.classList.remove('active', 'done');
    if (i < n)  el.classList.add('done');
    if (i === n) el.classList.add('active');
  }
  for (let i = 1; i <= 2; i++) {
    document.getElementById(`sl-${i}`).classList.toggle('done', i < n);
  }
}

// ── Step 1: Create Quiz ───────────────────────────────────────────────────────
window.createQuiz = async function () {
  const courseId = document.getElementById('course-select').value;
  const title    = document.getElementById('quiz-title').value.trim();
  const desc     = document.getElementById('quiz-desc').value.trim();
  const alertEl  = document.getElementById('quiz-alert');
  alertEl.classList.add('hidden');

  if (!courseId) return showAlert(alertEl, 'Please select a course.');
  if (!title)    return showAlert(alertEl, 'Quiz title is required.');

  const btn = document.getElementById('create-quiz-btn');
  btn.disabled = true;
  btn.textContent = 'Creating…';

  try {
    const quiz = await api.post('/quizzes', { course_id: courseId, title, description: desc || null });
    currentQuizId = quiz.id;

    const card = document.getElementById('quiz-details-card');
    card.style.opacity = '0.55';
    card.style.pointerEvents = 'none';
    btn.textContent = '✓ Quiz Created';

    setStep(2);
    document.getElementById('bulk-input-section').classList.remove('hidden');
    document.getElementById('bulk-input-section').scrollIntoView({ behavior: 'smooth' });
    _iqSave();
  } catch (err) {
    showAlert(alertEl, err.message);
    btn.disabled = false;
    btn.textContent = 'Create Quiz & Continue →';
  }
};

// ── Step 2: File upload helpers ───────────────────────────────────────────────
window.triggerFileInput = function () {
  document.getElementById('content-file-input').click();
};

window.clearUploadedFile = function () {
  document.getElementById('content-file-input').value = '';
  document.getElementById('file-badge').classList.add('hidden');
  document.getElementById('content-textarea').value = '';
};

window.handleContentFileInput = async function (e) {
  const file = e.target.files[0];
  if (!file) return;

  const alertEl = document.getElementById('parse-alert');
  alertEl.classList.add('hidden');

  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!['.pdf', '.docx', '.pptx', '.txt'].includes(ext)) {
    showAlert(alertEl, 'Unsupported file. Please upload a PDF, DOCX, PPTX, or TXT file.');
    return;
  }

  // Show loading state
  document.getElementById('file-reading').classList.remove('hidden');
  document.getElementById('file-badge').classList.add('hidden');

  try {
    let text = '';
    if (ext === '.txt') {
      text = await readTextFile(file);
    } else if (ext === '.docx') {
      text = await readDocxFile(file);
    } else if (ext === '.pdf' || ext === '.pptx') {
      text = await uploadFileForText(file);
    }

    if (!text.trim()) {
      showAlert(alertEl, 'The file appears to be empty or contains no readable text.');
      return;
    }

    // Populate textarea with extracted text
    document.getElementById('content-textarea').value = text;

    // Show file badge
    document.getElementById('file-name-label').textContent = `📄 ${file.name}`;
    document.getElementById('file-badge').classList.remove('hidden');
  } catch (err) {
    showAlert(alertEl, err.message || 'Failed to read the file. Please try again.');
  } finally {
    document.getElementById('file-reading').classList.add('hidden');
    e.target.value = '';
  }
};

function readTextFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = e => resolve(e.target.result);
    reader.onerror = () => reject(new Error('Failed to read the text file.'));
    reader.readAsText(file, 'utf-8');
  });
}

async function readDocxFile(file) {
  if (typeof mammoth === 'undefined') {
    throw new Error('mammoth.js failed to load. Check your internet connection.');
  }
  const arrayBuffer = await file.arrayBuffer();
  const result = await mammoth.extractRawText({ arrayBuffer });
  const text = result.value.trim();
  if (!text) throw new Error('This DOCX file contains no extractable text.');
  return text;
}

async function uploadFileForText(file) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.postForm('/brainstorm/upload', formData);
  return res.text;
}

// ── Step 2: Analyze & Generate ────────────────────────────────────────────────
window.analyzeAndGenerate = async function () {
  const content = document.getElementById('content-textarea').value.trim();
  const alertEl = document.getElementById('parse-alert');
  alertEl.classList.add('hidden');

  if (!content) return showAlert(alertEl, 'Please paste some content or upload a file first.');

  const btn = document.getElementById('analyze-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Analyzing…';

  try {
    const generated = await api.post('/generate/parse', { content });

    if (!Array.isArray(generated) || generated.length === 0) {
      showAlert(alertEl, 'AI could not detect any questions in the content. Please check the input and try again.');
      return;
    }

    const items = generated.map(g => {
      const type = g.question_type || 'mcq';
      let opts = g.options || null;
      if (type === 'mcq')        opts = normaliseMcqOptions(opts);
      if (type === 'true_false') opts = ['True', 'False'];
      if (type === 'short_answer') opts = null;
      return {
        question_text:  g.question_text  || '',
        question_type:  type,
        options:        opts,
        correct_answer: g.correct_answer || '',
        explanation:    g.explanation    || '',
      };
    });

    showReviewSection(items);
    _iqSave();
  } catch (err) {
    showAlert(alertEl, `Analysis failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '✨ Analyze &amp; Generate Quiz';
  }
};

// ── Step 3: Render Review ─────────────────────────────────────────────────────
function showReviewSection(items) {
  document.getElementById('bulk-input-section').classList.add('hidden');
  document.getElementById('review-section').classList.remove('hidden');
  setStep(3);
  renderReview(items);
  document.getElementById('review-section').scrollIntoView({ behavior: 'smooth' });
}

window.goBackToBulkInput = function () {
  document.getElementById('review-section').classList.add('hidden');
  document.getElementById('bulk-input-section').classList.remove('hidden');
  document.getElementById('save-alert').classList.add('hidden');
  setStep(2);
};

function renderReview(items) {
  reviewItems = items;
  document.getElementById('review-count').textContent =
    `${items.length} question${items.length !== 1 ? 's' : ''}`;

  const container = document.getElementById('review-list');
  if (items.length === 0) {
    container.innerHTML = `
      <div class="card text-center" style="padding:40px;color:var(--text-muted)">
        All questions removed.
        <a href="#" onclick="goBackToBulkInput();return false" style="margin-left:6px">← Go back to add more</a>
      </div>`;
    return;
  }
  container.innerHTML = items.map((item, i) => renderCard(i, item)).join('');
}

function normaliseMcqOptions(opts) {
  const arr = (opts || []).slice(0, 4);
  while (arr.length < 4) arr.push('');
  return arr;
}

function renderCard(i, item) {
  const labels = ['A', 'B', 'C', 'D'];
  let optionsHtml = '';

  if (item.question_type === 'mcq') {
    const opts = normaliseMcqOptions(item.options);

    const optInputs = opts.map((o, j) => `
      <div class="opt-row">
        <span class="opt-label">${labels[j]}</span>
        <input type="text" class="form-control" style="padding:8px 12px"
          value="${escAttr(o)}" placeholder="Option ${labels[j]}"
          oninput="updateReviewOption(${i},${j},this.value)">
      </div>`).join('');

    const correctOpts = opts.map((o, j) =>
      `<option value="${escAttr(o)}" ${item.correct_answer === o ? 'selected' : ''}>${labels[j]}. ${escHtml(o) || '(empty)'}</option>`
    ).join('');

    optionsHtml = `
      <div class="form-group">
        <label class="form-label">Options</label>
        ${optInputs}
      </div>
      <div class="form-group">
        <label class="form-label">Correct Answer</label>
        <select class="form-control" id="correct-sel-${i}"
          onchange="updateReviewField(${i},'correct_answer',this.value)">
          ${correctOpts}
        </select>
      </div>`;

  } else if (item.question_type === 'true_false') {
    optionsHtml = `
      <div class="form-group">
        <label class="form-label">Correct Answer</label>
        <select class="form-control" onchange="updateReviewField(${i},'correct_answer',this.value)">
          <option value="True"  ${item.correct_answer === 'True'  ? 'selected' : ''}>True</option>
          <option value="False" ${item.correct_answer === 'False' ? 'selected' : ''}>False</option>
        </select>
      </div>`;

  } else {
    optionsHtml = `
      <div class="form-group">
        <label class="form-label">Expected Answer <span style="color:var(--danger)">*</span></label>
        <input type="text" class="form-control" value="${escAttr(item.correct_answer || '')}"
          placeholder="Type the expected answer"
          oninput="updateReviewField(${i},'correct_answer',this.value)">
      </div>`;
  }

  return `
    <div class="card mb-3 review-card" id="rcard-${i}">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-2">
          <span class="badge badge-primary">Q${i + 1}</span>
          <select class="form-control"
            style="padding:5px 10px;font-size:0.8rem;width:auto;height:auto;border-radius:var(--radius-sm)"
            onchange="changeReviewType(${i},this.value)">
            <option value="mcq"          ${item.question_type === 'mcq'          ? 'selected' : ''}>Multiple Choice</option>
            <option value="true_false"   ${item.question_type === 'true_false'   ? 'selected' : ''}>True / False</option>
            <option value="short_answer" ${item.question_type === 'short_answer' ? 'selected' : ''}>Short Answer</option>
          </select>
        </div>
        <button class="btn btn-danger btn-sm" onclick="removeReviewItem(${i})">Remove</button>
      </div>

      <div class="form-group">
        <label class="form-label">Question</label>
        <textarea class="form-control" rows="2"
          oninput="updateReviewField(${i},'question_text',this.value)">${escHtml(item.question_text)}</textarea>
      </div>

      ${optionsHtml}

      <div class="form-group" style="margin-bottom:0">
        <label class="form-label">
          Explanation
          <span class="text-muted text-xs" style="font-weight:400">(optional)</span>
        </label>
        <textarea class="form-control" rows="1" placeholder="Why is this answer correct?"
          oninput="updateReviewField(${i},'explanation',this.value)">${escHtml(item.explanation || '')}</textarea>
      </div>
    </div>`;
}

// ── Review item mutations ─────────────────────────────────────────────────────
window.updateReviewField = function (i, field, value) {
  reviewItems[i][field] = value;
};

window.updateReviewOption = function (i, j, value) {
  if (!reviewItems[i].options) reviewItems[i].options = ['', '', '', ''];
  while (reviewItems[i].options.length < 4) reviewItems[i].options.push('');

  const old = reviewItems[i].options[j];
  reviewItems[i].options[j] = value;

  if (reviewItems[i].correct_answer === old) {
    reviewItems[i].correct_answer = value;
  }

  const sel = document.getElementById(`correct-sel-${i}`);
  if (sel) {
    const labels = ['A', 'B', 'C', 'D'];
    const opts   = reviewItems[i].options;
    sel.innerHTML = opts.map((o, idx) =>
      `<option value="${escAttr(o)}" ${reviewItems[i].correct_answer === o ? 'selected' : ''}>${labels[idx]}. ${escHtml(o) || '(empty)'}</option>`
    ).join('');
  }
};

window.changeReviewType = function (i, type) {
  const item = reviewItems[i];
  item.question_type = type;

  if (type === 'true_false') {
    item.options        = ['True', 'False'];
    item.correct_answer = 'True';
  } else if (type === 'mcq') {
    item.options = normaliseMcqOptions(item.options);
    item.correct_answer = item.options[0] || '';
  } else {
    item.options = null;
  }

  const card = document.getElementById(`rcard-${i}`);
  if (card) card.outerHTML = renderCard(i, item);
};

window.removeReviewItem = function (i) {
  reviewItems.splice(i, 1);
  renderReview(reviewItems);
  _iqSave();
};

// ── Step 3: Save All ──────────────────────────────────────────────────────────
window.saveAllQuestions = async function () {
  const alertEl = document.getElementById('save-alert');
  alertEl.classList.add('hidden');

  if (reviewItems.length === 0) {
    alertEl.className = 'alert alert-error';
    alertEl.textContent = 'No questions to save.';
    alertEl.classList.remove('hidden');
    return;
  }

  const errors = [];
  reviewItems.forEach((item, i) => {
    if (!item.question_text.trim())
      errors.push(`Q${i + 1}: Question text is empty.`);
    if (item.question_type === 'mcq') {
      const opts = item.options || [];
      if (opts.some(o => !o.trim())) errors.push(`Q${i + 1}: All 4 options must be filled.`);
      if (!item.correct_answer)       errors.push(`Q${i + 1}: Please select a correct answer.`);
    }
    if (item.question_type === 'short_answer' && !item.correct_answer.trim())
      errors.push(`Q${i + 1}: Expected answer is required.`);
  });

  if (errors.length > 0) {
    alertEl.className = 'alert alert-error';
    alertEl.innerHTML = errors.join('<br>');
    alertEl.classList.remove('hidden');
    return;
  }

  const btn = document.getElementById('save-all-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Saving…';

  let saved = 0;
  const failed = [];

  for (let i = 0; i < reviewItems.length; i++) {
    const item = reviewItems[i];
    try {
      await api.post(`/quizzes/${currentQuizId}/questions`, {
        question_text:  item.question_text.trim(),
        question_type:  item.question_type,
        options:        item.options,
        correct_answer: item.correct_answer.trim(),
        explanation:    item.explanation?.trim() || null,
        order_index:    i,
      });
      saved++;
    } catch (err) {
      failed.push(`Q${i + 1}: ${err.message}`);
    }
  }

  btn.disabled = false;
  btn.innerHTML = 'Save All Questions';

  if (failed.length === 0) {
    alertEl.className = 'alert alert-success';
    alertEl.innerHTML =
      `✓ ${saved} question${saved !== 1 ? 's' : ''} saved successfully! ` +
      `<a href="my-questions.html" style="font-weight:600;margin-left:8px">View All Quizzes →</a>`;
    reviewItems = [];
    renderReview([]);
    _iqClear();
  } else {
    alertEl.className = 'alert alert-error';
    alertEl.innerHTML = `${saved} saved, ${failed.length} failed:<br>${failed.join('<br>')}`;
  }

  alertEl.classList.remove('hidden');
};

// ── Reset ─────────────────────────────────────────────────────────────────────
window.resetAll = function () {
  _iqClear();
  currentQuizId = null;
  reviewItems   = [];

  document.getElementById('review-section').classList.add('hidden');
  document.getElementById('bulk-input-section').classList.add('hidden');

  const card = document.getElementById('quiz-details-card');
  card.style.opacity = '';
  card.style.pointerEvents = '';

  document.getElementById('quiz-title').value = '';
  document.getElementById('quiz-desc').value  = '';
  document.getElementById('content-textarea').value = '';
  document.getElementById('file-badge').classList.add('hidden');
  document.getElementById('content-file-input').value = '';

  const btn = document.getElementById('create-quiz-btn');
  btn.disabled    = false;
  btn.textContent = 'Create Quiz & Continue →';

  document.getElementById('quiz-alert').classList.add('hidden');
  setStep(1);
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

// ── Course Modal ──────────────────────────────────────────────────────────────
window.openCourseModal  = function () { document.getElementById('course-modal').classList.remove('hidden'); };
window.closeCourseModal = function () { document.getElementById('course-modal').classList.add('hidden'); };

window.createCourse = async function () {
  const name    = document.getElementById('new-course-name').value.trim();
  const desc    = document.getElementById('new-course-desc').value.trim();
  const alertEl = document.getElementById('course-modal-alert');
  alertEl.classList.add('hidden');

  if (!name) return showAlert(alertEl, 'Course name is required.');

  try {
    const course = await api.post('/courses', { name, description: desc || null });
    closeCourseModal();
    await loadCourses();
    document.getElementById('course-select').value = course.id;
    document.getElementById('new-course-name').value = '';
    document.getElementById('new-course-desc').value = '';
  } catch (err) {
    showAlert(alertEl, err.message);
  }
};

// ── Utilities ─────────────────────────────────────────────────────────────────
function showAlert(el, msg) {
  el.textContent = msg;
  el.classList.remove('hidden');
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escAttr(str) {
  return String(str ?? '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Restore saved state on page load ──────────────────────────────────────────
window.addEventListener('beforeunload', _iqSave);
_iqRestore();
