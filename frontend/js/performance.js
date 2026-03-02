import { api, requirePremium } from './api.js';
import { renderLayout } from './layout.js';

if (!requirePremium()) throw new Error('not premium');
renderLayout('Performance Analysis', 'Performance');

let trendChart  = null;
let courseChart = null;
let allRecommendations = [];

async function init() {
  await Promise.all([
    loadOverview(),
    loadTrend(),
    loadCourseSummary(),
    loadCourseFilter(),
  ]);
  loadAttempts();
}

async function loadOverview() {
  try {
    const data = await api.get('/analytics/overview');
    document.getElementById('s-attempts').textContent = data.total_attempts;
    document.getElementById('s-avg').textContent = data.average_score ? `${data.average_score}%` : '–';
    document.getElementById('s-courses').textContent = data.total_courses;
    document.getElementById('s-best').textContent = data.best_course || '–';
  } catch {}
}

async function loadTrend() {
  try {
    const trend = await api.get('/analytics/trend');
    const canvas = document.getElementById('trend-chart');
    const empty  = document.getElementById('trend-empty');

    if (trend.length === 0) {
      canvas.classList.add('hidden');
      empty.classList.remove('hidden');
      return;
    }

    if (trendChart) trendChart.destroy();
    trendChart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: trend.map(t => t.date),
        datasets: [{
          label: 'Score (%)',
          data: trend.map(t => t.score),
          borderColor: '#4f46e5',
          backgroundColor: 'rgba(79,70,229,0.1)',
          fill: true,
          tension: 0.4,
          pointBackgroundColor: '#4f46e5',
          pointRadius: 4,
        }],
      },
      options: {
        responsive: true,
        scales: {
          y: { min: 0, max: 100, ticks: { callback: v => `${v}%` } },
        },
        plugins: { legend: { display: false } },
      },
    });
  } catch (err) {
    console.error(err);
  }
}

async function loadCourseSummary() {
  try {
    const data = await api.get('/analytics/courses-summary');
    const canvas = document.getElementById('course-chart');
    const empty  = document.getElementById('course-empty');
    const withData = data.filter(d => d.average_score !== null);

    if (withData.length === 0) {
      canvas.classList.add('hidden');
      empty.classList.remove('hidden');
      return;
    }

    if (courseChart) courseChart.destroy();
    courseChart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: withData.map(d => d.course_name),
        datasets: [{
          label: 'Avg Score (%)',
          data: withData.map(d => d.average_score),
          backgroundColor: [
            '#4f46e5', '#06b6d4', '#10b981', '#f59e0b',
            '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6',
          ],
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        scales: {
          y: { min: 0, max: 100, ticks: { callback: v => `${v}%` } },
        },
        plugins: { legend: { display: false } },
      },
    });
  } catch (err) {
    console.error(err);
  }
}

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

window.loadAttempts = async function() {
  try {
    const attempts = await api.get('/attempts');
    const tbody  = document.getElementById('attempt-table');
    const empty  = document.getElementById('attempt-empty');

    if (attempts.length === 0) {
      tbody.innerHTML = '';
      empty.classList.remove('hidden');
      return;
    }

    empty.classList.add('hidden');

    // Load all unique quiz titles in parallel (not sequentially)
    const uniqueIds = [...new Set(attempts.map(a => a.quiz_id))];
    const quizResults = await Promise.all(
      uniqueIds.map(id => api.get(`/quizzes/${id}`).catch(() => ({ id, title: 'Unknown Quiz' })))
    );
    const quizCache = Object.fromEntries(quizResults.map(q => [q.id, q]));

    tbody.innerHTML = attempts.map(a => {
      const quiz   = quizCache[a.quiz_id] || {};
      const date   = a.completed_at ? new Date(a.completed_at).toLocaleDateString() : 'In progress';
      const score  = a.score !== null ? `${a.score}%` : '–';
      const m = a.time_taken_seconds ? Math.floor(a.time_taken_seconds / 60) : 0;
      const s = a.time_taken_seconds ? a.time_taken_seconds % 60 : 0;
      const time = a.time_taken_seconds ? (m > 0 ? `${m}m ${s}s` : `${s}s`) : '–';
      const scoreColor = a.score >= 80 ? 'var(--success)' : a.score >= 60 ? 'var(--warning)' : 'var(--danger)';
      return `
        <tr>
          <td>${escHtml(quiz.title || 'Unknown')}</td>
          <td><span class="badge ${a.mode === 'exam' ? 'badge-primary' : 'badge-muted'}">${a.mode}</span></td>
          <td><strong style="color:${scoreColor}">${score}</strong></td>
          <td>${a.correct_answers} / ${a.total_questions}</td>
          <td>${time}</td>
          <td>${date}</td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error(err);
  }
};

// ── AI Recommendations ────────────────────────────────────────────────────────

window.loadRecommendations = async function() {
  const btn         = document.getElementById('rec-btn');
  const spinner     = document.getElementById('rec-spinner');
  const placeholder = document.getElementById('rec-placeholder');
  const empty       = document.getElementById('rec-empty');
  const errEl       = document.getElementById('rec-error');
  const tableWrap   = document.getElementById('rec-table-wrap');

  btn.disabled = true;
  btn.textContent = 'Generating…';
  spinner.classList.remove('hidden');
  placeholder.classList.add('hidden');
  empty.classList.add('hidden');
  errEl.classList.add('hidden');
  tableWrap.classList.add('hidden');

  try {
    allRecommendations = await api.get('/analytics/recommendations');

    if (allRecommendations.length === 0) {
      empty.classList.remove('hidden');
      return;
    }

    _populateRecCourseFilter();
    _renderRecommendations(allRecommendations);
    tableWrap.classList.remove('hidden');

  } catch (err) {
    errEl.textContent = err.message || 'Failed to generate recommendations. Please try again.';
    errEl.classList.remove('hidden');
    placeholder.classList.remove('hidden');
  } finally {
    spinner.classList.add('hidden');
    btn.disabled = false;
    btn.textContent = '🤖 Refresh Recommendations';
  }
};

function _populateRecCourseFilter() {
  const select  = document.getElementById('rec-filter-course');
  const courses = [...new Set(allRecommendations.map(r => r.course_name).filter(Boolean))];
  select.innerHTML = '<option value="">All Courses</option>';
  courses.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    select.appendChild(opt);
  });
}

window.filterRecommendations = function() {
  if (!allRecommendations.length) return;
  const course   = document.getElementById('rec-filter-course').value;
  const filtered = course
    ? allRecommendations.filter(r => r.course_name === course)
    : allRecommendations;
  _renderRecommendations(filtered);
  document.getElementById('rec-table-wrap').classList.toggle('hidden', filtered.length === 0);
  document.getElementById('rec-empty').classList.toggle('hidden', filtered.length > 0);
  if (filtered.length === 0) {
    document.getElementById('rec-empty').textContent = 'No recommendations for the selected course.';
  }
};

function _renderRecommendations(recs) {
  const tbody = document.getElementById('rec-tbody');

  const PRIORITY = {
    'High Priority':   { bg: 'var(--danger)',  fg: '#fff' },
    'Medium Priority': { bg: 'var(--warning)', fg: '#fff' },
    'Low Priority':    { bg: 'var(--success)', fg: '#fff' },
  };

  tbody.innerHTML = recs.map(r => {
    const p          = PRIORITY[r.priority] || { bg: 'var(--muted)', fg: '#fff' };
    const score      = r.performance_score ?? null;
    const scoreColor = score === null ? '' : score < 50 ? 'var(--danger)' : score < 70 ? 'var(--warning)' : 'var(--success)';
    const weakList   = (r.weak_concepts         || []).map(c => `<li>${escHtml(c)}</li>`).join('');
    const recList    = (r.recommended_concepts  || []).map(c => `<li>${escHtml(c)}</li>`).join('');

    return `
      <tr>
        <td>${escHtml(r.course_name  || '')}</td>
        <td><strong>${escHtml(r.topic_name || '')}</strong></td>
        <td><strong style="color:${scoreColor}">${score !== null ? score + '%' : '–'}</strong></td>
        <td>
          <span class="badge" style="background:${p.bg};color:${p.fg};white-space:nowrap">
            ${escHtml(r.priority || '')}
          </span>
        </td>
        <td><ul class="rec-list">${weakList}</ul></td>
        <td><ul class="rec-list">${recList}</ul></td>
        <td class="text-sm">${escHtml(r.study_focus || '')}</td>
        <td><strong style="white-space:nowrap">${escHtml(r.study_time || '')}</strong></td>
      </tr>`;
  }).join('');
}

function escHtml(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

init();
