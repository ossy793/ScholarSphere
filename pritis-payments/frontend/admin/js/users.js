// Search users and see one person's classes, rep status and payments. Read-only.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const form = document.getElementById('search');
const REP = { none: 'Not a rep', pending: 'Rep application waiting', approved: 'Verified rep', rejected: 'Rep application rejected' };

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function row(label, value) {
  return el('div', { className: 'detail-row' }, el('dt', { textContent: label }), el('dd', { textContent: value ?? '—' }));
}

async function showUser(id) {
  const u = await api.get(`/users/${id}`);
  document.getElementById('d-name').textContent = u.full_name;
  document.getElementById('d-info').replaceChildren(
    row('Email', `${u.email}${u.email_verified ? '' : ' (not verified)'}`),
    row('Phone', u.phone), row('Matric number', u.matric_no),
    row('University', u.university ? `${u.university}${u.level ? ` · ${u.level} level` : ''}` : null),
    row('Classes', u.classes.join(', ') || null),
    row('Rep', `${REP[u.rep_status]}${u.rep_class ? ` · ${u.rep_class}` : ''}`),
    row('Account', `${u.status} · joined ${formatDateTime(u.created_at)}`),
  );
  document.getElementById('d-payments').replaceChildren(...u.payments.map((p) => el('tr', {},
    el('td', { textContent: formatDateTime(p.paid_at ?? p.created_at) }),
    el('td', { textContent: `${p.due_title} · ${p.class_name}` }),
    el('td', { className: 'num', textContent: formatNaira(p.total_kobo) }),
    el('td', { textContent: p.status + (p.failure_reason ? ` (${p.failure_reason})` : '') }),
    el('td', { className: 'mono', textContent: p.reference }),
    el('td', { className: 'mono', textContent: p.receipt_no ?? '' }))));
  if (!u.payments.length) {
    document.getElementById('d-payments').append(el('tr', {}, el('td', { colSpan: 6, className: 'muted', textContent: 'No payments.' })));
  }
  document.getElementById('detail').hidden = false;
  document.getElementById('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// The list: everyone (newest first) or those matching the search, 50 at a time.
const PAGE = 50;
let offset = 0;
let query = '';

function userRow(u) {
  const name = el('button', { type: 'button', className: 'btn-link', textContent: u.full_name });
  name.addEventListener('click', () => showUser(u.id).catch((err) => showError(errorEl, err)));
  const who = el('td', {}, name);
  if (u.is_rep) who.append(' ', el('span', { className: 'badge badge-active', textContent: 'Rep' }));
  if (!u.email_verified) who.append(' ', el('span', { className: 'badge badge-invited', textContent: 'Email not verified' }));
  if (u.status !== 'active') who.append(' ', el('span', { className: 'badge badge-disabled', textContent: u.status }));
  return el('tr', {},
    who, el('td', { textContent: u.email }), el('td', { textContent: u.phone ?? '—' }),
    el('td', { textContent: u.matric_no ?? '—' }),
    el('td', { textContent: u.university ? `${u.university}${u.level ? ` · ${u.level}` : ''}` : '—' }),
    el('td', { textContent: new Date(u.created_at).toLocaleDateString('en-NG', { dateStyle: 'medium' }) }));
}

async function load(append = false) {
  showError(errorEl, null);
  const params = new URLSearchParams({ kind: form.elements.kind.value, offset: String(offset), limit: String(PAGE) });
  if (query) params.set('q', query);
  const users = await api.get(`/users?${params}`);
  const body = document.getElementById('results');
  if (!append) body.replaceChildren();
  body.append(...users.map(userRow));
  offset += users.length;
  if (!body.children.length) {
    body.append(el('tr', {}, el('td', { colSpan: 6, className: 'muted', textContent: query ? 'Nobody matches.' : 'No users yet.' })));
  }
  document.getElementById('more').hidden = users.length < PAGE;
  const shown = body.querySelectorAll('button.btn-link').length;
  document.getElementById('summary').textContent = query
    ? `${shown} matching "${query}"${users.length === PAGE ? ' so far' : ''}.`
    : `Showing ${shown}${users.length === PAGE ? '' : ' (all)'}, newest first.`;
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const q = form.elements.q.value.trim();
  if (q.length === 1) return showError(errorEl, 'Type at least 2 characters, or clear the box to see everyone.');
  query = q;
  offset = 0;
  document.getElementById('detail').hidden = true;
  load().catch((err) => showError(errorEl, err));
});
form.elements.kind.addEventListener('change', () => form.requestSubmit());
document.getElementById('more').addEventListener('click', () => load(true).catch((err) => showError(errorEl, err)));

renderLayout('/users.html').then(() => load()).catch((err) => showError(errorEl, err));
