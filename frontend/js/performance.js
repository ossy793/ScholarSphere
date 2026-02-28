import { api, requirePremium } from './api.js';
import { renderLayout } from './layout.js';

if (!requirePremium()) throw new Error('not premium');
renderLayout('Performance Analysis', 'Performance');

let trendChart  = null;
let courseChart = null;

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

    // Load quiz titles for display (cache by id)
    const quizCache = {};
    for (const a of attempts) {
      if (!quizCache[a.quiz_id]) {
        try {
          quizCache[a.quiz_id] = await api.get(`/quizzes/${a.quiz_id}`);
        } catch {
          quizCache[a.quiz_id] = { title: 'Unknown Quiz' };
        }
      }
    }

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

function escHtml(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

init();
