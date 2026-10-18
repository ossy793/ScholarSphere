// Settlement: payments Paystack hasn't reported as settled, the manual "mark settled"
// fallback, the last nightly check and the worker's jobs. Accountant and super admin.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { formatNaira } from '/shared/money.js';
import { setBusy, showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
const rows = document.getElementById('rows');
const form = document.getElementById('mark-form');
const markButton = document.getElementById('mark-button');
const selectAll = document.getElementById('select-all');
let items = [];

const JOB_NAMES = {
  email_outbox: 'Emails',
  webhook_retry: 'Paystack event retry',
  pending_payments: 'Pending payments check',
  settlement_check: 'Paystack settlement check',
  reconciliation: 'Nightly check',
};

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function td(content, className) {
  return el('td', className ? { className } : {}, content instanceof Node ? content : String(content ?? '—'));
}

function selected() {
  return [...rows.querySelectorAll('input[type="checkbox"]:checked')].map((c) => c.value);
}

function updateSelection() {
  const ids = new Set(selected());
  const chosen = items.filter((i) => ids.has(i.payment_id));
  markButton.disabled = chosen.length === 0;
  markButton.textContent = chosen.length
    ? `Mark ${chosen.length} as settled (${formatNaira(chosen.reduce((s, i) => s + i.due_kobo, 0))} to Available)`
    : 'Mark selected as settled';
  selectAll.checked = items.length > 0 && chosen.length === items.length;
}

async function loadUnsettled() {
  const page = await api.get('/settlements/unsettled');
  items = page.items;
  document.getElementById('totals').textContent =
    `${page.count} payment(s) · students paid ${formatNaira(page.total_paid_kobo)} · reps' share ${formatNaira(page.total_due_kobo)}`;
  rows.replaceChildren(...items.map((i) => {
    const box = el('input', { type: 'checkbox', value: i.payment_id });
    box.setAttribute('aria-label', `Select ${i.reference}`);
    box.addEventListener('change', updateSelection);
    return el('tr', {},
      td(box), td(formatDateTime(i.paid_at)), td(`${i.class_name} · ${i.rep_name}`),
      td(i.reference, 'mono'), td(formatNaira(i.total_kobo), 'num'),
      td(i.paystack_fee_kobo == null ? '—' : formatNaira(i.paystack_fee_kobo), 'num'),
      td(formatNaira(i.due_kobo), 'num'));
  }));
  if (!items.length) {
    const cell = td('Everything paid so far has been settled.', 'muted');
    cell.colSpan = 7;
    rows.append(el('tr', {}, cell));
  }
  updateSelection();
}

async function loadStatus() {
  const status = await api.get('/settlements/status');
  document.getElementById('jobs').replaceChildren(...status.jobs.map((j) => el('tr', {},
    td(JOB_NAMES[j.name] ?? j.name),
    td(j.schedule),
    td(j.last_started_at ? `${formatDateTime(j.last_started_at)} · ${j.last_status}` : 'never'),
    td(j.last_status === 'error' ? (j.last_error ?? '') : (j.last_result ?? '')),
  )));

  const r = status.latest_reconciliation;
  const summary = document.getElementById('recon-summary');
  const list = document.getElementById('recon-items');
  if (!r) {
    summary.textContent = 'The nightly check hasn\'t run yet. It runs at 02:00 Lagos time while the worker is running.';
    list.replaceChildren();
    return;
  }
  const label = { clean: 'Clean: everything matches.', mismatch: 'Problems found:', error: 'Could not finish:' }[r.status];
  summary.textContent = `${formatDateTime(r.created_at)}, for ${new Date(r.period_start).toLocaleDateString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium' })}. ${label}`;
  summary.className = r.status === 'clean' ? 'alert alert-success' : 'alert alert-error';
  list.replaceChildren(...r.items.slice(0, 20).map((i) => el('li', { textContent: `${i.kind}: ${i.ref} ${i.detail}` })));
}

selectAll.addEventListener('change', () => {
  rows.querySelectorAll('input[type="checkbox"]').forEach((c) => { c.checked = selectAll.checked; });
  updateSelection();
});

let armed = false;
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  noticeEl.hidden = true;
  const ids = selected();
  const reference = String(form.elements.bank_reference.value).trim();
  if (!ids.length) return;
  if (reference.length < 3) return showError(errorEl, 'Enter the bank statement reference you checked these against.');
  if (!armed) {
    armed = true;
    markButton.textContent = 'Click again to confirm';
    setTimeout(() => { armed = false; updateSelection(); }, 5000);
    return;
  }
  armed = false;
  setBusy(markButton, true, 'Saving…');
  try {
    const res = await api.post('/settlements/mark', { payment_ids: ids, bank_reference: reference });
    noticeEl.textContent = `${res.settled} payment(s) marked settled. The reps' shares are now Available.`;
    noticeEl.hidden = false;
    form.reset();
    await loadUnsettled();
  } catch (err) {
    showError(errorEl, err);
  } finally {
    setBusy(markButton, false);
    updateSelection();
  }
});

renderLayout('/settlements.html')
  .then(() => Promise.all([loadUnsettled(), loadStatus()]))
  .catch((err) => showError(errorEl, err));
