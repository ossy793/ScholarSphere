import { requireAuth, api } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('Not authenticated');
renderLayout('Study Strategy', 'Study Strategy');

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function toast(msg, duration = 2500) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), duration);
}

// ── Tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.strategy-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.strategy-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.strategy-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('panel-' + btn.dataset.tab).classList.add('active');

    if (btn.dataset.tab === 'notes' && !_notesLoaded) _initNotes();
    if (btn.dataset.tab === 'timetable' && !_schedLoaded) _loadSchedule();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// NOTES
// ═══════════════════════════════════════════════════════════════════════════════
let _notesLoaded   = false;
let _allNotes      = [];        // [{id, folder, title, content, updated_at}]
let _folders       = [];        // sorted folder names
let _activeFolder  = '__all__'; // '__all__' | '__none__' | 'folder name'
let _activeNoteId  = null;
let _saveTimer     = null;
let _isDirty       = false;

async function _initNotes() {
  _notesLoaded = true;
  await _loadFolders();
  await _loadNotes();
  _renderFolderList();
  _renderNoteList();
}

async function _loadFolders() {
  try {
    _folders = await api.get('/study/notes/folders');
  } catch (_) { _folders = []; }
}

async function _loadNotes(folder) {
  let url = '/study/notes';
  if (folder !== undefined) {
    url += folder ? `?folder=${encodeURIComponent(folder)}` : '?folder=__none__';
  }
  try {
    _allNotes = await api.get(url);
  } catch (e) {
    toast('Failed to load notes: ' + e.message);
  }
}

function _renderFolderList() {
  const el = document.getElementById('folder-list');
  const allActive    = _activeFolder === '__all__';
  const noneActive   = _activeFolder === '__none__';

  let html = `
    <div class="notes-folder-item ${allActive ? 'active' : ''}" data-folder="__all__">
      All Notes
      <span class="folder-badge">${_allNotes.length}</span>
    </div>
  `;

  _folders.forEach(f => {
    const count = _allNotes.filter(n => n.folder === f).length;
    const active = _activeFolder === f;
    html += `
      <div class="notes-folder-item ${active ? 'active' : ''}" data-folder="${_esc(f)}">
        ${_esc(f)}
        <span class="folder-badge">${count}</span>
      </div>
    `;
  });

  html += `
    <div class="notes-folder-item ${noneActive ? 'active' : ''}" data-folder="__none__">
      No Folder
    </div>
  `;

  el.innerHTML = html;

  el.querySelectorAll('.notes-folder-item').forEach(item => {
    item.addEventListener('click', async () => {
      _activeFolder = item.dataset.folder;
      if (_activeFolder === '__all__') {
        await _loadNotes();
      } else {
        await _loadNotes(_activeFolder === '__none__' ? null : _activeFolder);
      }
      _renderFolderList();
      _renderNoteList();
    });
  });
}

function _renderNoteList() {
  // The "note list" lives inside the sidebar, below folders
  let listSection = document.getElementById('note-list-section');
  if (!listSection) {
    listSection = document.createElement('div');
    listSection.id = 'note-list-section';
    listSection.className = 'notes-list-section';
    document.getElementById('folder-list').insertAdjacentElement('afterend', listSection);
  }

  if (!_allNotes.length) {
    listSection.innerHTML = '<div class="note-list-item" style="font-style:italic">No notes yet</div>';
    return;
  }

  listSection.innerHTML = _allNotes.map(n => `
    <div class="note-list-item ${n.id === _activeNoteId ? 'active' : ''}"
         data-note-id="${n.id}" title="${_esc(n.title)}">
      ${_esc(n.title)}
    </div>
  `).join('');

  listSection.querySelectorAll('.note-list-item').forEach(item => {
    item.addEventListener('click', () => {
      const note = _allNotes.find(n => n.id === item.dataset.noteId);
      if (note) _openNote(note);
    });
  });
}

function _openNote(note) {
  _activeNoteId = note.id;
  _isDirty = false;
  _renderNoteList();

  const editor = document.getElementById('notes-editor');
  editor.innerHTML = `
    <div class="notes-editor-toolbar">
      <input class="note-title-input" id="note-title" value="${_esc(note.title)}"
             placeholder="Note title" maxlength="300" />
      <input class="note-folder-input" id="note-folder" value="${_esc(note.folder || '')}"
             placeholder="Folder (optional)" maxlength="100" />
      <span class="note-save-status" id="save-status">Saved</span>
      <button class="note-delete-btn" id="btn-delete-note">Delete</button>
    </div>
    <textarea class="note-content-area" id="note-content" placeholder="Start writing…">${_escTextarea(note.content)}</textarea>
  `;

  const titleEl   = document.getElementById('note-title');
  const folderEl  = document.getElementById('note-folder');
  const contentEl = document.getElementById('note-content');

  const markDirty = () => {
    _isDirty = true;
    document.getElementById('save-status').textContent = 'Unsaved…';
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(() => _autoSave(), 1200);
  };

  titleEl.addEventListener('input',   markDirty);
  folderEl.addEventListener('input',  markDirty);
  contentEl.addEventListener('input', markDirty);

  document.getElementById('btn-delete-note').addEventListener('click', () => _deleteNote(note.id));
}

async function _autoSave() {
  if (!_activeNoteId || !_isDirty) return;
  const statusEl = document.getElementById('save-status');
  if (statusEl) statusEl.textContent = 'Saving…';

  const title   = document.getElementById('note-title')?.value || 'Untitled';
  const folder  = document.getElementById('note-folder')?.value.trim() || null;
  const content = document.getElementById('note-content')?.value || '';

  try {
    const updated = await api.patch(`/study/notes/${_activeNoteId}`, { title, folder, content });
    // Update local cache
    const idx = _allNotes.findIndex(n => n.id === _activeNoteId);
    if (idx !== -1) _allNotes[idx] = { ..._allNotes[idx], ...updated };
    _isDirty = false;
    if (statusEl) statusEl.textContent = 'Saved';

    // Refresh folders + list without losing position
    await _loadFolders();
    _renderFolderList();
    _renderNoteList();
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Error saving';
    toast('Save failed: ' + e.message);
  }
}

async function _deleteNote(noteId) {
  if (!confirm('Delete this note? This cannot be undone.')) return;
  try {
    await api.del(`/study/notes/${noteId}`);
    _allNotes = _allNotes.filter(n => n.id !== noteId);
    _activeNoteId = null;
    document.getElementById('notes-editor').innerHTML = `
      <div class="notes-empty" id="notes-empty-state">
        <div class="empty-icon">📝</div>
        <p>Select a note from the sidebar or create a new one.</p>
        <button class="notes-new-btn" onclick="document.getElementById('btn-new-note').click()">Create note</button>
      </div>`;
    await _loadFolders();
    _renderFolderList();
    _renderNoteList();
    toast('Note deleted.');
  } catch (e) {
    toast('Delete failed: ' + e.message);
  }
}

// New note button
document.getElementById('btn-new-note').addEventListener('click', async () => {
  try {
    const note = await api.post('/study/notes', {
      title: 'Untitled',
      content: '',
      folder: _activeFolder !== '__all__' && _activeFolder !== '__none__' ? _activeFolder : null,
    });
    _allNotes.unshift(note);
    await _loadFolders();
    _renderFolderList();
    _renderNoteList();
    _openNote(note);
  } catch (e) {
    toast('Failed to create note: ' + e.message);
  }
});

// Initialise notes on load (default tab is notes)
_initNotes();


// ═══════════════════════════════════════════════════════════════════════════════
// GPA CALCULATOR
// ═══════════════════════════════════════════════════════════════════════════════
const GPA_KEY = 'pritis_gpa_courses';
const GRADE_POINTS = { A: 5, B: 4, C: 3, D: 2, E: 1, F: 0 };

let _gpaCourses = _loadGpaFromStorage();

function _loadGpaFromStorage() {
  try {
    return JSON.parse(localStorage.getItem(GPA_KEY)) || [_blankCourse()];
  } catch (_) {
    return [_blankCourse()];
  }
}

function _blankCourse() {
  return { name: '', units: '', grade: 'A' };
}

function _saveGpaToStorage() {
  localStorage.setItem(GPA_KEY, JSON.stringify(_gpaCourses));
}

function _calcGpa() {
  let totalPoints = 0, totalUnits = 0;
  _gpaCourses.forEach(c => {
    const u = parseFloat(c.units);
    if (!u || !c.grade || !(c.grade in GRADE_POINTS)) return;
    const pts = GRADE_POINTS[c.grade];
    totalPoints += pts * u;
    totalUnits  += u;
  });
  const gpa = totalUnits > 0 ? (totalPoints / totalUnits) : 0;
  return { gpa, totalUnits };
}

function _gpaClass(gpa) {
  if (gpa >= 4.5) return 'First Class';
  if (gpa >= 3.5) return 'Second Class Upper';
  if (gpa >= 2.4) return 'Second Class Lower';
  if (gpa >= 1.5) return 'Third Class';
  if (gpa >= 1.0) return 'Pass';
  return 'Fail';
}

function _renderGpa() {
  const tbody = document.getElementById('gpa-course-rows');
  tbody.innerHTML = _gpaCourses.map((c, i) => `
    <tr>
      <td><input type="text" value="${_esc(c.name)}" placeholder="Course code"
                 data-idx="${i}" data-field="name" /></td>
      <td style="width:60px"><input type="number" value="${_esc(c.units)}" min="1" max="6"
                 placeholder="3" data-idx="${i}" data-field="units" /></td>
      <td style="width:70px">
        <select data-idx="${i}" data-field="grade">
          ${['A','B','C','D','E','F'].map(g =>
            `<option ${c.grade === g ? 'selected' : ''}>${g}</option>`).join('')}
        </select>
      </td>
      <td style="width:36px">
        <button class="remove-row-btn" data-idx="${i}" title="Remove">✕</button>
      </td>
    </tr>
  `).join('');

  // Bind inputs
  tbody.querySelectorAll('input, select').forEach(el => {
    el.addEventListener('input', () => {
      const idx   = parseInt(el.dataset.idx, 10);
      const field = el.dataset.field;
      _gpaCourses[idx][field] = el.value;
      _updateGpaResult();
    });
  });

  // Bind remove buttons
  tbody.querySelectorAll('.remove-row-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx, 10);
      _gpaCourses.splice(idx, 1);
      if (!_gpaCourses.length) _gpaCourses.push(_blankCourse());
      _renderGpa();
      _updateGpaResult();
    });
  });

  _updateGpaResult();
}

function _updateGpaResult() {
  const { gpa, totalUnits } = _calcGpa();
  document.getElementById('gpa-value').textContent  = gpa.toFixed(2);
  document.getElementById('gpa-class').textContent  = _gpaClass(gpa);
  document.getElementById('gpa-total').textContent  = `${totalUnits} credit unit${totalUnits !== 1 ? 's' : ''}`;
}

document.getElementById('btn-add-course').addEventListener('click', () => {
  _gpaCourses.push(_blankCourse());
  _renderGpa();
});

document.getElementById('btn-save-gpa').addEventListener('click', () => {
  _saveGpaToStorage();
  toast('GPA data saved locally.');
});

document.getElementById('btn-clear-gpa').addEventListener('click', () => {
  if (!confirm('Clear all courses?')) return;
  _gpaCourses = [_blankCourse()];
  _renderGpa();
  _saveGpaToStorage();
});

_renderGpa();


// ═══════════════════════════════════════════════════════════════════════════════
// TIMETABLE
// ═══════════════════════════════════════════════════════════════════════════════
let _schedLoaded  = false;
let _schedules    = [];

async function _loadSchedule() {
  _schedLoaded = true;
  try {
    _schedules = await api.get('/study/schedule?upcoming_only=true');
    _renderSchedule();
  } catch (e) {
    toast('Failed to load schedule: ' + e.message);
  }
}

function _renderSchedule() {
  const listEl = document.getElementById('schedule-list');
  if (!_schedules.length) {
    listEl.innerHTML = `
      <div class="sched-empty">
        <div class="empty-icon">📅</div>
        <p>No upcoming sessions. Add one above to get started!</p>
      </div>`;
    return;
  }

  listEl.innerHTML = _schedules.map(s => {
    const dt  = new Date(s.scheduled_time);
    const fmt = dt.toLocaleString('en-GB', {
      weekday: 'short', day: '2-digit', month: 'short',
      year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
    const dur = s.duration_minutes ? `${s.duration_minutes} min` : '';
    return `
      <div class="schedule-card" data-sched-id="${s.id}">
        <div class="schedule-card-icon">📚</div>
        <div class="schedule-card-body">
          <div class="schedule-card-course">${_esc(s.course)}</div>
          <div class="schedule-card-time">${fmt}</div>
          ${s.description ? `<div class="schedule-card-desc">${_esc(s.description)}</div>` : ''}
          ${dur ? `<div class="schedule-card-duration">${dur}</div>` : ''}
        </div>
        <div class="schedule-card-actions">
          <button class="sched-btn" data-edit="${s.id}">Edit</button>
          <button class="sched-btn danger" data-del="${s.id}">Delete</button>
        </div>
      </div>
    `;
  }).join('');

  listEl.querySelectorAll('[data-edit]').forEach(btn => {
    btn.addEventListener('click', () => _showEditForm(btn.dataset.edit));
  });
  listEl.querySelectorAll('[data-del]').forEach(btn => {
    btn.addEventListener('click', () => _deleteSchedule(btn.dataset.del));
  });
}

function _showEditForm(schedId) {
  const s = _schedules.find(x => x.id === schedId);
  if (!s) return;

  // Remove any existing edit form
  document.querySelectorAll('.sched-edit-row').forEach(el => el.remove());

  const card = document.querySelector(`[data-sched-id="${schedId}"]`);
  if (!card) return;

  // Convert UTC ISO to local datetime-local value
  const localDt = _utcIsoToLocalInput(s.scheduled_time);

  const form = document.createElement('div');
  form.className = 'sched-edit-row';
  form.innerHTML = `
    <input type="text"   id="edit-course"   value="${_esc(s.course)}" placeholder="Course" style="flex:1;min-width:120px" />
    <input type="text"   id="edit-desc"     value="${_esc(s.description || '')}" placeholder="Description" style="flex:1;min-width:120px" />
    <input type="datetime-local" id="edit-time" value="${localDt}" />
    <input type="number" id="edit-dur"      value="${s.duration_minutes || ''}" min="15" max="480" step="15" placeholder="Duration (min)" style="width:130px" />
    <button class="btn-sm btn-primary"  id="btn-save-edit">Save</button>
    <button class="btn-sm btn-outline"  id="btn-cancel-edit">Cancel</button>
  `;
  card.insertAdjacentElement('afterend', form);

  document.getElementById('btn-cancel-edit').addEventListener('click', () => form.remove());

  document.getElementById('btn-save-edit').addEventListener('click', async () => {
    const course = document.getElementById('edit-course').value.trim();
    const desc   = document.getElementById('edit-desc').value.trim() || null;
    const timeV  = document.getElementById('edit-time').value;
    const dur    = parseInt(document.getElementById('edit-dur').value, 10) || null;

    if (!course) { toast('Course is required.'); return; }
    if (!timeV)  { toast('Date & time is required.'); return; }

    const payload = { course, description: desc, duration_minutes: dur };
    // Convert local datetime-local value to UTC ISO
    const utcIso = new Date(timeV).toISOString();
    payload.scheduled_time = utcIso;

    try {
      const updated = await api.patch(`/study/schedule/${schedId}`, payload);
      const idx = _schedules.findIndex(x => x.id === schedId);
      if (idx !== -1) _schedules[idx] = updated;
      form.remove();
      _renderSchedule();
      toast('Session updated.');
    } catch (e) {
      toast('Update failed: ' + (e.message || 'Unknown error'));
    }
  });
}

async function _deleteSchedule(schedId) {
  if (!confirm('Delete this session?')) return;
  try {
    await api.del(`/study/schedule/${schedId}`);
    _schedules = _schedules.filter(s => s.id !== schedId);
    _renderSchedule();
    toast('Session deleted.');
  } catch (e) {
    toast('Delete failed: ' + e.message);
  }
}

document.getElementById('btn-add-sched').addEventListener('click', async () => {
  const course   = document.getElementById('sched-course').value.trim();
  const desc     = document.getElementById('sched-desc').value.trim() || null;
  const timeVal  = document.getElementById('sched-time').value;
  const durVal   = parseInt(document.getElementById('sched-duration').value, 10) || null;

  if (!course)  { toast('Please enter a course name.'); return; }
  if (!timeVal) { toast('Please select a date & time.'); return; }

  const utcIso = new Date(timeVal).toISOString();

  try {
    const created = await api.post('/study/schedule', {
      course,
      description: desc,
      scheduled_time: utcIso,
      duration_minutes: durVal,
    });
    _schedules.push(created);
    _schedules.sort((a, b) => new Date(a.scheduled_time) - new Date(b.scheduled_time));
    _renderSchedule();
    // Clear inputs
    document.getElementById('sched-course').value   = '';
    document.getElementById('sched-desc').value     = '';
    document.getElementById('sched-time').value     = '';
    document.getElementById('sched-duration').value = '';
    toast('Session scheduled!');
  } catch (e) {
    const detail = e.message || 'Unknown error';
    toast('Failed: ' + detail);
  }
});


// ── Helpers ───────────────────────────────────────────────────────────────────
function _esc(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _escTextarea(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function _utcIsoToLocalInput(isoStr) {
  // Convert "2025-04-20T14:00:00Z" → "2025-04-20T16:00" (local)
  const d = new Date(isoStr);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
