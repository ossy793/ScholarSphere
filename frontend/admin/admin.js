import { api, getToken, getUser } from '../js/api.js';
import { renderLayout } from '../js/layout.js';
import { initCustomerService } from './customer-service.js';

// ── Admin guard ───────────────────────────────────────────────────────────────
if (!getToken()) {
  window.location.href = '../index.html';
  throw new Error('unauthenticated');
}
const _me = getUser();
if (!_me?.is_admin) {
  window.location.href = '../dashboard.html';
  throw new Error('not admin');
}

renderLayout('Admin Dashboard', 'Admin', '../');

// ── State ─────────────────────────────────────────────────────────────────────
let currentPage   = 1;
let totalPages    = 1;
let currentSearch = '';
let currentFilter = 'all';
let searchTimer   = null;
let deleteTarget  = null;   // { id, name } pending deletion
let notifTarget   = null;   // { id, name } | null = broadcast

let lbData        = null;   // cached leaderboard API response
let activeLbMetric = 'brainstorm_time';

// ── Boot ──────────────────────────────────────────────────────────────────────
loadStats();
loadUsers();
loadLeaderboards();
loadUniversities();
loadNotificationHistory();

// ── Stats ─────────────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const s = await api.get('/admin/stats');
    document.getElementById('stats-row').innerHTML = `
      <div class="admin-stat-card">
        <div class="stat-label">Total Users</div>
        <div class="stat-value">${s.total_users}</div>
        <div class="stat-sub">${s.new_users_today} joined today</div>
      </div>
      <div class="admin-stat-card green">
        <div class="stat-label">Active</div>
        <div class="stat-value">${s.active_users}</div>
        <div class="stat-sub">${pct(s.active_users, s.total_users)}% of users</div>
      </div>
      <div class="admin-stat-card red">
        <div class="stat-label">Inactive</div>
        <div class="stat-value">${s.inactive_users}</div>
      </div>
      <div class="admin-stat-card purple">
        <div class="stat-label">Premium</div>
        <div class="stat-value">${s.premium_users}</div>
        <div class="stat-sub">${pct(s.premium_users, s.total_users)}% conversion</div>
      </div>
      <div class="admin-stat-card blue">
        <div class="stat-label">Quizzes</div>
        <div class="stat-value">${s.total_quizzes}</div>
        <div class="stat-sub">${s.total_questions} questions total</div>
      </div>
      <div class="admin-stat-card orange">
        <div class="stat-label">Attempts</div>
        <div class="stat-value">${s.total_attempts}</div>
      </div>
      <div class="admin-stat-card teal">
        <div class="stat-label">Brainstorm Sessions</div>
        <div class="stat-value">${s.total_brainstorm_sessions}</div>
        <div class="stat-sub">Total: ${s.total_brainstorm_time}</div>
      </div>
      <div class="admin-stat-card indigo">
        <div class="stat-label">Avg Session Duration</div>
        <div class="stat-value" style="font-size:1.4rem">${s.avg_brainstorm_duration}</div>
        <div class="stat-sub">time reading per session</div>
      </div>
      <div class="admin-stat-card blue">
        <div class="stat-label">Push Subscribers</div>
        <div class="stat-value">${s.push_subscribers ?? 0}</div>
        <div class="stat-sub">devices opted in</div>
      </div>
    `;
    const subCountEl = document.getElementById('push-sub-count');
    if (subCountEl) subCountEl.textContent = s.push_subscribers ?? 0;
  } catch (err) {
    document.getElementById('stats-row').innerHTML =
      `<div class="admin-stat-card red" style="grid-column:1/-1">
        <div class="stat-label">Stats Error</div>
        <div style="font-size:.9rem;margin-top:6px;color:var(--danger)">${err.message}</div>
      </div>`;
  }
}

function pct(part, total) {
  if (!total) return 0;
  return Math.round((part / total) * 100);
}

// ── User list ─────────────────────────────────────────────────────────────────
async function loadUsers() {
  const tbody = document.getElementById('user-tbody');
  tbody.innerHTML = '<tr><td colspan="6" class="table-loading">Loading…</td></tr>';

  try {
    const params = new URLSearchParams({
      page:   currentPage,
      limit:  20,
      filter: currentFilter,
    });
    if (currentSearch) params.set('search', currentSearch);

    const data = await api.get(`/admin/users?${params}`);
    totalPages = data.pages || 1;

    renderTable(data.users, data.total);
    renderPagination(data.total, data.page, data.pages);
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="table-empty">Failed to load users: ${escHtml(err.message)}</td></tr>`;
  }
}

function renderTable(users, total) {
  const tbody = document.getElementById('user-tbody');

  if (!users.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No users found.</td></tr>';
    return;
  }

  tbody.innerHTML = users.map(u => {
    const initials = u.full_name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
    const joined   = new Date(u.created_at).toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' });

    const planBadge   = u.is_premium
      ? '<span class="badge badge-pro">PRO</span>'
      : '<span class="badge badge-free">FREE</span>';

    const statusBadge = u.is_active
      ? '<span class="badge badge-active">Active</span>'
      : '<span class="badge badge-inactive">Inactive</span>';

    const adminBadge  = u.is_admin ? ' <span class="badge badge-admin" title="Admin">Admin</span>' : '';

    const toggleBtn = u.is_active
      ? `<button class="btn-icon deactivate" title="Deactivate user"
           onclick="event.stopPropagation();toggleActive('${u.id}', '${escAttr(u.full_name)}', false)">⏸</button>`
      : `<button class="btn-icon activate" title="Activate user"
           onclick="event.stopPropagation();toggleActive('${u.id}', '${escAttr(u.full_name)}', true)">▶</button>`;

    const msgBtn = `<button class="btn-icon" title="Send notification"
         onclick="event.stopPropagation();openUserNotifModal('${u.id}', '${escAttr(u.full_name)}')">✉️</button>`;

    const deleteBtn = u.is_admin
      ? ''
      : `<button class="btn-icon delete" title="Delete user"
             onclick="event.stopPropagation();openDeleteModal('${u.id}', '${escAttr(u.full_name)}')">🗑</button>`;

    return `
      <tr id="row-${u.id}" onclick="openDetailModal('${u.id}')" style="cursor:pointer" title="Click to view details">
        <td>
          <div class="user-cell">
            <div class="user-avatar">${escHtml(initials)}</div>
            <div>
              <div class="user-name">${escHtml(u.full_name)}${adminBadge}</div>
              <div class="user-email">${escHtml(u.email)}</div>
            </div>
          </div>
        </td>
        <td>${planBadge}</td>
        <td>${statusBadge}</td>
        <td style="white-space:nowrap;color:var(--text-muted)">${joined}</td>
        <td><span class="activity-text">${u.total_quizzes} quizzes · ${u.total_attempts} attempts</span></td>
        <td><div class="action-btns">${msgBtn}${toggleBtn}${deleteBtn}</div></td>
      </tr>
    `;
  }).join('');
}

function renderPagination(total, page, pages) {
  document.getElementById('pagination-info').textContent =
    `${total} user${total !== 1 ? 's' : ''} · Page ${page} of ${pages}`;
  document.getElementById('prev-btn').disabled = page <= 1;
  document.getElementById('next-btn').disabled = page >= pages;
}

// ── Search (debounced) ────────────────────────────────────────────────────────
window.onSearch = function (val) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    currentSearch = val.trim();
    currentPage   = 1;
    loadUsers();
  }, 400);
};

// ── Filter ────────────────────────────────────────────────────────────────────
window.onFilter = function (val) {
  currentFilter = val;
  currentPage   = 1;
  loadUsers();
};

// ── Pagination ────────────────────────────────────────────────────────────────
window.changePage = function (dir) {
  const next = currentPage + dir;
  if (next < 1 || next > totalPages) return;
  currentPage = next;
  loadUsers();
};

// ── Activate / Deactivate ─────────────────────────────────────────────────────
window.toggleActive = async function (userId, name, activate) {
  const action = activate ? 'activate' : 'deactivate';
  try {
    const data = await api.patch(`/admin/users/${userId}/${action}`, {});
    showToast(data.message, 'success');
    loadUsers();
    loadStats();
  } catch (err) {
    showToast(err.message, 'error');
  }
};

// ── User Detail Modal ─────────────────────────────────────────────────────────
window.openDetailModal = async function (userId) {
  document.getElementById('detail-modal').classList.remove('hidden');
  document.getElementById('detail-content').innerHTML =
    '<div style="text-align:center;padding:32px;color:var(--text-muted)">Loading…</div>';

  try {
    const u = await api.get(`/admin/users/${userId}/detail`);

    const fmtDate = (iso) => iso
      ? new Date(iso).toLocaleString('en-GB', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' })
      : '—';

    const planBadge = u.is_premium
      ? '<span class="badge badge-pro">PRO</span>'
      : '<span class="badge badge-free">FREE</span>';
    const statusBadge = u.is_active
      ? '<span class="badge badge-active">Active</span>'
      : '<span class="badge badge-inactive">Inactive</span>';

    document.getElementById('detail-content').innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
        <div class="user-avatar" style="width:48px;height:48px;font-size:1.1rem">
          ${escHtml(u.full_name.split(' ').map(n=>n[0]).join('').toUpperCase().slice(0,2))}
        </div>
        <div>
          <div style="font-weight:700;font-size:1rem">${escHtml(u.full_name)}</div>
          <div style="font-size:0.82rem;color:var(--text-muted)">${escHtml(u.email)}</div>
          <div style="display:flex;gap:6px;margin-top:5px">${planBadge}${statusBadge}${u.is_admin ? '<span class="badge badge-admin">Admin</span>' : ''}</div>
        </div>
      </div>

      <div class="detail-section-title">Academic Profile</div>
      <div class="detail-grid">
        <div class="detail-field">
          <span class="detail-label">Department</span>
          <span class="detail-value">${escHtml(u.department || '—')}</span>
        </div>
        <div class="detail-field">
          <span class="detail-label">University</span>
          <span class="detail-value">${escHtml(u.university || '—')}</span>
        </div>
        <div class="detail-field">
          <span class="detail-label">Level</span>
          <span class="detail-value">${escHtml(u.level || '—')}</span>
        </div>
        <div class="detail-field">
          <span class="detail-label">Joined</span>
          <span class="detail-value">${fmtDate(u.created_at)}</span>
        </div>
        <div class="detail-field">
          <span class="detail-label">Last Login</span>
          <span class="detail-value">${fmtDate(u.last_login_at)}</span>
        </div>
        <div class="detail-field">
          <span class="detail-label">Premium Since</span>
          <span class="detail-value">${fmtDate(u.premium_activated_at)}</span>
        </div>
      </div>

      <div class="detail-section-title">Activity</div>
      <div class="detail-stat-row">
        <div class="detail-stat">
          <div class="detail-stat-val">${u.total_quizzes}</div>
          <div class="detail-stat-lbl">Quizzes Created</div>
        </div>
        <div class="detail-stat">
          <div class="detail-stat-val">${u.total_attempts}</div>
          <div class="detail-stat-lbl">Quiz Attempts</div>
        </div>
        <div class="detail-stat">
          <div class="detail-stat-val">${u.average_score !== null ? u.average_score + '%' : '—'}</div>
          <div class="detail-stat-lbl">Avg Score</div>
        </div>
      </div>
      <div class="detail-stat-row">
        <div class="detail-stat">
          <div class="detail-stat-val">${u.brainstorm_sessions}</div>
          <div class="detail-stat-lbl">Brainstorm Sessions</div>
        </div>
        <div class="detail-stat">
          <div class="detail-stat-val">${u.brainstorm_messages}</div>
          <div class="detail-stat-lbl">Messages with UrPadi</div>
        </div>
        <div class="detail-stat">
          <div class="detail-stat-val" style="font-size:1.1rem">${u.brainstorm_total_time}</div>
          <div class="detail-stat-lbl">Total Brainstorm Time</div>
        </div>
      </div>

      ${u.brainstorm_session_list?.length ? `
      <div class="detail-section-title" style="margin-top:16px">Recent Brainstorm Sessions</div>
      <div class="bs-session-list">
        ${u.brainstorm_session_list.map(s => `
          <div class="bs-session-row">
            <div class="bs-session-title">${escHtml(s.title)}</div>
            <div class="bs-session-meta">
              <span class="bs-duration-badge">${escHtml(s.duration)}</span>
              <span style="color:var(--text-muted);font-size:0.78rem">${s.messages} msg${s.messages !== 1 ? 's' : ''}</span>
              <span style="color:var(--text-muted);font-size:0.75rem;margin-left:auto">${fmtDate(s.created_at)}</span>
            </div>
          </div>
        `).join('')}
      </div>
      ` : ''}

      ${!u.is_admin ? `
      <div class="detail-actions">
        <button class="btn btn-outline btn-sm" onclick="closeDetailModal();openUserNotifModal('${u.id}', '${escAttr(u.full_name)}')">
          ✉️ Send Message
        </button>
        ${u.is_active
          ? `<button class="btn btn-sm" style="background:#FEF3C7;color:#92400E;border:1px solid #FDE68A" onclick="closeDetailModal();toggleActive('${u.id}','${escAttr(u.full_name)}',false)">⏸ Deactivate</button>`
          : `<button class="btn btn-sm" style="background:#DCFCE7;color:#166534;border:1px solid #BBF7D0" onclick="closeDetailModal();toggleActive('${u.id}','${escAttr(u.full_name)}',true)">▶ Activate</button>`
        }
        <button class="btn btn-sm" style="background:#FEE2E2;color:#991B1B;border:1px solid #FECACA" onclick="closeDetailModal();openDeleteModal('${u.id}','${escAttr(u.full_name)}')">
          🗑 Delete
        </button>
      </div>
      ` : ''}
    `;
  } catch (err) {
    document.getElementById('detail-content').innerHTML =
      `<div style="text-align:center;padding:24px;color:var(--danger)">Failed to load: ${escHtml(err.message)}</div>`;
  }
};

window.closeDetailModal = function () {
  document.getElementById('detail-modal').classList.add('hidden');
};

document.getElementById('detail-modal').addEventListener('click', function (e) {
  if (e.target === this) closeDetailModal();
});

// ── Notification Modals ───────────────────────────────────────────────────────
window.openBroadcastModal = function () {
  notifTarget = null;
  document.getElementById('notif-target-label').textContent = '📢 This message will be sent to ALL users.';
  document.getElementById('notif-title').value   = '';
  document.getElementById('notif-message').value = '';
  document.getElementById('send-notif-btn').textContent = 'Send to All';
  document.getElementById('notif-modal').classList.remove('hidden');
};

window.openUserNotifModal = function (userId, name) {
  notifTarget = { id: userId, name };
  document.getElementById('notif-target-label').textContent = `✉️ Sending to: ${name}`;
  document.getElementById('notif-title').value   = '';
  document.getElementById('notif-message').value = '';
  document.getElementById('send-notif-btn').textContent = 'Send';
  document.getElementById('notif-modal').classList.remove('hidden');
};

window.closeNotifModal = function () {
  document.getElementById('notif-modal').classList.add('hidden');
  notifTarget = null;
};

window.sendNotification = async function () {
  const title   = document.getElementById('notif-title').value.trim();
  const message = document.getElementById('notif-message').value.trim();
  if (!title || !message) {
    showToast('Please fill in both title and message.', 'error');
    return;
  }

  const btn = document.getElementById('send-notif-btn');
  btn.disabled    = true;
  btn.textContent = 'Sending…';

  try {
    const payload = { title, message };
    if (notifTarget) payload.target_user_id = notifTarget.id;
    const data = await api.post('/admin/notifications', payload);
    closeNotifModal();
    showToast(data.message, 'success');
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.disabled    = false;
    btn.textContent = notifTarget ? 'Send' : 'Send to All';
  }
};

document.getElementById('notif-modal').addEventListener('click', function (e) {
  if (e.target === this) closeNotifModal();
});

// ── Delete modal ──────────────────────────────────────────────────────────────
window.openDeleteModal = function (userId, name) {
  deleteTarget = { id: userId, name };
  document.getElementById('delete-modal-text').textContent =
    `Permanently delete "${name}" and all their quizzes, attempts and data? This cannot be undone.`;
  document.getElementById('delete-modal').classList.remove('hidden');
};

window.closeDeleteModal = function () {
  deleteTarget = null;
  document.getElementById('delete-modal').classList.add('hidden');
};

window.confirmDelete = async function () {
  if (!deleteTarget) return;
  const { id, name } = deleteTarget;
  const btn = document.getElementById('confirm-delete-btn');
  btn.disabled    = true;
  btn.textContent = 'Deleting…';

  try {
    await api.del(`/admin/users/${id}`);
    closeDeleteModal();
    showToast(`${name} has been deleted.`, 'success');
    loadUsers();
    loadStats();
  } catch (err) {
    showToast(err.message, 'error');
    btn.disabled    = false;
    btn.textContent = 'Delete';
  }
};

// Close modal on overlay click
document.getElementById('delete-modal').addEventListener('click', function (e) {
  if (e.target === this) closeDeleteModal();
});

// ── Toast ─────────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, type = 'success') {
  const el = document.getElementById('admin-toast');
  el.textContent = msg;
  el.className   = `admin-toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3500);
}

// ── Leaderboard ───────────────────────────────────────────────────────────────

const LB_LABELS = {
  brainstorm_time:     { title: 'Brainstorm Time',   col: 'Time Spent',      tab: 'Brainstorm Time' },
  brainstorm_messages: { title: 'AI Messages Sent',  col: 'Messages',        tab: 'AI Messages' },
  quizzes_generated:   { title: 'Quizzes Generated', col: 'Quizzes Created', tab: 'Quizzes Generated' },
  avg_score:           { title: 'Average Score',     col: 'Avg Score',       tab: 'Avg Score' },
};

async function loadLeaderboards() {
  const wrap = document.getElementById('lb-wrap');
  try {
    lbData = await api.get('/admin/leaderboard?limit=20');
    renderLeaderboard(activeLbMetric);
  } catch (err) {
    if (wrap) wrap.innerHTML =
      `<div style="padding:24px;text-align:center;color:var(--danger)">Failed to load leaderboard: ${escHtml(err.message)}</div>`;
  }
}

window.switchLeaderboard = function (metric) {
  activeLbMetric = metric;
  document.querySelectorAll('.lb-tab').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.trim() === LB_LABELS[metric].tab);
  });
  renderLeaderboard(metric);
};

function renderLeaderboard(metric) {
  const wrap = document.getElementById('lb-wrap');
  if (!wrap || !lbData) return;

  const rows    = lbData[metric] || [];
  const resets  = lbData.reset_timestamps || {};
  const resetAt = resets[metric];
  const label   = LB_LABELS[metric];

  const resetLabel = resetAt
    ? `Reset on ${new Date(resetAt).toLocaleString('en-GB', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' })}`
    : 'Showing all-time data';

  let html = `
    <div class="lb-meta">
      <span>${resetLabel}</span>
      <button class="lb-reset-btn" onclick="resetLeaderboard('${metric}')">Reset Leaderboard</button>
    </div>
  `;

  if (!rows.length) {
    html += `<div class="table-empty">No data yet for this leaderboard.</div>`;
  } else {
    html += `
      <table class="admin-table">
        <thead>
          <tr>
            <th style="width:48px">Rank</th>
            <th>User</th>
            <th>${escHtml(label.col)}</th>
            ${metric === 'avg_score' ? '<th>Attempts</th>' : ''}
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => {
            const rankClass = r.rank <= 3 ? `lb-rank-${r.rank}` : 'lb-rank-n';
            const rankIcon  = r.rank === 1 ? '&#127941;' : r.rank === 2 ? '&#129352;' : r.rank === 3 ? '&#129353;' : r.rank;
            const initials  = r.full_name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
            return `
              <tr>
                <td><div class="lb-rank-badge ${rankClass}">${rankIcon}</div></td>
                <td>
                  <div class="user-cell">
                    <div class="user-avatar">${escHtml(initials)}</div>
                    <div>
                      <div class="user-name">${escHtml(r.full_name)}</div>
                      <div class="user-email">${escHtml(r.email)}</div>
                    </div>
                  </div>
                </td>
                <td><span class="lb-value">${escHtml(r.display)}</span></td>
                ${metric === 'avg_score' ? `<td style="color:var(--text-muted);font-size:0.82rem">${r.attempts} attempt${r.attempts !== 1 ? 's' : ''}</td>` : ''}
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  }

  wrap.innerHTML = html;
}

window.resetLeaderboard = async function (metric) {
  const label = LB_LABELS[metric]?.title || metric;
  if (!confirm(`Reset the "${label}" leaderboard? Rankings will restart from now. This cannot be undone.`)) return;
  try {
    const data = await api.post(`/admin/leaderboard/${metric}/reset`, {});
    showToast(data.message, 'success');
    await loadLeaderboards();
  } catch (err) {
    showToast(err.message, 'error');
  }
};

// ── Universities ──────────────────────────────────────────────────────────────

async function loadUniversities() {
  const wrap = document.getElementById('uni-wrap');
  try {
    const data = await api.get('/admin/universities');
    if (!data.length) {
      wrap.innerHTML = '<div class="table-empty">No university data yet. Users will need to fill in their university on registration.</div>';
      return;
    }

    const cards = data.map(u => {
      const isUnspecified = u.university === 'Unspecified';
      const levelRows = Object.entries(u.levels)
        .sort(([, a], [, b]) => b - a)
        .map(([lvl, cnt]) => `
          <div class="uni-level-row">
            <span class="uni-level-name">${escHtml(lvl)}</span>
            <span class="uni-level-cnt">${cnt} student${cnt !== 1 ? 's' : ''}</span>
          </div>
        `).join('');

      return `
        <div class="uni-card${isUnspecified ? ' uni-unspecified' : ''}">
          <div class="uni-card-header">
            <span class="uni-card-name" title="${escHtml(u.university)}">${escHtml(u.university)}</span>
            <span class="uni-card-count">${u.total} user${u.total !== 1 ? 's' : ''}</span>
          </div>
          <div class="uni-levels">${levelRows}</div>
        </div>
      `;
    }).join('');

    wrap.innerHTML = `<div class="uni-grid">${cards}</div>`;
  } catch (err) {
    if (wrap) wrap.innerHTML =
      `<div style="padding:24px;text-align:center;color:var(--danger)">Failed to load universities: ${escHtml(err.message)}</div>`;
  }
}

// ── Notification History ──────────────────────────────────────────────────────

async function loadNotificationHistory() {
  const tbody = document.getElementById('notif-history-tbody');
  if (!tbody) return;
  try {
    const data = await api.get('/admin/notifications');
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="table-empty">No notifications sent yet.</td></tr>';
      return;
    }
    tbody.innerHTML = data.map(n => {
      const sent = new Date(n.created_at).toLocaleString('en-GB', {
        day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
      });
      const target = n.is_broadcast
        ? '<span class="badge badge-primary">All Users</span>'
        : '<span class="badge badge-free">Targeted</span>';
      return `
        <tr>
          <td style="font-weight:600">${escHtml(n.title)}</td>
          <td style="color:var(--text-muted);font-size:0.85rem;max-width:280px">${escHtml(n.message)}</td>
          <td>${target}</td>
          <td style="color:var(--text-muted);font-size:0.82rem;white-space:nowrap">${sent}</td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="4" style="text-align:center;color:var(--danger)">Failed: ${escHtml(err.message)}</td></tr>`;
  }
}

// ── Quick Push ─────────────────────────────────────────────────────────────────

const QUICK_PUSH = {
  daily:   { title: 'Time to study! 📚',        message: "Good morning! Open Pritis and practice a quiz today. Consistency is key!" },
  streak:  { title: "Don't break your streak! 🔥", message: "You haven't practiced today. Open Pritis and keep your streak alive!" },
  feature: { title: 'New on Pritis 🚀',          message: "We've added new features to help you study smarter. Check it out!" },
};

window.sendQuickPush = function (type) {
  const preset = QUICK_PUSH[type];
  if (!preset) return;
  notifTarget = null;
  document.getElementById('notif-target-label').textContent = '📢 This message will be sent to ALL users (in-app + push).';
  document.getElementById('notif-title').value   = preset.title;
  document.getElementById('notif-message').value = preset.message;
  document.getElementById('send-notif-btn').textContent = 'Send to All';
  // Override send to also reload history
  _quickPushPending = true;
  document.getElementById('notif-modal').classList.remove('hidden');
};

let _quickPushPending = false;

// Patch sendNotification to reload notification history after sending
const _origSend = window.sendNotification;
window.sendNotification = async function () {
  await _origSend();
  setTimeout(() => loadNotificationHistory(), 500);
  _quickPushPending = false;
};

// ── Admin tab switching ───────────────────────────────────────────────────────
window.switchAdminTab = function (tab) {
  // Update tab button states
  document.querySelectorAll('.admin-dash-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });

  // Show/hide panels — must remove the .hidden class (which uses !important)
  const usersPanel   = document.getElementById('tab-users');
  const supportPanel = document.getElementById('tab-support');

  if (tab === 'support') {
    usersPanel.classList.add('hidden');
    supportPanel.classList.remove('hidden');
    supportPanel.style.display = 'flex';
    // Lazy-init the customer service dashboard
    initCustomerService();
  } else {
    supportPanel.classList.add('hidden');
    supportPanel.style.display = 'none';
    usersPanel.classList.remove('hidden');
    usersPanel.style.display   = '';
  }
};

// ── PDF Export ────────────────────────────────────────────────────────────────
window.downloadUsersPdf = async function downloadUsersPdf() {
  const btn = document.getElementById('pdf-download-btn');
  btn.disabled = true;
  btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"
    style="animation:spin .8s linear infinite">
    <path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" opacity=".2"/>
    <path d="M21 12a9 9 0 00-9-9"/>
  </svg> Generating…`;

  try {
    const buffer = await api.getBuffer('/admin/export-users/pdf');
    if (!buffer) throw new Error('Server returned an error. Please try again.');

    const blob = new Blob([buffer], { type: 'application/pdf' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `pritis-users-${new Date().toISOString().slice(0, 10)}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast('PDF downloaded successfully!', 'success');
  } catch (err) {
    showToast(err.message || 'PDF export failed.', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
      <polyline points="7 10 12 15 17 10"/>
      <line x1="12" y1="15" x2="12" y2="3"/>
    </svg> Download PDF`;
  }
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escAttr(str) {
  return String(str).replace(/'/g, "\\'").replace(/"/g, '&quot;');
}
