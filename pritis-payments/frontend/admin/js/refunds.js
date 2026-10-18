// Refund approvals. Everyone with access sees the list; only the accountant and super
// admin get the approve and reject controls (the server enforces it either way).
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const TABS = ['requested', 'processing', 'processed', 'rejected', 'failed'];
const qs = new URLSearchParams(location.search).get('tab');
const tab = TABS.includes(qs) ? qs : 'requested';
const list = document.getElementById('list');
const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
let balance = null;
let canDecide = false;

const STATUS_LABEL = {
  requested: 'Waiting', processing: 'Being sent', processed: 'Refunded', rejected: 'Rejected', failed: 'Failed',
};
const MISTAKE = {
  pritis: { label: "Pritis's mistake", hint: 'Student gets everything back; Pritis absorbs Paystack\'s fee.' },
  rep: { label: "The rep's mistake", hint: 'Student gets everything back; Pritis absorbs Paystack\'s fee.' },
  student: { label: "The student's mistake", hint: 'Paystack\'s fee is kept from the refund.' },
};

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function row(label, value) {
  return el('div', { className: 'detail-row' }, el('dt', { textContent: label }), el('dd', { textContent: value ?? '—' }));
}

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

function decideBox(r) {
  // Whose mistake: decides the amount, shown before confirming.
  const fieldset = el('fieldset', { className: 'choice-list' }, el('legend', { textContent: 'Whose mistake was it?' }));
  const amount = (key) => (key === 'student' ? r.student_mistake_refund_kobo : r.full_refund_kobo);
  for (const [key, m] of Object.entries(MISTAKE)) {
    const input = el('input', { type: 'radio', name: `mistake-${r.id}`, value: key });
    fieldset.append(el('label', { className: 'choice' }, input,
      el('span', {}, el('strong', { textContent: m.label }), ` · refund ${formatNaira(amount(key))}`,
        el('span', { className: 'muted small', textContent: ` ${m.hint}` }))));
  }
  const chosen = () => fieldset.querySelector('input:checked')?.value;

  const approve = el('button', { type: 'button', className: 'btn btn-primary btn-inline', textContent: 'Approve refund', disabled: true });
  let armed = false;
  fieldset.addEventListener('change', () => {
    armed = false;
    const short = balance != null && balance < amount(chosen());
    approve.disabled = short;
    approve.textContent = short
      ? `Paystack balance too low for ${formatNaira(amount(chosen()))}`
      : `Approve and refund ${formatNaira(amount(chosen()))}`;
  });
  approve.addEventListener('click', async () => {
    const label = approve.textContent;
    if (!armed) {
      armed = true;
      approve.textContent = 'Click again to confirm';
      setTimeout(() => { if (armed) { armed = false; approve.textContent = label; } }, 5000);
      return;
    }
    armed = false;
    showError(errorEl, null);
    approve.disabled = true;
    approve.textContent = 'Working…';
    try {
      const res = await api.post(`/refunds/${r.id}/approve`, { mistake_by: chosen() });
      notice(res.status === 'failed'
        ? `Paystack refused the refund: ${res.problem}. Nothing was taken from the rep.`
        : `Refund of ${formatNaira(res.amount_kobo)} sent to Paystack. It shows as Refunded when Paystack confirms.`);
      await load();
    } catch (err) {
      showError(errorEl, err);
      approve.disabled = false;
      approve.textContent = label;
    }
  });

  const reason = el('textarea', { rows: 2, maxLength: 500, placeholder: 'Reason shown to the student and rep' });
  reason.setAttribute('aria-label', 'Reason for rejecting');
  const reject = el('button', { type: 'button', className: 'btn btn-inline', textContent: 'Reject' });
  reject.addEventListener('click', async () => {
    showError(errorEl, null);
    const text = reason.value.trim();
    if (text.length < 5) {
      reason.focus();
      return showError(errorEl, 'Write a short reason (at least 5 characters).');
    }
    reject.disabled = true;
    try {
      await api.post(`/refunds/${r.id}/reject`, { reason: text });
      notice('Rejected. The payment stands.');
      await load();
    } catch (err) {
      showError(errorEl, err);
      reject.disabled = false;
    }
  });
  return el('div', {}, fieldset,
    el('div', { className: 'panel-actions' }, approve, el('div', { className: 'reject-box' }, reason, reject)));
}

function refundCard(r) {
  const details = el('dl', { className: 'details' },
    row('Payment', `${r.payment_reference}${r.receipt_no ? ` · receipt ${r.receipt_no}` : ''}`),
    row('Student paid', `${formatNaira(r.total_kobo)} on ${formatDateTime(r.paid_at)}`),
    row('Paystack fee (not returned)', formatNaira(r.paystack_fee_kobo)),
    row('Reason', r.reason),
    row('Requested', `${formatDateTime(r.created_at)} by ${r.requested_by_email}`),
    row('Rep', `${r.rep_name} · Available now ${formatNaira(r.rep_available_kobo)}`),
    row('Payment settled', r.settled ? 'Yes: the rep\'s share comes off Available (it can go below zero)' : 'Not yet: the rep\'s share comes off Pending'),
  );
  if (r.status !== 'requested') {
    details.append(row('Refund amount', formatNaira(r.amount_kobo)));
    if (r.mistake_by) details.append(row('Whose mistake', `${MISTAKE[r.mistake_by].label}${r.fee_borne_kobo ? ` · Pritis absorbs ${formatNaira(r.fee_borne_kobo)}` : ''}`));
  }
  if (r.reviewed_at) details.append(row('Decided', `${formatDateTime(r.reviewed_at)} by ${r.reviewed_by_email ?? '—'}`));
  if (r.paystack_status && r.status === 'processing') details.append(row('Paystack says', r.paystack_status));
  if (r.processed_at) details.append(row('Refunded', formatDateTime(r.processed_at)));
  if (r.problem) details.append(row(r.status === 'rejected' ? 'Why rejected' : 'Problem', r.problem));

  const section = el('section', { className: 'panel' },
    el('div', { className: 'page-head' },
      el('h2', { textContent: `${r.student_name} · ${formatNaira(r.total_kobo)}` }),
      el('span', { className: 'badge', textContent: STATUS_LABEL[r.status] })),
    el('p', { className: 'muted small', textContent: `${r.due_title} · ${r.class_name} · ${r.student_email}` }),
    details);
  if (r.status === 'requested' && canDecide) section.append(decideBox(r));
  return section;
}

async function load() {
  showError(errorEl, null);
  if (canDecide) {
    try {
      balance = (await api.get('/paystack/balance')).balance_kobo;
      document.getElementById('balance').textContent = formatNaira(balance);
    } catch {
      balance = null;
      document.getElementById('balance').textContent = 'unavailable';
    }
  }
  const page = await api.get(`/refunds?status=${tab}`);
  list.replaceChildren(...page.items.map(refundCard));
  if (!page.items.length) list.append(el('p', { className: 'panel muted', textContent: 'Nothing here.' }));
}

document.querySelectorAll('.tabs a').forEach((a) => {
  if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page');
});
renderLayout('/refunds.html').then((me) => {
  canDecide = ['accountant', 'super_admin'].includes(me.role);
  document.getElementById('balance-box').hidden = !canDecide;
  return load();
}).catch((err) => showError(errorEl, err));
