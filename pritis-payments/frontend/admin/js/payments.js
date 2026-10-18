// Payment search for support (accountant read-only): find a payment by reference, receipt
// number or student, with the full split the student never sees.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const form = document.getElementById('search');
const STATUS = { success: 'Paid', pending: 'Pending', failed: 'Failed', abandoned: 'Abandoned', refunded: 'Refunded' };
const REFUND = { requested: 'Refund waiting', processing: 'Refund being sent', processed: 'Refunded', rejected: 'Refund rejected', failed: 'Refund failed' };

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

/** Starting a refund: a reason, then it waits for the accountant or super admin. */
function refundCell(p) {
  const open = ['requested', 'processing'].includes(p.refund_status);
  const cell = el('div', {});
  if (p.refund_status) {
    cell.append(el('a', { href: `/refunds.html?tab=${p.refund_status}`, className: 'small', textContent: REFUND[p.refund_status] }));
  }
  if (p.status !== 'success' || open) return cell;
  const start = el('button', { type: 'button', className: 'btn-link small', textContent: 'Start a refund…' });
  start.addEventListener('click', () => {
    const reason = el('textarea', { rows: 2, maxLength: 500, placeholder: 'Why? (shown to the approver, student and rep)' });
    reason.setAttribute('aria-label', 'Reason for the refund');
    const send = el('button', { type: 'button', className: 'btn btn-inline', textContent: 'Ask for approval' });
    send.addEventListener('click', async () => {
      showError(errorEl, null);
      if (reason.value.trim().length < 5) {
        reason.focus();
        return showError(errorEl, 'Write a short reason (at least 5 characters).');
      }
      send.disabled = true;
      try {
        await api.post('/refunds', { payment_id: p.id, reason: reason.value.trim() });
        cell.replaceChildren(el('a', { href: '/refunds.html', className: 'small', textContent: 'Refund waiting for approval' }));
      } catch (err) {
        showError(errorEl, err);
        send.disabled = false;
      }
    });
    cell.replaceChildren(reason, send);
    reason.focus();
  });
  cell.append(start);
  return cell;
}

async function search() {
  showError(errorEl, null);
  const params = new URLSearchParams();
  if (form.elements.q.value.trim()) params.set('q', form.elements.q.value.trim());
  if (form.elements.status.value) params.set('status', form.elements.status.value);
  const rows = await api.get(`/payments?${params}`);
  document.getElementById('results').replaceChildren(...rows.map((p) => el('tr', {},
    el('td', { textContent: formatDateTime(p.paid_at ?? p.created_at) }),
    el('td', {}, el('div', { textContent: p.student_name }), el('div', { className: 'muted small', textContent: p.student_email })),
    el('td', { textContent: `${p.due_title} · ${p.class_name}` }),
    el('td', { className: 'num', textContent: formatNaira(p.total_kobo) }),
    el('td', { className: 'num', textContent: formatNaira(p.due_kobo) }),
    el('td', { className: 'num', textContent: formatNaira(p.service_charge_kobo) }),
    el('td', { className: 'num', textContent: p.paystack_fee_kobo == null ? '—' : formatNaira(p.paystack_fee_kobo) }),
    el('td', { textContent: STATUS[p.status] + (p.channel ? ` · ${p.channel}` : '') + (p.failure_reason ? ` (${p.failure_reason})` : '') }),
    el('td', { className: 'mono small', textContent: `${p.reference}${p.receipt_no ? ` · ${p.receipt_no}` : ''}` }),
    el('td', { textContent: p.settled_at ? formatDateTime(p.settled_at) : '—' }),
    el('td', {}, refundCell(p)))));
  if (!rows.length) {
    document.getElementById('results').append(el('tr', {}, el('td', { colSpan: 11, className: 'muted', textContent: 'No payments match.' })));
  }
}

form.addEventListener('submit', (e) => { e.preventDefault(); search().catch((err) => showError(errorEl, err)); });
renderLayout('/payments.html').then(search).catch((err) => showError(errorEl, err));
