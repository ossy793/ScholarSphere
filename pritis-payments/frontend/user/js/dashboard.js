// Student dashboard: live dues of the student's classes, marked Unpaid, Paid or Due closed.
// The class rep view lives on rep.html; choosing it here sends verified reps there and
// everyone else to the rep application.
import { api } from './api.js';
import { formatDeadline } from './due-format.js';
import { formatNaira } from '/shared/money.js';
import { showError, store } from '/shared/ui.js';

const params = new URLSearchParams(location.search);
// "rep" when chosen explicitly (the login page's class rep button) or remembered from last time.
const askedView = params.get('view') ?? (store.get('dpp_role') === 'rep' ? 'rep-remembered' : 'student');

const errorEl = document.getElementById('error');

async function init() {
  const me = await api.get('/me');
  if (!me.profile_complete) {
    const here = location.pathname + location.search;
    location.replace(`/complete-profile.html?next=${encodeURIComponent(here)}`);
    return;
  }
  // Choosing "I'm a class rep" explicitly: reps go to their dashboard, others to the application.
  if (askedView === 'rep') {
    location.replace(me.is_rep ? '/rep.html' : '/rep-apply.html');
    return;
  }
  // Remembered from an earlier visit: only verified reps are taken there.
  if (askedView === 'rep-remembered') {
    if (me.is_rep) {
      location.replace('/rep.html');
      return;
    }
    store.set('dpp_role', 'student');
  }

  // The student/class rep switch is only for people who are both.
  document.getElementById('view-tabs').hidden = !me.is_rep;

  document.getElementById('name').textContent = me.full_name;
  document.getElementById('email').textContent =
    `${me.email} · ${me.university.short_name ?? me.university.name} · ${me.level} level`;

  if (!me.email_verified) {
    const here = location.pathname + location.search;
    document.getElementById('verify-link').href = `/verify-email.html?next=${encodeURIComponent(here)}`;
    document.getElementById('verify-banner').hidden = false;
  }

  document.querySelector('.tabs a[data-view="student"]').setAttribute('aria-current', 'page');

  document.getElementById('classes-text').textContent = me.memberships.length
    ? `Your classes: ${me.memberships.map((m) => m.class_name).join(', ')}.`
    : 'Open the dues link your class rep shared to join your class and see what you owe.';
  const [dues, history] = await Promise.all([api.get('/dues'), api.get('/payments')]);
  renderDues(dues);
  renderHistory(history);
  document.getElementById('student-view').hidden = false;

  document.getElementById('main').hidden = false;
}

const STATUS = {
  unpaid: { label: 'Unpaid', badge: 'badge badge-invited' },
  paid: { label: 'Paid', badge: 'badge badge-active' },
  closed: { label: 'Due closed', badge: 'badge badge-disabled' },
};

function renderHistory(payments) {
  if (!payments.length) return;
  const cell = (content, className) => {
    const td = document.createElement('td');
    if (className) td.className = className;
    td.append(content);
    return td;
  };
  document.getElementById('history').replaceChildren(...payments.map((p) => {
    const tr = document.createElement('tr');
    const paidOn = new Date(p.paid_at).toLocaleDateString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium' });
    if (p.status === 'refunded') {
      const badge = document.createElement('span');
      badge.className = 'badge badge-disabled';
      badge.textContent = 'Refunded';
      tr.append(cell(`${p.due_title} · ${p.class_name}`), cell(formatNaira(p.total_kobo), 'num'), cell(paidOn), cell(badge));
      return tr;
    }
    // Receipts are emailed; this button sends it again.
    const receipt = document.createElement('button');
    receipt.type = 'button';
    receipt.className = 'btn-link';
    receipt.textContent = `${p.receipt_no} · email again`;
    receipt.addEventListener('click', async () => {
      receipt.disabled = true;
      try {
        const res = await api.post(`/payments/${p.id}/email-receipt`);
        receipt.textContent = `${p.receipt_no} · sent to ${res.email}`;
      } catch (err) {
        showError(errorEl, err);
        receipt.disabled = false;
      }
    });
    tr.append(
      cell(`${p.due_title} · ${p.class_name}`),
      cell(formatNaira(p.total_kobo), 'num'),
      cell(paidOn),
      cell(receipt),
    );
    return tr;
  }));
  document.getElementById('history-section').hidden = false;
}

function renderDues(dues) {
  const box = document.getElementById('dues');
  box.replaceChildren(...dues.map((d) => {
    const status = STATUS[d.status];
    const link = document.createElement('a');
    link.className = 'due-card due-link';
    link.href = `/due.html?id=${d.id}`;
    const head = document.createElement('div');
    head.className = 'page-head';
    const title = document.createElement('h3');
    title.textContent = d.title;
    const badge = document.createElement('span');
    badge.className = status.badge;
    badge.textContent = status.label;
    head.append(title, badge);
    const meta = document.createElement('p');
    meta.className = 'muted small';
    meta.textContent = `${d.class_name} · ${formatNaira(d.total_kobo)} · ${d.status === 'closed' ? 'closed' : 'closes'} ${formatDeadline(d.deadline)}`;
    link.append(head, meta);
    return link;
  }));
}

document.getElementById('logout').addEventListener('click', async () => {
  try {
    await api.post('/auth/logout');
  } finally {
    location.href = '/login.html';
  }
});

init().catch((err) => showError(errorEl, err));
