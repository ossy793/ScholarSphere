// The rep dashboard: dues (create, submit, go live, pause, share), the class and the payout account.
import { api } from './api.js';
import { PHASES, formatDeadline } from './due-format.js';
import { setupMoney } from './rep-money.js';
import { drawChart } from '/shared/charts.js';
import { formatNaira } from '/shared/money.js';
import { showError, store } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const actionErrorEl = document.getElementById('action-error');
const noticeEl = document.getElementById('notice');

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function td(text) {
  return el('td', { textContent: text });
}

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

function actionButton(label, onClick, primary = false) {
  const button = el('button', { type: 'button', className: `btn btn-inline${primary ? ' btn-primary' : ''}`, textContent: label });
  button.addEventListener('click', async () => {
    showError(actionErrorEl, null);
    notice('');
    button.disabled = true;
    try {
      await onClick();
      await loadDues();
    } catch (err) {
      showError(actionErrorEl, err);
      button.disabled = false;
    }
  });
  return button;
}

function dueCard(due) {
  // What students see if approved; otherwise the draft being worked on.
  const shown = due.live_terms ?? due.open_revision?.terms ?? due.latest_revision.terms;
  const phase = PHASES[due.phase];
  const head = el('div', { className: 'page-head' },
    el('h3', { textContent: shown.title }),
    el('span', { className: phase.badge, textContent: due.closed ? `${phase.label} · Due closed` : phase.label }));

  const meta = el('p', { className: 'muted small',
    textContent: `${formatNaira(shown.amount_kobo)} · closes ${formatDeadline(shown.deadline)}${shown.require_matric ? ' · matric number required' : ''}` });

  const card = el('div', { className: 'due-card' }, head, meta);
  if (due.live_terms) {
    card.append(el('p', { className: 'small', textContent:
      `${due.paid_count} of ${due.students} paid · collected ${formatNaira(due.collected_kobo)} · outstanding ${formatNaira(due.outstanding_kobo)}` }));
  }

  // A waiting change: show what's proposed next to what students see now.
  if (due.phase === 'edit_pending') {
    const p = due.open_revision.terms;
    card.append(el('p', { className: 'alert alert-warning small',
      textContent: `Change waiting for approval: ${p.title}, ${formatNaira(p.amount_kobo)}, closes ${formatDeadline(p.deadline)}. Students see the current terms until it's approved.` }));
  }
  const last = due.latest_revision;
  if (last.status === 'rejected') {
    card.append(el('p', { className: 'alert alert-error small',
      textContent: `${due.live_terms ? 'Your last change' : 'This due'} wasn't approved: ${last.reject_reason}` }));
  }

  const actions = el('div', { className: 'actions' });
  if (due.live_terms) {
    actions.append(el('a', { className: 'btn btn-inline', href: `/rep-due-payments.html?id=${due.id}`, textContent: 'Who has paid' }));
  }
  if (due.phase === 'draft') {
    actions.append(actionButton('Submit for approval', async () => {
      await api.post(`/rep/dues/${due.id}/submit`);
      notice('Submitted. We\'ll email you when it\'s reviewed.');
    }, true));
  }
  if (due.phase === 'approved') {
    actions.append(actionButton('Go live', async () => {
      await api.post(`/rep/dues/${due.id}/live`);
      notice('Live. Share the link with your class.');
    }, true));
  }
  if (due.phase === 'live' || due.phase === 'edit_pending') {
    actions.append(actionButton('Pause', () => api.post(`/rep/dues/${due.id}/pause`)));
  }
  if (!['pending', 'edit_pending'].includes(due.phase)) {
    actions.append(el('a', {
      className: 'btn btn-inline', href: `/rep-due.html?id=${due.id}`,
      textContent: due.closed ? 'Extend deadline' : 'Edit',
    }));
  }
  if (due.live_terms) {
    const copy = el('button', { type: 'button', className: 'btn btn-inline', textContent: 'Copy link' });
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(due.share_url);
        copy.textContent = 'Link copied';
      } catch {
        window.prompt('Copy this link:', due.share_url);
      }
    });
    actions.append(copy);
  }
  card.append(actions);
  if (due.live_terms) {
    card.append(el('p', { className: 'muted small mono', textContent: due.share_url }));
  }
  return card;
}

async function loadDues() {
  const list = await api.get('/rep/dues');
  const box = document.getElementById('dues');
  box.replaceChildren(...list.map(dueCard));
  if (!list.length) box.append(el('p', { className: 'muted', textContent: 'No dues yet. Create your first one.' }));

  // Collected and outstanding per approved due (design section 10: horizontal stacked bar).
  const approved = list.filter((d) => d.live_terms);
  document.getElementById('collect-chart-box').hidden = approved.length === 0;
  if (approved.length) {
    const canvas = document.getElementById('c-collect');
    canvas.closest('.chart-canvas').style.height = `${Math.max(140, approved.length * 44 + 70)}px`;
    drawChart(canvas, {
      type: 'bar', horizontal: true, stacked: true, money: true, valueLabel: 'Due',
      labels: approved.map((d) => d.live_terms.title),
      series: [
        { label: 'Collected', values: approved.map((d) => d.collected_kobo) },
        { label: 'Outstanding', values: approved.map((d) => d.outstanding_kobo) },
      ],
    });
  }
}

async function loadMembers(classId) {
  const members = await api.get(`/rep/classes/${classId}/members`);
  const rows = document.getElementById('members');
  if (!members.length) {
    const cell = td('No students yet.');
    cell.colSpan = 3;
    cell.className = 'muted';
    rows.replaceChildren(el('tr', {}, cell));
    return;
  }
  rows.replaceChildren(...members.map((m) => el('tr', {},
    td(m.full_name), td(m.matric_no ?? '—'),
    td(new Date(m.joined_at).toLocaleDateString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium' })))));
}

async function init() {
  const me = await api.get('/me');
  if (!me.is_rep) {
    location.replace('/rep-apply.html');
    return;
  }
  store.set('dpp_role', 'rep');
  const rep = await api.get('/rep/class');
  const c = rep.class;
  document.getElementById('class-name').textContent = c.name;
  document.getElementById('class-meta').textContent =
    `${c.school} · ${c.department} · ${c.level} level · ${c.session}`;
  document.getElementById('member-count').textContent = String(rep.member_count);
  const b = rep.bank_account;
  document.getElementById('bank').textContent = b
    ? `${b.bank_name} · ${b.account_name} · ••••${b.last4}`
    : 'No payout account yet.';

  await Promise.all([setupMoney(actionErrorEl, notice), loadDues(), loadMembers(c.id)]);
  const saved = new URLSearchParams(location.search).get('saved');
  if (saved) notice(saved === 'draft' ? 'Draft saved.' : 'Sent for approval. We\'ll email you when it\'s reviewed.');
  document.getElementById('main').hidden = false;
}

document.getElementById('logout').addEventListener('click', async () => {
  try { await api.post('/auth/logout'); } finally { location.href = '/login.html'; }
});

init().catch((err) => showError(errorEl, err));
