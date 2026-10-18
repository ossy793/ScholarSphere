// Approval queue for new dues and changes to live dues. Super admin and accountant.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const PAGE_SIZE = 20;
const qs = new URLSearchParams(location.search).get('status');
const status = ['pending', 'approved', 'rejected'].includes(qs) ? qs : 'pending';
const list = document.getElementById('list');
const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
let page = 1;
let me = null;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

function deadline(iso) {
  return new Date(iso).toLocaleString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium', timeStyle: 'short' });
}

/** One column of terms; fields that differ from `other` are highlighted. */
function termsBlock(heading, t, other) {
  const field = (label, value, otherValue) => {
    const dd = el('dd', { textContent: value });
    if (other && value !== otherValue) dd.className = 'changed';
    return [el('dt', { textContent: label }), dd];
  };
  const o = other ?? {};
  return el('div', {},
    el('h4', { textContent: heading }),
    el('dl', { className: 'details' },
      ...field('Title', t.title, o.title),
      ...field('Amount', formatNaira(t.amount_kobo), o.amount_kobo != null ? formatNaira(o.amount_kobo) : undefined),
      ...field('Deadline', deadline(t.deadline), o.deadline ? deadline(o.deadline) : undefined),
      ...field('Matric number', t.require_matric ? 'Required' : 'Not asked', o.require_matric == null ? undefined : (o.require_matric ? 'Required' : 'Not asked')),
      ...field('Description', t.description || '—', o.description == null ? undefined : (o.description || '—')),
    ));
}

function decision(item) {
  const blocked = item.needs_super_admin && me.role !== 'super_admin';
  const wrap = el('div', { className: 'panel-actions' });

  const fee = el('input', { type: 'text', inputMode: 'decimal', value: String(Number(item.suggested_fee_percent)) });
  fee.setAttribute('aria-label', 'Platform fee percent');
  const feeLabel = el('label', { className: 'small' }, 'Platform fee for this due',
    el('span', { className: 'input-suffix' }, fee, el('span', { textContent: '%' })));

  const approve = el('button', { type: 'button', className: 'btn btn-primary btn-inline', textContent: 'Approve', disabled: blocked });
  let armed = false;
  approve.addEventListener('click', async () => {
    const value = fee.value.trim();
    if (!/^\d{1,2}(\.\d{1,2})?$/.test(value) || Number(value) > 20) {
      return showError(errorEl, 'The platform fee must be between 0 and 20%, with at most 2 decimals.');
    }
    if (!armed) {
      armed = true;
      approve.textContent = `Click again to approve at ${value}%`;
      setTimeout(() => { armed = false; approve.textContent = 'Approve'; }, 5000);
      return;
    }
    showError(errorEl, null);
    approve.disabled = true;
    try {
      await api.post(`/dues/${item.due_id}/approve`, { platform_fee_percent: value });
      notice(`Approved "${item.proposed.title}" for ${item.class_name}. The rep has been emailed.`);
      await load();
    } catch (err) {
      showError(errorEl, err);
      approve.disabled = false;
      approve.textContent = 'Approve';
    }
  });

  const reason = el('textarea', { rows: 2, maxLength: 500, placeholder: 'Reason shown to the rep' });
  reason.setAttribute('aria-label', 'Reason for rejecting');
  const reject = el('button', { type: 'button', className: 'btn btn-inline', textContent: 'Reject' });
  reject.addEventListener('click', async () => {
    showError(errorEl, null);
    const text = reason.value.trim();
    if (text.length < 5) {
      reason.focus();
      return showError(errorEl, 'Write a short reason (at least 5 characters) so the rep knows what to change.');
    }
    reject.disabled = true;
    try {
      await api.post(`/dues/${item.due_id}/reject`, { reason: text });
      notice(`Rejected "${item.proposed.title}". The rep has been emailed the reason.`);
      await load();
    } catch (err) {
      showError(errorEl, err);
      reject.disabled = false;
    }
  });

  wrap.append(el('div', { className: 'form-grid' }, feeLabel, approve), el('div', { className: 'reject-box' }, reason, reject));
  return wrap;
}

function card(item) {
  const kind = item.kind === 'edit' ? 'Change to a live due' : 'New due';
  const section = el('section', { className: 'panel' },
    el('div', { className: 'page-head' },
      el('h2', { textContent: `${item.proposed.title}` }),
      el('span', { className: 'badge', textContent: kind })),
    el('p', { className: 'muted small', textContent: `${item.class_name} · ${item.school} · rep ${item.rep_name} (${item.rep_email})` }),
  );

  if (item.current) {
    section.append(el('div', { className: 'compare' },
      termsBlock('Live now', item.current), termsBlock('Proposed', item.proposed, item.current)));
    section.append(el('p', { className: 'muted small', textContent: `Students keep paying the live terms until you approve. Current platform fee: ${Number(item.current_fee_percent)}%.` }));
  } else {
    section.append(termsBlock('Terms', item.proposed));
  }

  if (item.needs_super_admin) {
    section.append(el('p', { className: 'alert alert-warning small', textContent:
      `This due is above the ${formatNaira(maxKobo)} maximum, so only the super admin can approve it.` }));
  }
  if (item.status === 'pending') {
    section.append(el('p', { className: 'muted small', textContent: `Submitted ${formatDateTime(item.submitted_at)}` }));
    section.append(decision(item));
  } else {
    const who = `${formatDateTime(item.reviewed_at)} by ${item.reviewed_by_email ?? '—'}`;
    section.append(el('p', { className: 'muted small', textContent: item.status === 'approved'
      ? `Approved ${who} at ${Number(item.approved_fee_percent)}% platform fee.`
      : `Rejected ${who}: ${item.reject_reason}` }));
  }
  return section;
}

let maxKobo = 0;

async function load() {
  const data = await api.get(`/dues?status=${status}&page=${page}&page_size=${PAGE_SIZE}`);
  maxKobo = data.max_due_amount_kobo;
  document.getElementById('intro').textContent =
    `Check the amount and deadline make sense for the class. Students pay the due plus a service charge (Paystack part plus your platform fee). Dues above ${formatNaira(maxKobo)} need the super admin.`;
  list.replaceChildren(...data.items.map(card));
  if (!data.items.length) {
    list.append(el('p', { className: 'panel muted', textContent: { pending: 'Nothing waiting for approval.', approved: 'Nothing approved yet.', rejected: 'Nothing rejected.' }[status] }));
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

renderLayout('/dues.html')
  .then((who) => { me = who; return load(); })
  .catch((err) => showError(errorEl, err));
