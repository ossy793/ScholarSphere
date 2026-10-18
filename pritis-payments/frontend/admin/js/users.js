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

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  showError(errorEl, null);
  const q = form.elements.q.value.trim();
  if (q.length < 2) return showError(errorEl, 'Type at least 2 characters.');
  try {
    const users = await api.get(`/users?q=${encodeURIComponent(q)}`);
    document.getElementById('results').replaceChildren(...users.map((u) => {
      const name = el('button', { type: 'button', className: 'btn-link', textContent: u.full_name });
      name.addEventListener('click', () => showUser(u.id).catch((err) => showError(errorEl, err)));
      return el('tr', {},
        el('td', {}, name), el('td', { textContent: u.email }), el('td', { textContent: u.phone ?? '—' }),
        el('td', { textContent: u.matric_no ?? '—' }),
        el('td', { textContent: u.university ? `${u.university}${u.level ? ` · ${u.level}` : ''}` : '—' }),
        el('td', { textContent: new Date(u.created_at).toLocaleDateString('en-NG', { dateStyle: 'medium' }) }));
    }));
    if (!users.length) {
      document.getElementById('results').append(el('tr', {}, el('td', { colSpan: 6, className: 'muted', textContent: 'Nobody matches.' })));
    }
    document.getElementById('results-wrap').hidden = false;
    document.getElementById('detail').hidden = true;
  } catch (err) {
    showError(errorEl, err);
  }
});

renderLayout('/users.html').catch((err) => showError(errorEl, err));
