import { api, requireAuth } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('unauthenticated');
renderLayout('Question Bank', 'Question Bank');

// ── State ─────────────────────────────────────────────────────────────────────
let allCourses = [];
let allQuizzes = [];
let currentCourseId = null;
let pendingDeleteCourseId = null;
let pendingDeleteQuizId = null;
let _activeDropdownId = null;

// ── Folder color palette ──────────────────────────────────────────────────────
const FOLDER_COLORS = [
  '#3B82F6','#8B5CF6','#10B981','#F59E0B','#EF4444',
  '#06B6D4','#EC4899','#6366F1','#84CC16','#F97316',
];

function _folderColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h);
  return FOLDER_COLORS[Math.abs(h) % FOLDER_COLORS.length];
}

// ── View switching ────────────────────────────────────────────────────────────
function _showFoldersView() {
  document.getElementById('view-folders').classList.remove('hidden');
  document.getElementById('view-quizzes').classList.add('hidden');
}
function _showQuizzesView() {
  document.getElementById('view-folders').classList.add('hidden');
  document.getElementById('view-quizzes').classList.remove('hidden');
}

// ── Load courses ──────────────────────────────────────────────────────────────
async function loadFolders() {
  const loading = document.getElementById('folders-loading');
  const empty   = document.getElementById('folders-empty');
  const grid    = document.getElementById('folder-grid');
  loading.classList.remove('hidden');
  empty.classList.add('hidden');
  grid.innerHTML = '';
  try {
    allCourses = await api.get('/courses');
    filterFolders();
  } catch (err) {
    console.error('loadFolders:', err);
  } finally {
    loading.classList.add('hidden');
  }
}

window.filterFolders = function () {
  const q = document.getElementById('folder-search').value.toLowerCase();
  const filtered = q ? allCourses.filter(c => c.name.toLowerCase().includes(q)) : allCourses;
  renderFolderGrid(filtered);
};

function renderFolderGrid(courses) {
  const grid  = document.getElementById('folder-grid');
  const empty = document.getElementById('folders-empty');

  if (courses.length === 0 && !document.getElementById('folder-search').value) {
    grid.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  grid.innerHTML = courses.map(c => {
    const color     = _folderColor(c.name);
    const quizLabel = c.quiz_count === 1 ? '1 quiz' : `${c.quiz_count || 0} quizzes`;
    return `
      <div class="folder-card" onclick="openFolderById('${c.id}')">
        <div class="folder-color-bar" style="background:${color}"></div>
        <button class="folder-menu-btn" onclick="toggleDropdown(event,'${c.id}')">⋮</button>
        <div class="folder-dropdown hidden" id="dd-${c.id}">
          <button class="folder-dd-item" onclick="openEditCourseModal(event,'${c.id}')">✏️ Edit</button>
          <button class="folder-dd-item danger" onclick="openDeleteCourseModal(event,'${c.id}')">🗑️ Delete</button>
        </div>
        <div class="folder-card-body">
          <div class="folder-card-icon">📁</div>
          <div class="folder-card-name">${escHtml(c.name)}</div>
          <div class="folder-card-count">${quizLabel}</div>
        </div>
      </div>
    `;
  }).join('') + `
    <div class="add-folder-card" onclick="openCreateCourseModal()">
      <span class="afc-plus">+</span>
      <p>New Course</p>
    </div>
  `;
}

// ── Dropdown ──────────────────────────────────────────────────────────────────
window.toggleDropdown = function (e, courseId) {
  e.stopPropagation();
  const dd = document.getElementById(`dd-${courseId}`);
  const opening = dd.classList.contains('hidden');
  // Close any open dropdown first
  _closeActiveDropdown();
  if (opening) {
    dd.classList.remove('hidden');
    _activeDropdownId = courseId;
  }
};

function _closeActiveDropdown() {
  if (_activeDropdownId) {
    document.getElementById(`dd-${_activeDropdownId}`)?.classList.add('hidden');
    _activeDropdownId = null;
  }
}

document.addEventListener('click', _closeActiveDropdown);

// ── Open folder ───────────────────────────────────────────────────────────────
window.openFolderById = function (courseId) {
  const course = allCourses.find(c => c.id === courseId);
  if (!course) return;
  currentCourseId = courseId;
  document.getElementById('current-course-name').textContent = course.name;
  document.getElementById('quiz-search').value = '';
  _showQuizzesView();
  loadQuizzes();
};

window.backToFolders = function () {
  _showFoldersView();
  loadFolders();
};

// ── Load quizzes ──────────────────────────────────────────────────────────────
async function loadQuizzes() {
  const loading = document.getElementById('quizzes-loading');
  const empty   = document.getElementById('quizzes-empty');
  loading.classList.remove('hidden');
  empty.classList.add('hidden');
  document.getElementById('quiz-grid').innerHTML = '';
  try {
    allQuizzes = await api.get(`/quizzes?course_id=${currentCourseId}`);
    filterQuizzes();
  } catch (err) {
    console.error('loadQuizzes:', err);
  } finally {
    loading.classList.add('hidden');
  }
}

window.filterQuizzes = function () {
  const q = document.getElementById('quiz-search').value.toLowerCase();
  const filtered = q ? allQuizzes.filter(qz => qz.title.toLowerCase().includes(q)) : allQuizzes;
  renderQuizGrid(filtered);
};

function renderQuizGrid(quizzes) {
  const grid  = document.getElementById('quiz-grid');
  const empty = document.getElementById('quizzes-empty');

  if (quizzes.length === 0) {
    grid.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  const srcLabels = {
    manual:  { label: 'Manual',    icon: '✏️' },
    ai_text: { label: 'AI · Text', icon: '🤖' },
    ai_pdf:  { label: 'AI · PDF',  icon: '📄' },
    ai_docx: { label: 'AI · Word', icon: '📝' },
    ai_pptx: { label: 'AI · PPTX', icon: '📊' },
  };

  grid.innerHTML = quizzes.map(quiz => {
    const src     = srcLabels[quiz.source_type] || { label: quiz.source_type, icon: '📋' };
    const created = new Date(quiz.created_at).toLocaleDateString();
    return `
      <div class="quiz-card">
        <div class="quiz-card-title">${escHtml(quiz.title)}</div>
        <div class="quiz-card-meta">
          <span>${src.icon} ${src.label}</span>
          <span>📚 ${quiz.question_count} Q</span>
          <span>🗓 ${created}</span>
          ${quiz.share_token ? '<span class="badge badge-success" style="font-size:.7rem">Shared</span>' : ''}
        </div>
        ${quiz.description ? `<p class="text-xs text-muted" style="margin:0">${escHtml(quiz.description)}</p>` : ''}
        <div class="quiz-card-actions">
          <a href="quiz-practice.html?quiz=${quiz.id}" class="btn btn-primary btn-sm">Practice</a>
          <button class="btn btn-outline btn-sm" onclick="shareQuiz('${quiz.id}')">Share</button>
          <button class="btn btn-danger btn-sm" onclick="openDeleteQuizModal('${quiz.id}')">Delete</button>
        </div>
      </div>
    `;
  }).join('');
}

// ── Create Course ─────────────────────────────────────────────────────────────
window.openCreateCourseModal = function () {
  document.getElementById('new-course-name').value = '';
  document.getElementById('create-course-alert').classList.add('hidden');
  document.getElementById('create-course-modal').classList.remove('hidden');
  setTimeout(() => document.getElementById('new-course-name').focus(), 50);
};
window.closeCreateCourseModal = () => document.getElementById('create-course-modal').classList.add('hidden');

window.createCourse = async function () {
  const name    = document.getElementById('new-course-name').value.trim();
  const alertEl = document.getElementById('create-course-alert');
  alertEl.classList.add('hidden');
  if (!name) { alertEl.textContent = 'Course name is required.'; alertEl.classList.remove('hidden'); return; }
  const btn = document.getElementById('create-course-btn');
  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    await api.post('/courses', { name });
    closeCreateCourseModal();
    await loadFolders();
  } catch (err) {
    alertEl.textContent = err.message; alertEl.classList.remove('hidden');
  } finally {
    btn.disabled = false; btn.textContent = 'Create Course';
  }
};

// ── Edit Course ───────────────────────────────────────────────────────────────
window.openEditCourseModal = function (e, courseId) {
  e.stopPropagation();
  _closeActiveDropdown();
  const course = allCourses.find(c => c.id === courseId);
  if (!course) return;
  document.getElementById('edit-course-id').value   = courseId;
  document.getElementById('edit-course-name').value = course.name;
  document.getElementById('edit-course-alert').classList.add('hidden');
  document.getElementById('edit-course-modal').classList.remove('hidden');
  setTimeout(() => document.getElementById('edit-course-name').focus(), 50);
};
window.closeEditCourseModal = () => document.getElementById('edit-course-modal').classList.add('hidden');

window.saveCourseEdit = async function () {
  const id      = document.getElementById('edit-course-id').value;
  const name    = document.getElementById('edit-course-name').value.trim();
  const alertEl = document.getElementById('edit-course-alert');
  alertEl.classList.add('hidden');
  if (!name) { alertEl.textContent = 'Course name is required.'; alertEl.classList.remove('hidden'); return; }
  const btn = document.getElementById('save-course-btn');
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    await api.put(`/courses/${id}`, { name });
    closeEditCourseModal();
    await loadFolders();
  } catch (err) {
    alertEl.textContent = err.message; alertEl.classList.remove('hidden');
  } finally {
    btn.disabled = false; btn.textContent = 'Save Changes';
  }
};

// ── Delete Course ─────────────────────────────────────────────────────────────
window.openDeleteCourseModal = function (e, courseId) {
  e.stopPropagation();
  _closeActiveDropdown();
  const course = allCourses.find(c => c.id === courseId);
  if (!course) return;
  pendingDeleteCourseId = courseId;
  document.getElementById('delete-course-name').textContent = course.name;
  const count   = course.quiz_count || 0;
  const warning = document.getElementById('delete-course-warning');
  warning.textContent = count > 0
    ? `⚠️ This will permanently delete ${count} quiz${count === 1 ? '' : 'zes'} inside this course. This cannot be undone.`
    : 'This cannot be undone.';
  document.getElementById('delete-course-modal').classList.remove('hidden');
};
window.closeDeleteCourseModal = function () {
  document.getElementById('delete-course-modal').classList.add('hidden');
  pendingDeleteCourseId = null;
};

window.confirmDeleteCourse = async function () {
  if (!pendingDeleteCourseId) return;
  const btn = document.getElementById('confirm-delete-course-btn');
  btn.disabled = true; btn.textContent = 'Deleting…';
  try {
    await api.del(`/courses/${pendingDeleteCourseId}`);
    closeDeleteCourseModal();
    await loadFolders();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Delete';
  }
};

// ── Share Quiz ────────────────────────────────────────────────────────────────
window.shareQuiz = async function (quizId) {
  try {
    const data    = await api.post(`/quizzes/${quizId}/share`, {});
    const fullUrl = `${window.location.origin}/quiz-practice.html?share=${data.share_token}`;
    document.getElementById('share-link').value = fullUrl;
    document.getElementById('share-modal').classList.remove('hidden');
  } catch (err) { alert(err.message); }
};
window.closeShareModal = () => document.getElementById('share-modal').classList.add('hidden');
window.copyShareLink = function () {
  const input = document.getElementById('share-link');
  input.select();
  navigator.clipboard.writeText(input.value).then(() => {
    const btn = document.getElementById('copy-share-btn');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  });
};

// ── Delete Quiz ───────────────────────────────────────────────────────────────
window.openDeleteQuizModal = function (quizId) {
  pendingDeleteQuizId = quizId;
  const quiz = allQuizzes.find(q => q.id === quizId);
  document.getElementById('delete-quiz-name').textContent = quiz ? quiz.title : 'this quiz';
  document.getElementById('delete-quiz-modal').classList.remove('hidden');
};
window.closeDeleteQuizModal = function () {
  document.getElementById('delete-quiz-modal').classList.add('hidden');
  pendingDeleteQuizId = null;
};

window.confirmDeleteQuiz = async function () {
  if (!pendingDeleteQuizId) return;
  const btn = document.getElementById('confirm-delete-quiz-btn');
  btn.disabled = true; btn.textContent = 'Deleting…';
  try {
    await api.del(`/quizzes/${pendingDeleteQuizId}`);
    allQuizzes = allQuizzes.filter(q => q.id !== pendingDeleteQuizId);
    closeDeleteQuizModal();
    filterQuizzes();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Delete';
  }
};

// ── Util ──────────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadFolders();
