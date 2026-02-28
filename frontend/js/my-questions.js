import { api, requirePremium } from './api.js';
import { renderLayout } from './layout.js';

if (!requirePremium()) throw new Error('not premium');
renderLayout('My Questions', 'My Questions');

let allQuizzes = [];
let pendingDeleteId = null;

// ── Load courses for filter ──
async function loadCourseFilter() {
  try {
    const courses = await api.get('/courses');
    const select = document.getElementById('filter-course');
    courses.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = c.name;
      select.appendChild(opt);
    });
  } catch {}
}

// ── Load quizzes ──
window.loadQuizzes = async function() {
  const courseId = document.getElementById('filter-course').value;
  const loading = document.getElementById('loading');
  loading.classList.remove('hidden');
  try {
    const url = courseId ? `/quizzes?course_id=${courseId}` : '/quizzes';
    allQuizzes = await api.get(url);
    filterQuizzes();
  } catch (err) {
    console.error(err);
  } finally {
    loading.classList.add('hidden');
  }
};

window.filterQuizzes = function() {
  const search = document.getElementById('filter-search').value.toLowerCase();
  const filtered = search
    ? allQuizzes.filter(q => q.title.toLowerCase().includes(search))
    : allQuizzes;
  renderQuizGrid(filtered);
};

function renderQuizGrid(quizzes) {
  const grid = document.getElementById('quiz-grid');
  const empty = document.getElementById('empty-state');

  if (quizzes.length === 0) {
    grid.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  const sourceLabels = {
    manual: { label: 'Manual', icon: '✏️' },
    ai_text: { label: 'AI · Text', icon: '🤖' },
    ai_pdf:  { label: 'AI · PDF', icon: '📄' },
    ai_docx: { label: 'AI · Word', icon: '📝' },
  };

  grid.innerHTML = quizzes.map(quiz => {
    const src = sourceLabels[quiz.source_type] || { label: quiz.source_type, icon: '📋' };
    const created = new Date(quiz.created_at).toLocaleDateString();
    return `
      <div class="quiz-card">
        <div class="quiz-card-title">${escHtml(quiz.title)}</div>
        <div class="quiz-card-meta">
          <span>${src.icon} ${src.label}</span>
          <span>📚 ${quiz.question_count} Q</span>
          <span>🗓 ${created}</span>
          ${quiz.share_token ? '<span class="badge badge-success">Shared</span>' : ''}
        </div>
        ${quiz.description ? `<p class="text-xs text-muted mb-3">${escHtml(quiz.description)}</p>` : ''}
        <div class="quiz-card-actions">
          <a href="quiz-practice.html?quiz=${quiz.id}" class="btn btn-primary btn-sm">Practice</a>
          <button class="btn btn-outline btn-sm" onclick="shareQuiz('${quiz.id}')">Share</button>
          <button class="btn btn-danger btn-sm" onclick="openDeleteModal('${quiz.id}', '${escHtml(quiz.title)}')">Delete</button>
        </div>
      </div>
    `;
  }).join('');
}

// ── Share ──
window.shareQuiz = async function(quizId) {
  try {
    const data = await api.post(`/quizzes/${quizId}/share`, {});
    const fullUrl = `${window.location.origin}/quiz-practice.html?share=${data.share_token}`;
    document.getElementById('share-link').value = fullUrl;
    document.getElementById('share-modal').classList.remove('hidden');
  } catch (err) {
    alert(err.message);
  }
};

window.closeShareModal = () => document.getElementById('share-modal').classList.add('hidden');

window.copyShareLink = function() {
  const input = document.getElementById('share-link');
  input.select();
  navigator.clipboard.writeText(input.value).then(() => {
    const btn = document.querySelector('#share-modal .btn-primary');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  });
};

// ── Delete ──
window.openDeleteModal = function(quizId, name) {
  pendingDeleteId = quizId;
  document.getElementById('delete-quiz-name').textContent = name;
  document.getElementById('delete-modal').classList.remove('hidden');
};

window.closeDeleteModal = () => document.getElementById('delete-modal').classList.add('hidden');

window.confirmDelete = async function() {
  if (!pendingDeleteId) return;
  const btn = document.getElementById('confirm-delete-btn');
  btn.disabled = true; btn.textContent = 'Deleting…';
  try {
    await api.del(`/quizzes/${pendingDeleteId}`);
    closeDeleteModal();
    allQuizzes = allQuizzes.filter(q => q.id !== pendingDeleteId);
    filterQuizzes();
    pendingDeleteId = null;
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Delete';
  }
};

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Init ──
loadCourseFilter();
loadQuizzes();
