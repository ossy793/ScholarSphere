import { getUser, clearToken, api } from './api.js';

// Nav items that require a premium account
const PREMIUM_NAV = new Set([
  'Input Questions', 'AI Generate', 'My Questions', 'Performance',
]);

export function renderLayout(pageTitle, activeNav, basePath = '') {
  const user = getUser();
  if (!user) return;

  const isPremium = !!user.is_premium;

  const initials = user.full_name
    .split(' ')
    .map(n => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  const navItems = [
    { href: basePath + 'dashboard.html',       icon: svgHome(),       label: 'Dashboard' },
    { href: basePath + 'input-questions.html', icon: svgEdit(),       label: 'Input Questions' },
    { href: basePath + 'ai-generate.html',     icon: svgAI(),         label: 'AI Generate' },
    { href: basePath + 'my-questions.html',    icon: svgQuiz(),       label: 'My Questions' },
    { href: basePath + 'brainstorm.html',      icon: svgBrainstorm(), label: 'Brainstorm' },
    { href: basePath + 'performance.html',     icon: svgChart(),      label: 'Performance' },
  ];

  const navHtml = navItems.map(item => {
    const locked  = PREMIUM_NAV.has(item.label) && !isPremium;
    const href    = locked ? basePath + 'upgrade.html' : item.href;
    const classes = ['nav-item', activeNav === item.label ? 'active' : '', locked ? 'locked' : '']
      .filter(Boolean).join(' ');
    const lockBadge = locked ? '<span class="nav-lock">PRO</span>' : '';
    return `
      <a href="${href}" class="${classes}" title="${locked ? item.label + ' – Premium' : item.label}">
        ${item.icon}
        <span class="nav-label">${item.label}${lockBadge}</span>
      </a>
    `;
  }).join('');

  // Admin-only nav item
  const adminNavHtml = user.is_admin ? `
    <div style="border-top:1px solid rgba(255,255,255,.08);margin:8px 0 4px;padding-top:8px">
      <a href="${basePath}admin/index.html" class="nav-item ${activeNav === 'Admin' ? 'active' : ''}"
         title="Admin Dashboard" style="color:#fde68a">
        ${svgShield()}
        <span class="nav-label">Admin</span>
      </a>
    </div>
  ` : '';

  document.getElementById('sidebar').innerHTML = `
    <div class="sidebar-logo">
      <div class="logo-icon">S</div>
      <span class="logo-text">ScholarSphere</span>
      <button class="sidebar-toggle-btn" id="sidebar-toggle-btn"
        onclick="toggleSidebar()" title="Toggle sidebar">
        ${svgChevron()}
      </button>
    </div>
    <nav class="sidebar-nav">${navHtml}${adminNavHtml}</nav>
    <div class="sidebar-footer">
      <button class="nav-item w-full" onclick="handleLogout()"
        style="border:none;cursor:pointer;background:none;text-align:left"
        title="Sign Out">
        ${svgLogout()}
        <span class="nav-label">Sign Out</span>
      </button>
    </div>
  `;

  document.getElementById('page-title').textContent = pageTitle;
  document.getElementById('user-name').textContent = user.full_name;
  document.getElementById('user-avatar').textContent = initials;

  // ── Notification bell ──
  _injectNotifBell(basePath);
  _loadUnreadCount();

  // ── Inject floating expand button (desktop only) ──
  if (!document.getElementById('sidebar-expand-btn')) {
    const expandBtn = document.createElement('button');
    expandBtn.id        = 'sidebar-expand-btn';
    expandBtn.className = 'sidebar-expand-btn';
    expandBtn.title     = 'Expand sidebar';
    expandBtn.setAttribute('onclick', 'toggleSidebar()');
    expandBtn.innerHTML = svgMenu();
    document.body.appendChild(expandBtn);
  }

  // ── Inject hamburger into top-header (mobile) ──
  const headerEl = document.querySelector('.top-header');
  if (headerEl && !document.getElementById('header-menu-btn')) {
    const menuBtn = document.createElement('button');
    menuBtn.id        = 'header-menu-btn';
    menuBtn.className = 'header-menu-btn';
    menuBtn.title     = 'Open menu';
    menuBtn.setAttribute('onclick', 'toggleSidebar()');
    menuBtn.innerHTML = svgMenu();
    headerEl.insertBefore(menuBtn, headerEl.firstChild);
  }

  // ── Inject sidebar overlay (mobile) ──
  if (!document.getElementById('sidebar-overlay')) {
    const overlay = document.createElement('div');
    overlay.id = 'sidebar-overlay';
    overlay.className = 'sidebar-overlay';
    overlay.addEventListener('click', () => {
      document.body.classList.remove('sidebar-open');
    });
    document.body.appendChild(overlay);
  }

  // ── Apply saved sidebar state (desktop only) ──
  if (window.innerWidth > 640 && localStorage.getItem('ossyquiz_nav_collapsed') === '1') {
    document.body.classList.add('nav-collapsed');
  }

  window.handleLogout = function () {
    clearToken();
    window.location.href = basePath + 'index.html';
  };

  window.toggleSidebar = function () {
    if (window.innerWidth <= 640) {
      document.body.classList.toggle('sidebar-open');
    } else {
      const collapsed = document.body.classList.toggle('nav-collapsed');
      localStorage.setItem('ossyquiz_nav_collapsed', collapsed ? '1' : '0');
    }
  };
}

// ── SVG icons ──
function svgHome() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>`;
}

function svgEdit() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
}

function svgAI() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>`;
}

function svgQuiz() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>`;
}

function svgChart() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`;
}

function svgBrainstorm() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-1.66z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-1.66z"/></svg>`;
}

function svgLogout() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`;
}

function svgChevron() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>`;
}

function svgShield() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`;
}

function svgMenu() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>`;
}

function svgBell() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 01-3.46 0"/></svg>`;
}

// ── Notification Bell ────────────────────────────────────────────────────────

function _injectNotifBell(basePath) {
  if (document.getElementById('notif-bell-btn')) return;

  // Inject styles
  const style = document.createElement('style');
  style.textContent = `
    .notif-bell-wrap { position: relative; }
    #notif-bell-btn {
      background: none; border: none; cursor: pointer;
      color: var(--text-muted); padding: 6px; border-radius: 8px;
      display: flex; align-items: center; justify-content: center;
      transition: background 0.15s, color 0.15s;
    }
    #notif-bell-btn:hover { background: rgba(0,119,255,0.08); color: var(--primary); }
    #notif-badge {
      position: absolute; top: 2px; right: 2px;
      background: #EF4444; color: #fff; font-size: 0.6rem; font-weight: 700;
      border-radius: 50%; width: 16px; height: 16px;
      display: flex; align-items: center; justify-content: center;
      pointer-events: none;
    }
    #notif-badge.hidden { display: none; }
    #notif-panel {
      position: absolute; top: calc(100% + 8px); right: 0;
      width: 360px; max-height: 480px;
      background: var(--bg-card); border: 1px solid var(--border);
      border-radius: 14px; box-shadow: 0 8px 32px rgba(0,0,0,0.14);
      z-index: 1000; display: flex; flex-direction: column; overflow: hidden;
    }
    #notif-panel.hidden { display: none; }
    .notif-panel-head {
      padding: 14px 16px 10px; border-bottom: 1px solid var(--border);
      display: flex; align-items: center; justify-content: space-between;
    }
    .notif-panel-head h3 { font-size: 0.9rem; font-weight: 700; color: var(--text); margin: 0; }
    .notif-mark-all {
      font-size: 0.75rem; color: var(--primary); background: none; border: none;
      cursor: pointer; font-weight: 600; padding: 2px 6px; border-radius: 5px;
    }
    .notif-mark-all:hover { background: rgba(0,119,255,0.08); }
    .notif-list { overflow-y: auto; flex: 1; }
    .notif-item {
      padding: 12px 16px; border-bottom: 1px solid var(--border);
      cursor: default; transition: background 0.1s;
    }
    .notif-item:last-child { border-bottom: none; }
    .notif-item.unread { background: rgba(0,119,255,0.04); }
    .notif-item:hover { background: rgba(0,0,0,0.02); }
    .notif-item-title {
      font-size: 0.83rem; font-weight: 600; color: var(--text); margin-bottom: 3px;
      display: flex; align-items: center; gap: 6px;
    }
    .notif-dot { width: 7px; height: 7px; background: var(--primary); border-radius: 50%; flex-shrink: 0; }
    .notif-item-msg { font-size: 0.78rem; color: var(--text-muted); line-height: 1.5; }
    .notif-item-time { font-size: 0.7rem; color: var(--text-muted); margin-top: 4px; }
    .notif-empty { padding: 36px 16px; text-align: center; color: var(--text-muted); font-size: 0.85rem; }
    @media (max-width: 640px) {
      #notif-panel {
        position: fixed;
        top: var(--header-h);
        right: 0; left: 0;
        width: 100%;
        max-width: 100%;
        border-radius: 0 0 14px 14px;
        max-height: 60vh;
      }
    }
  `;
  document.head.appendChild(style);

  // Find the user badge area in the header and inject bell before it
  const header = document.querySelector('.top-header');
  if (!header) return;

  const bellWrap = document.createElement('div');
  bellWrap.className = 'notif-bell-wrap';
  bellWrap.innerHTML = `
    <button id="notif-bell-btn" title="Notifications" onclick="toggleNotifPanel()">
      ${svgBell()}
      <span id="notif-badge" class="hidden">0</span>
    </button>
    <div id="notif-panel" class="hidden">
      <div class="notif-panel-head">
        <h3>Notifications</h3>
        <button class="notif-mark-all" onclick="markAllRead()">Mark all read</button>
      </div>
      <div class="notif-list" id="notif-list">
        <div class="notif-empty">Loading…</div>
      </div>
    </div>
  `;

  // Insert before user-badge
  const userBadge = header.querySelector('.user-badge');
  if (userBadge) {
    userBadge.parentNode.insertBefore(bellWrap, userBadge);
  } else {
    header.appendChild(bellWrap);
  }

  // Close panel on outside click
  document.addEventListener('click', (e) => {
    const panel = document.getElementById('notif-panel');
    const btn   = document.getElementById('notif-bell-btn');
    if (panel && !panel.classList.contains('hidden') && !panel.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
      panel.classList.add('hidden');
    }
  });
}

async function _loadUnreadCount() {
  try {
    const data  = await api.get('/notifications/unread-count');
    const badge = document.getElementById('notif-badge');
    if (!badge) return;
    if (data.unread > 0) {
      badge.textContent = data.unread > 99 ? '99+' : data.unread;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  } catch (_) { /* silent */ }
}

window.toggleNotifPanel = async function () {
  const panel = document.getElementById('notif-panel');
  if (!panel) return;
  const isHidden = panel.classList.contains('hidden');
  panel.classList.toggle('hidden');
  if (isHidden) {
    await _renderNotifList();
  }
};

async function _renderNotifList() {
  const list = document.getElementById('notif-list');
  if (!list) return;
  list.innerHTML = '<div class="notif-empty">Loading…</div>';
  try {
    const notifs = await api.get('/notifications');
    if (!notifs.length) {
      list.innerHTML = '<div class="notif-empty">No notifications yet.</div>';
      return;
    }
    list.innerHTML = notifs.map(n => {
      const date = new Date(n.created_at).toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' });
      const dot  = !n.is_read ? '<span class="notif-dot"></span>' : '';
      return `
        <div class="notif-item ${n.is_read ? '' : 'unread'}" onclick="markOneRead('${n.id}', this)">
          <div class="notif-item-title">${dot}${_escHtml(n.title)}</div>
          <div class="notif-item-msg">${_escHtml(n.message)}</div>
          <div class="notif-item-time">${date}</div>
        </div>
      `;
    }).join('');
  } catch (err) {
    list.innerHTML = `<div class="notif-empty">Failed to load: ${_escHtml(err.message)}</div>`;
  }
}

window.markOneRead = async function (notifId, el) {
  if (!el.classList.contains('unread')) return;
  try {
    await api.patch(`/notifications/${notifId}/read`, {});
    el.classList.remove('unread');
    el.querySelector('.notif-dot')?.remove();
    _loadUnreadCount();
  } catch (_) { /* silent */ }
};

window.markAllRead = async function () {
  try {
    await api.patch('/notifications/read-all', {});
    document.querySelectorAll('.notif-item.unread').forEach(el => {
      el.classList.remove('unread');
      el.querySelector('.notif-dot')?.remove();
    });
    _loadUnreadCount();
  } catch (_) { /* silent */ }
};

function _escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
