import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { showError } from '/shared/ui.js';

const PAGE_SIZE = 20;
const status = ['pending', 'approved', 'rejected'].includes(new URLSearchParams(location.search).get('status'))
  ? new URLSearchParams(location.search).get('status')
  : 'pending';
const list = document.getElementById('list');
const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
let page = 1;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function row(label, value) {
  return el('div', { className: 'detail-row' },
    el('dt', { textContent: label }), el('dd', { textContent: value ?? '—' }));
}

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

/** Proof files need the staff session cookie, so they open through the API, not a public URL. */
function proofButton(item) {
  const isPdf = item.proof_content_type === 'application/pdf';
  return el('a', {
    className: 'btn btn-inline', href: item.proof_url, target: '_blank', rel: 'noopener',
    textContent: isPdf ? 'Download proof (PDF)' : 'View proof',
  });
}

function decisionControls(item) {
  const wrap = el('div', { className: 'panel-actions' });

  const approve = el('button', { type: 'button', className: 'btn btn-primary btn-inline', textContent: 'Approve' });
  let armed = false;
  approve.addEventListener('click', async () => {
    if (!armed) {
      armed = true;
      approve.textContent = 'Click again to approve';
      setTimeout(() => { armed = false; approve.textContent = 'Approve'; }, 5000);
      return;
    }
    showError(errorEl, null);
    approve.disabled = true;
    approve.textContent = 'Approving…';
    try {
      await api.post(`/rep-applications/${item.id}/approve`);
      notice(`${item.applicant.full_name} is now the verified rep for ${item.class.name}.`);
      await load();
    } catch (err) {
      showError(errorEl, err);
      approve.disabled = false;
      approve.textContent = 'Approve';
    }
  });

  const reason = el('textarea', {
    rows: 2, maxLength: 500, placeholder: 'Reason shown to the applicant, e.g. "The ID photo is too blurry to read."',
  });
  reason.setAttribute('aria-label', 'Reason for rejecting');
  const reject = el('button', { type: 'button', className: 'btn btn-inline', textContent: 'Reject' });
  reject.addEventListener('click', async () => {
    showError(errorEl, null);
    const text = reason.value.trim();
    if (text.length < 5) {
      reason.focus();
      return showError(errorEl, 'Write a short reason (at least 5 characters) so the applicant knows what to fix.');
    }
    reject.disabled = true;
    try {
      await api.post(`/rep-applications/${item.id}/reject`, { reason: text });
      notice(`Application from ${item.applicant.full_name} rejected. They've been emailed the reason.`);
      await load();
    } catch (err) {
      showError(errorEl, err);
      reject.disabled = false;
    }
  });

  wrap.append(approve, el('div', { className: 'reject-box' }, reason, reject));
  return wrap;
}

function card(item) {
  const a = item.applicant;
  const c = item.class;
  const b = item.bank_account;
  const details = el('dl', { className: 'details' },
    row('Email', `${a.email}${a.email_verified ? '' : ' (not verified)'}`),
    row('Phone', a.phone),
    row('Matric number', a.matric_no),
    row('University', c.school),
    row('Department', c.department),
    row('Level / session', `${c.level} level · ${c.session}`),
    row('Payout account', b ? `${b.bank_name} · ${b.account_name} · ••••${b.last4}` : null),
    row('Applied', formatDateTime(item.created_at)),
  );
  if (item.status !== 'pending') {
    details.append(row('Decided', `${formatDateTime(item.reviewed_at)} by ${item.reviewed_by_email ?? '—'}`));
  }
  if (item.reject_reason) details.append(row('Reason', item.reject_reason));

  const nameMismatch = b && !namesOverlap(a.full_name, b.account_name);
  const section = el('section', { className: 'panel' },
    el('div', { className: 'page-head' },
      el('h2', { textContent: `${a.full_name} · ${c.name}` }), proofButton(item)),
    details);
  if (nameMismatch) {
    section.append(el('p', {
      className: 'alert alert-warning',
      textContent: 'The bank account name doesn\'t share a name with the applicant. Check before approving.',
    }));
  }
  if (item.status === 'pending') section.append(decisionControls(item));
  return section;
}

/** A light hint for reviewers: do the applicant's name and account name share any word? */
function namesOverlap(fullName, accountName) {
  const words = (s) => new Set(s.toLowerCase().split(/[^a-z]+/).filter((w) => w.length > 1));
  const account = words(accountName);
  return [...words(fullName)].some((w) => account.has(w));
}

async function load() {
  const data = await api.get(`/rep-applications?status=${status}&page=${page}&page_size=${PAGE_SIZE}`);
  list.replaceChildren(...data.items.map(card));
  if (!data.items.length) {
    list.append(el('p', { className: 'panel muted', textContent: {
      pending: 'No applications waiting. Nice.', approved: 'No approved applications yet.', rejected: 'No rejected applications.',
    }[status] }));
  }
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  document.getElementById('page-info').textContent = `Page ${page} of ${pages} · ${data.total} total`;
  document.getElementById('prev').disabled = page <= 1;
  document.getElementById('next').disabled = page >= pages;
}

document.querySelectorAll('.tabs a').forEach((a) => {
  if (a.dataset.status === status) a.setAttribute('aria-current', 'page');
});
document.getElementById('prev').addEventListener('click', () => { page -= 1; load().catch((e) => showError(errorEl, e)); });
document.getElementById('next').addEventListener('click', () => { page += 1; load().catch((e) => showError(errorEl, e)); });

renderLayout('/rep-applications.html').then(load).catch((err) => showError(errorEl, err));
