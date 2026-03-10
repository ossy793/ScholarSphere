import { api, requireAuth, getUser, setUser } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('unauthenticated');

// Always refresh user data from server so premium upgrades reflect immediately
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

// ── Free-user view ────────────────────────────────────────────────────────────
// Load streak for all users (Brainstorm is available to everyone)
loadStreak();

if (!user?.is_premium) {
  document.querySelector('.page-body').innerHTML = `
    <div style="max-width:540px;margin:0 auto;text-align:center;padding:40px 0">
      <div style="font-size:3rem;margin-bottom:16px">🔒</div>
      <h2 style="font-size:1.4rem;font-weight:800;color:var(--text);margin:0 0 10px">
        You're on the Free Plan
      </h2>
      <p style="color:var(--text-muted);margin:0 0 28px;font-size:.95rem">
        Free users have access to the <strong>Brainstorm</strong> feature.
        Upgrade with a promo code to unlock AI question generation, quizzes,
        performance analytics and more — completely free.
      </p>
      <a href="upgrade.html" class="btn btn-primary btn-lg"
         style="margin-bottom:12px;display:inline-block;width:100%;max-width:280px">
        Get Premium Access
      </a>
      <br>
      <a href="brainstorm.html" class="btn btn-outline btn-lg"
         style="display:inline-block;width:100%;max-width:280px">
        Go to Brainstorm
      </a>

      <div style="margin-top:20px;display:inline-flex;align-items:center;gap:10px;
                  background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);
                  padding:10px 18px">
        <span id="streak-icon" style="font-size:1.3rem">🔥</span>
        <div style="text-align:left">
          <div style="font-weight:800;font-size:1rem;color:var(--text)" id="stat-streak">– days</div>
          <div style="font-size:0.75rem;color:var(--text-muted)">Brainstorm Streak</div>
        </div>
      </div>

      <div style="margin-top:28px;display:grid;grid-template-columns:1fr 1fr;gap:14px;text-align:left">
        ${[
          ['🤖', 'AI Generate',    'Generate quizzes from your notes, PDFs or Word documents.'],
          ['✏️', 'Input Questions', 'Create custom quizzes with MCQs, short answers and more.'],
          ['📋', 'My Questions',   'Manage, share and practice all your saved quizzes.'],
          ['📊', 'Performance',    'Track your scores and progress with detailed analytics.'],
        ].map(([icon, title, desc]) => `
          <div style="background:var(--bg);border-radius:var(--radius);padding:16px;
                      border:1px solid var(--border)">
            <div style="font-size:1.4rem;margin-bottom:6px">${icon}</div>
            <p style="font-weight:700;margin:0 0 4px;font-size:.9rem">
              ${title}
              <span style="font-size:.65rem;background:#6366f1;color:#fff;
                           padding:1px 6px;border-radius:999px;vertical-align:middle">PRO</span>
            </p>
            <p style="font-size:.8rem;color:var(--text-muted);margin:0">${desc}</p>
          </div>
        `).join('')}
      </div>
    </div>
  `;
} else {
  // ── Premium-user view ─────────────────────────────────────────────────────
  loadDashboard();
}

} // end initDashboard

async function loadStreak() {
  try {
    const data = await api.get('/analytics/brainstorm-streak');
    const streak = data.streak || 0;
    const el = document.getElementById('stat-streak');
    const iconEl = document.getElementById('streak-icon');
    if (el) el.textContent = streak > 0 ? `${streak} day${streak !== 1 ? 's' : ''}` : '0 days';
    if (iconEl) iconEl.textContent = streak >= 7 ? '🔥' : streak >= 3 ? '✨' : streak > 0 ? '🌱' : '💤';
  } catch {
    // streak is non-critical; fail silently
  }
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
