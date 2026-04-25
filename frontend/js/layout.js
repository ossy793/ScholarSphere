import { getUser, clearToken, api, setUser } from './api.js';
import { initAssistant } from './assistant.js';
import { initPushNotifications } from './push.js';

// Minimum plan required per nav item (undefined = free/open)
const NAV_MIN_PLAN = {
  'Create Quiz': 'free',   // free gets 2 uses — access granted, limits enforced by backend
  'AI Generate':     'free',   // same — free gets 3 uses
  'Question Bank':   'free',
  'Performance':     'free',
};

// Plan display config
const PLAN_META = {
  free:  { label: 'Free',  color: '#9ca3af', badge: '' },
  basic: { label: 'Basic', color: '#6366f1', badge: 'BASIC' },
  pro:   { label: 'Pro',   color: '#f59e0b', badge: 'PRO' },
};

function _planOrder(p) { return { free: 0, basic: 1, pro: 2 }[p] || 0; }

export function renderLayout(pageTitle, activeNav, basePath = '') {
  const user = getUser();
  if (!user) return;

  const plan      = user.is_admin ? 'pro' : (user.subscription_plan || 'free');
  const planMeta  = PLAN_META[plan] || PLAN_META.free;

  const initials = (user.full_name || user.email || '?')
    .split(' ')
    .filter(n => n.length > 0)
    .map(n => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2) || '?';

  const navItems = [
    { href: basePath + 'dashboard.html',       icon: svgHome(),       label: 'Dashboard' },
    { href: basePath + 'input-questions.html', icon: svgEdit(),       label: 'Create Quiz' },
    { href: basePath + 'ai-generate.html',     icon: svgAI(),         label: 'AI Generate' },
    { href: basePath + 'question-bank.html',   icon: svgQuiz(),       label: 'Question Bank' },
    { href: basePath + 'brainstorm.html',       icon: svgBrainstorm(),  label: 'Study Zone' },
    { href: basePath + 'study-strategy.html',  icon: svgStrategy(),    label: 'Study Strategy' },
    { href: basePath + 'challenge.html',       icon: svgTrophy(),      label: 'Challenge' },
    { href: basePath + 'performance.html',     icon: svgChart(),       label: 'Performance' },
    { href: basePath + 'settings.html',        icon: svgSettings(),   label: 'Settings' },
  ];

  const navHtml = navItems.map(item => {
    const minPlan = NAV_MIN_PLAN[item.label];
    // Lock nav items only when user has NO plan at all (shouldn't happen since free exists)
    // All items accessible; limits enforced at the feature level with upgrade modals
    const locked  = false;
    const href    = item.href;
    const classes = ['nav-item', activeNav === item.label ? 'active' : '']
      .filter(Boolean).join(' ');
    return `
      <a href="${href}" class="${classes}" title="${item.label}">
        ${item.icon}
        <span class="nav-label">${item.label}</span>
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

  // Plan badge / expiry in sidebar footer
  let planFooterHtml = '';
  if (!user.is_admin) {
    const expiryStr = user.subscription_expiry
      ? `Expires ${new Date(user.subscription_expiry).toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' })}`
      : '';
    const upgradeLink = plan === 'free'
      ? `<a href="${basePath}upgrade.html" style="display:inline-block;margin-top:4px;font-size:.68rem;font-weight:700;
           color:#6366f1;text-decoration:none;background:rgba(99,102,241,.1);padding:2px 8px;
           border-radius:5px;">Upgrade ↗</a>`
      : (plan === 'basic'
          ? `<a href="${basePath}upgrade.html" style="display:inline-block;margin-top:4px;font-size:.68rem;font-weight:700;
               color:#f59e0b;text-decoration:none;background:rgba(245,158,11,.1);padding:2px 8px;
               border-radius:5px;">Go Pro ↗</a>`
          : '');
    planFooterHtml = `
      <div style="padding:8px 12px 4px;border-top:1px solid rgba(255,255,255,.07)">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
          <span style="width:7px;height:7px;border-radius:50%;background:${planMeta.color};flex-shrink:0"></span>
          <span style="font-size:.72rem;font-weight:700;color:${planMeta.color};letter-spacing:.5px;text-transform:uppercase">${planMeta.label} Plan</span>
        </div>
        ${expiryStr ? `<div style="font-size:.68rem;color:rgba(255,255,255,.35);padding-left:13px">${expiryStr}</div>` : ''}
        <div style="padding-left:13px">${upgradeLink}</div>
      </div>
    `;
  } else {
    planFooterHtml = `
      <div style="padding:8px 12px 4px;border-top:1px solid rgba(255,255,255,.07)">
        <span style="font-size:.7rem;font-weight:700;color:#fde68a;letter-spacing:.5px">⚡ ADMIN — FULL ACCESS</span>
      </div>
    `;
  }

  document.getElementById('sidebar').innerHTML = `
    <div class="sidebar-logo">
      <div class="logo-icon">P</div>
      <span class="logo-text">Pritis</span>
      <button class="sidebar-toggle-btn" id="sidebar-toggle-btn"
        onclick="toggleSidebar()" title="Collapse sidebar">
        ${svgChevron()}
      </button>
      <button class="sidebar-close-btn" id="sidebar-close-btn"
        onclick="toggleSidebar()" title="Close menu">
        ${svgX()}
      </button>
    </div>
    <nav class="sidebar-nav">${navHtml}${adminNavHtml}</nav>
    <div class="sidebar-footer">
      ${planFooterHtml}
      <button class="nav-item w-full" onclick="handleLogout()"
        style="border:none;cursor:pointer;background:none;text-align:left"
        title="Sign Out">
        ${svgLogout()}
        <span class="nav-label">Sign Out</span>
      </button>
    </div>
  `;

  document.getElementById('page-title').textContent = pageTitle;
  document.getElementById('user-name').textContent  = user.username || user.full_name;

  // Show profile picture if available, otherwise initials
  const avatarEl = document.getElementById('user-avatar');
  if (user.profile_picture_url) {
    avatarEl.innerHTML = `<img src="${user.profile_picture_url}" alt="avatar"
      style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
  } else {
    avatarEl.innerHTML = '';
    avatarEl.textContent = initials;
  }

  // ── Profile completion enforcement ───────────────────────────────────────────
  // Skip on the settings page itself
  const onSettings = window.location.pathname.includes('settings.html');
  if (!onSettings && !user.profile_completed) {
    _injectProfileIncompleteOverlay(basePath);
  }

  // ── Notification bell ──
  _injectNotifBell(basePath);
  _loadUnreadCount();

  // ── Inject hamburger into top-header (opens sidebar on mobile; re-opens on desktop when collapsed) ──
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

  // ── Inject mobile sign-out button into top-header (beside bell) ──
  if (headerEl && !document.getElementById('mobile-signout-btn')) {
    const signOutBtn = document.createElement('button');
    signOutBtn.id        = 'mobile-signout-btn';
    signOutBtn.className = 'mobile-signout-btn';
    signOutBtn.title     = 'Sign Out';
    signOutBtn.setAttribute('onclick', 'handleLogout()');
    signOutBtn.innerHTML = 'Sign Out';

    // Insert after the bell wrap (sign-out appears right of notification button)
    const bellWrap = headerEl.querySelector('.notif-bell-wrap');
    if (bellWrap) {
      bellWrap.parentNode.insertBefore(signOutBtn, bellWrap.nextSibling);
    } else {
      const userBadge = headerEl.querySelector('.user-badge');
      userBadge ? userBadge.parentNode.insertBefore(signOutBtn, userBadge)
                : headerEl.appendChild(signOutBtn);
    }
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
  if (window.innerWidth > 640 && localStorage.getItem('pritis_nav_collapsed') === '1') {
    document.body.classList.add('nav-collapsed');
  }

  // ── Floating AI Assistant ──
  initAssistant();

  window.handleLogout = function () {
    clearToken();
    window.location.href = basePath + 'index.html';
  };

  window.toggleSidebar = function () {
    if (window.innerWidth <= 640) {
      document.body.classList.toggle('sidebar-open');
    } else {
      const collapsed = document.body.classList.toggle('nav-collapsed');
      localStorage.setItem('pritis_nav_collapsed', collapsed ? '1' : '0');
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

function svgStrategy() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 014 4v14a3 3 0 00-3-3H2z"/><path d="M22 3h-6a4 4 0 00-4 4v14a3 3 0 013-3h7z"/></svg>`;
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

function svgX() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
}

function svgTrophy() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 010-5H6"/><path d="M18 9h1.5a2.5 2.5 0 000-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0012 0V2z"/></svg>`;
}

function svgSettings() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>`;
}

function svgBell() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 01-3.46 0"/></svg>`;
}

function svgHomeOutline() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>`;
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

  // Home button
  const homeBtn = document.createElement('a');
  homeBtn.id        = 'header-home-btn';
  homeBtn.href      = basePath + 'dashboard.html';
  homeBtn.title     = 'Go to Dashboard';
  homeBtn.innerHTML = svgHomeOutline();
  homeBtn.style.cssText = `
    background:none; border:none; cursor:pointer;
    color:var(--text-muted); padding:6px; border-radius:8px;
    display:flex; align-items:center; justify-content:center;
    transition:background 0.15s, color 0.15s; text-decoration:none;
  `;
  homeBtn.addEventListener('mouseenter', () => {
    homeBtn.style.background = 'rgba(0,119,255,0.08)';
    homeBtn.style.color = 'var(--primary)';
  });
  homeBtn.addEventListener('mouseleave', () => {
    homeBtn.style.background = 'none';
    homeBtn.style.color = 'var(--text-muted)';
  });

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

  // Insert home btn + bell before user-badge
  const userBadge = header.querySelector('.user-badge');
  if (userBadge) {
    userBadge.parentNode.insertBefore(homeBtn, userBadge);
    userBadge.parentNode.insertBefore(bellWrap, userBadge);
  } else {
    header.appendChild(homeBtn);
    header.appendChild(bellWrap);
  }

  // Push notifications — try silent init first, then show banner if needed
  setTimeout(() => _initPushOrBanner(), 1500);

  // Close panel on outside click
  document.addEventListener('click', (e) => {
    const panel = document.getElementById('notif-panel');
    const btn   = document.getElementById('notif-bell-btn');
    if (panel && !panel.classList.contains('hidden') && !panel.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
      panel.classList.add('hidden');
    }
  });
}

async function _initPushOrBanner() {
  if (!('Notification' in window)) return;

  if (Notification.permission === 'granted') {
    // Already have permission — silently subscribe/re-sync
    initPushNotifications();
    return;
  }

  if (Notification.permission === 'denied') return;

  // Permission is 'default' — show a banner so user clicks (Chrome requires user gesture)
  const banner = document.createElement('div');
  banner.id = 'push-banner';
  banner.style.cssText = `
    position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
    background:#1e293b;color:#fff;padding:12px 20px;border-radius:12px;
    font-size:.85rem;font-weight:600;display:flex;align-items:center;gap:12px;
    box-shadow:0 8px 30px rgba(0,0,0,.35);z-index:9990;max-width:90vw;
    animation:slideUp .3s ease;
  `;
  banner.innerHTML = `
    <style>@keyframes slideUp{from{opacity:0;transform:translateX(-50%) translateY(20px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}</style>
    <span>🔔</span>
    <span>Enable notifications to get reminders &amp; alerts</span>
    <button id="push-allow-btn" style="background:#3b82f6;border:none;color:#fff;padding:7px 14px;border-radius:8px;font-weight:700;cursor:pointer;font-size:.82rem;white-space:nowrap">Allow</button>
    <button id="push-dismiss-btn" style="background:none;border:none;color:#94a3b8;cursor:pointer;font-size:1.1rem;padding:2px 6px">✕</button>
  `;
  document.body.appendChild(banner);

  document.getElementById('push-allow-btn').addEventListener('click', async () => {
    banner.remove();
    await initPushNotifications();
  });
  document.getElementById('push-dismiss-btn').addEventListener('click', () => {
    banner.remove();
  });

  // Auto-dismiss after 12 seconds
  setTimeout(() => { if (document.getElementById('push-banner')) banner.remove(); }, 12000);
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

// ── Profile completion blocking overlay ───────────────────────────────────────
function _injectProfileIncompleteOverlay(basePath) {
  if (document.getElementById('profile-incomplete-overlay')) return;

  const style = document.createElement('style');
  style.textContent = `
    #profile-incomplete-overlay {
      position: fixed; inset: 0;
      background: rgba(0,0,0,0.65);
      backdrop-filter: blur(4px);
      z-index: 9000;
      display: flex; align-items: center; justify-content: center;
      padding: 20px;
    }
    #profile-incomplete-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 36px 32px;
      max-width: 420px;
      width: 100%;
      text-align: center;
      box-shadow: 0 20px 60px rgba(0,0,0,0.3);
    }
    #profile-incomplete-card .pic-icon { font-size: 3rem; margin-bottom: 16px; }
    #profile-incomplete-card h2 {
      font-size: 1.1rem; font-weight: 800; color: var(--text);
      margin-bottom: 10px;
    }
    #profile-incomplete-card p {
      font-size: 0.88rem; color: var(--text-muted);
      line-height: 1.6; margin-bottom: 24px;
    }
    #profile-incomplete-card .go-btn {
      display: block; width: 100%;
      padding: 13px; border-radius: 10px; border: none;
      background: var(--primary); color: #fff;
      font-size: 0.95rem; font-weight: 700;
      cursor: pointer; text-decoration: none;
      transition: background 0.15s;
    }
    #profile-incomplete-card .go-btn:hover { background: var(--primary-dark); }
  `;
  document.head.appendChild(style);

  const overlay = document.createElement('div');
  overlay.id = 'profile-incomplete-overlay';
  const redirect = encodeURIComponent(window.location.pathname + window.location.search);
  overlay.innerHTML = `
    <div id="profile-incomplete-card">
      <div class="pic-icon">🎓</div>
      <h2>Complete Your Profile</h2>
      <p>Please reset and complete your profile to continue using the platform.<br>
         It only takes a moment!</p>
      <a class="go-btn" href="${basePath}settings.html?redirect=${redirect}&setup=1">
        Complete My Profile →
      </a>
    </div>`;
  document.body.appendChild(overlay);

  // Refresh user from server in background — in case localStorage is stale
  api.get('/users/me').then(u => {
    if (u?.profile_completed) {
      setUser(u);
      overlay.remove();
    }
  }).catch(() => {});
}
