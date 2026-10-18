// Who has paid one due, refreshed every 30 seconds; the paid list downloads as PDF or CSV
// (regenerated on every download, so it's always current).
import { api } from './api.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const dueId = new URLSearchParams(location.search).get('id');
const REFRESH_MS = 30_000;
const errorEl = document.getElementById('error');

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function when(iso) {
  return new Date(iso).toLocaleString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium', timeStyle: 'short' });
}

async function load(showMatric) {
  const c = await api.get(`/rep/dues/${dueId}/payments`);
  document.getElementById('s-paid').textContent = `${c.paid_count} of ${c.students}`;
  document.getElementById('s-collected').textContent = formatNaira(c.collected_kobo);
  document.getElementById('s-outstanding').textContent = formatNaira(c.outstanding_kobo);
  document.getElementById('updated').textContent = `Updated ${new Date().toLocaleTimeString('en-NG', { timeStyle: 'short' })} · refreshes every 30 seconds`;

  const paid = document.getElementById('paid');
  paid.replaceChildren(...c.paid.map((s, i) => el('tr', {},
    el('td', { textContent: String(i + 1) }),
    el('td', { textContent: s.name }),
    ...(showMatric ? [el('td', { textContent: s.matric_no ?? '—' })] : []),
    el('td', { className: 'num', textContent: formatNaira(s.amount_kobo) }),
    el('td', { textContent: when(s.paid_at) }),
    el('td', { className: 'mono', textContent: s.receipt_no ?? '' }))));
  if (!c.paid.length) paid.append(el('tr', {}, el('td', { colSpan: 6, className: 'muted', textContent: 'Nobody has paid yet.' })));

  const unpaid = document.getElementById('unpaid');
  unpaid.replaceChildren(...c.unpaid.map((s) => el('tr', {},
    el('td', { textContent: s.name }), el('td', { textContent: s.matric_no ?? '—' }))));
  if (!c.unpaid.length) unpaid.append(el('tr', {}, el('td', { colSpan: 2, className: 'muted', textContent: 'Everyone in your class has paid.' })));
}

async function init() {
  if (!dueId) throw new Error('No due chosen. Go back to your dashboard and pick one.');
  const due = await api.get(`/rep/dues/${dueId}`);
  const terms = due.live_terms ?? due.latest_revision.terms;
  document.getElementById('title').textContent = terms.title;
  document.title = `${terms.title} · Who has paid`;
  const showMatric = terms.require_matric;
  document.getElementById('matric-head').hidden = !showMatric;
  document.getElementById('csv').href = `/api/user/rep/dues/${dueId}/paid-list.csv`;
  document.getElementById('pdf').href = `/api/user/rep/dues/${dueId}/paid-list.pdf`;
  await load(showMatric);
  document.getElementById('main').hidden = false;
  setInterval(() => load(showMatric).catch(() => {}), REFRESH_MS);
}

init().catch((err) => showError(errorEl, err));
