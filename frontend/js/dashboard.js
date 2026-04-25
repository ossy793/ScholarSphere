import { api, requireAuth, getUser, setUser } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('unauthenticated');

(async () => {
  try {
    const fresh = await api.get('/auth/me');
    if (fresh) setUser(fresh);
  } catch {}
  initDashboard();
})();

function initDashboard() {
  renderLayout('Dashboard', 'Dashboard');
  const user = getUser();
  document.getElementById('user-name').textContent = user?.full_name || user?.email || '';
  document.getElementById('user-avatar').textContent =
    (user?.full_name || user?.email || '?')[0].toUpperCase();

  loadStreak();
  loadDashboard();
  loadPlanCard();
}

async function loadStreak() {
  try {
    const data = await api.get('/analytics/brainstorm-streak');
    const streak = data.streak || 0;
    const el = document.getElementById('stat-streak');
    const iconEl = document.getElementById('streak-icon');
    if (el) el.textContent = streak > 0 ? `${streak} day${streak !== 1 ? 's' : ''}` : '0 days';
    if (iconEl) iconEl.textContent = streak >= 7 ? '🔥' : streak >= 3 ? '✨' : streak > 0 ? '🌱' : '💤';
  } catch {}
}

async function loadDashboard() {
  try {
    const overview = await api.get('/analytics/overview');
    document.getElementById('stat-courses').textContent  = overview.total_courses;
    document.getElementById('stat-quizzes').textContent  = overview.total_quizzes;
    document.getElementById('stat-attempts').textContent = overview.total_attempts;
    document.getElementById('stat-avg').textContent =
      overview.average_score ? `${overview.average_score}%` : '–';

    if (overview.best_course) {
      document.getElementById('best-course-card').style.display = '';
      document.getElementById('best-course-name').textContent   = overview.best_course;
    }
  } catch (err) {
    console.error('Dashboard load error:', err);
  }
}

async function loadPlanCard() {
  const user = getUser();
  if (!user) return;

  const isAdmin = user.is_admin;
  const container = document.getElementById('plan-card');
  if (!container) return;

  if (isAdmin) {
    container.innerHTML = `
      <div class="plan-status-card plan-pro">
        <div class="plan-status-left">
          <div class="plan-status-icon pro">⚡</div>
          <div class="plan-status-info">
            <h3>Admin — Full Max Access</h3>
            <p>Unlimited access to all features, no restrictions.</p>
          </div>
        </div>
      </div>`;
    return;
  }

  // Fetch live usage from backend (includes dates + per-feature counts)
  let data = {};
  try { data = await api.get('/payment/usage') || {}; } catch {}

  const plan          = data.plan || user.subscription_plan || 'free';
  const usage         = data.usage || {};
  const daysRemaining = data.days_remaining ?? null;
  const daysUsed      = data.days_used ?? null;

  const fmtDate = iso => iso
    ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
    : null;
  const startStr  = fmtDate(data.start);
  const expiryStr = fmtDate(data.expiry);

  const planMeta = {
    free:  { label: 'Free Plan',  icon: '📌', cls: 'free', iconCls: 'free' },
    basic: { label: 'Basic Plan', icon: '✨', cls: 'basic', iconCls: 'basic' },
    pro:   { label: 'Max Plan',   icon: '🚀', cls: 'pro',  iconCls: 'pro'  },
    max:   { label: 'Max Plan',   icon: '🚀', cls: 'pro',  iconCls: 'pro'  },
  };
  const meta = planMeta[plan] || planMeta.free;

  // ── Usage bar helper ──────────────────────────────────────────────────────
  const usageBar = (label, used, limit) => {
    const pct     = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
    const fillCls = pct >= 100 ? 'full' : pct >= 67 ? 'warn' : '';
    return `
      <div class="plan-usage-item">
        <div class="plan-usage-label">${label}</div>
        <div class="plan-usage-bar">
          <div class="plan-usage-fill ${fillCls}" style="width:${pct}%"></div>
        </div>
        <div class="plan-usage-count">${used}/${limit} used</div>
      </div>`;
  };

  // ── Subscription date row ─────────────────────────────────────────────────
  const dateRow = (start, expiry, remaining) => {
    if (!expiry) return '';
    const remLabel = remaining !== null ? `<span style="color:var(--primary);font-weight:600">${remaining}d left</span>` : '';
    return `
      <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:.75rem;color:var(--text-muted);margin-bottom:10px">
        ${start  ? `<span>Started: <strong>${start}</strong></span>` : ''}
        ${expiry ? `<span>Expires: <strong>${expiry}</strong></span>` : ''}
        ${remLabel}
      </div>`;
  };

  let rightSection = '';

  if (plan === 'free') {
    const items = [
      { key: 'ai_generate',     label: 'AI Generate',    limit: usage.ai_generate?.limit     ?? 3 },
      { key: 'study_zone',      label: 'Study Zone',     limit: usage.study_zone?.limit      ?? 3 },
      { key: 'input_questions', label: 'Input Questions', limit: usage.input_questions?.limit ?? 3 },
    ];
    const bars = items.map(({ key, label, limit }) =>
      usageBar(label, usage[key]?.used ?? 0, limit)
    ).join('');

    rightSection = `
      <div class="plan-usage-row">${bars}</div>
      <a href="upgrade.html" class="plan-upgrade-btn">Upgrade ↗</a>`;

  } else if (plan === 'basic') {
    const resourcesUsed = usage.youtube_resources?.used  ?? 0;
    const resourcesLim  = usage.youtube_resources?.limit ?? 10;
    const visualUsed    = usage.visual_explanation?.used  ?? 0;
    const visualLim     = usage.visual_explanation?.limit ?? 10;

    rightSection = `
      ${dateRow(startStr, expiryStr, daysRemaining)}
      <div class="plan-usage-row" style="flex-wrap:wrap;gap:6px">
        ${usageBar('Resources', resourcesUsed, resourcesLim)}
        ${usageBar('Visual Explain', visualUsed, visualLim)}
      </div>
      <a href="upgrade.html" class="plan-upgrade-btn pro-btn">Go Max ↗</a>`;

  } else {
    // max / pro
    rightSection = `
      ${dateRow(startStr, expiryStr, daysRemaining)}
      <p style="font-size:.78rem;color:var(--text-muted);margin:0">
        Full access · All models including Claude · No limits
      </p>`;
  }

  container.innerHTML = `
    <div class="plan-status-card plan-${meta.cls}">
      <div class="plan-status-left">
        <div class="plan-status-icon ${meta.iconCls}">${meta.icon}</div>
        <div class="plan-status-info">
          <h3>${meta.label}</h3>
          <p>${_planDesc(plan)}</p>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;min-width:200px">
        ${rightSection}
      </div>
    </div>`;
}

function _planDesc(plan) {
  if (plan === 'free')  return '3 AI Generates · 3 Study sessions · 3 Input uploads (15 questions max)';
  if (plan === 'basic') return 'Unlimited core · Claude model · 10 Resources & Visual/month';
  if (plan === 'pro' || plan === 'max') return 'Full access · All models (Groq, ChatGPT, Claude) · No limits';
  return '';
}
