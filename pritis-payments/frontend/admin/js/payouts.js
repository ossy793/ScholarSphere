// Payout approvals (with the Paystack balance and the rep's history) and bank account
// changes. Accountant and super admin.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const TABS = ['requested', 'processing', 'paid', 'rejected', 'failed', 'bank'];
const qs = new URLSearchParams(location.search).get('tab');
const tab = TABS.includes(qs) ? qs : 'requested';
const list = document.getElementById('list');
const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
let balance = null;

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

/** Approve (two clicks) and reject (needs a reason). */
function decide({ approveLabel, onApprove, onReject, approveDisabled = false }) {
  const approve = el('button', { type: 'button', className: 'btn btn-primary btn-inline', textContent: approveLabel, disabled: approveDisabled });
  let armed = false;
  approve.addEventListener('click', async () => {
    if (!armed) {
      armed = true;
      approve.textContent = 'Click again to confirm';
      setTimeout(() => { armed = false; approve.textContent = approveLabel; }, 5000);
      return;
    }
    showError(errorEl, null);
    approve.disabled = true;
    approve.textContent = 'Working…';
    try {
      notice(await onApprove());
      await load();
    } catch (err) {
      showError(errorEl, err);
      approve.disabled = false;
      approve.textContent = approveLabel;
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
      return showError(errorEl, 'Write a short reason (at least 5 characters) for the rep.');
    }
    reject.disabled = true;
    try {
      notice(await onReject(text));
      await load();
    } catch (err) {
      showError(errorEl, err);
      reject.disabled = false;
    }
  });
  return el('div', { className: 'panel-actions' }, approve, el('div', { className: 'reject-box' }, reason, reject));
}

const STATUS_LABEL = {
  requested: 'Waiting', processing: 'Being sent', paid: 'Paid', rejected: 'Rejected', failed: 'Failed', reversed: 'Reversed',
};

function payoutCard(p) {
  const needed = p.net_kobo + p.transfer_fee_kobo;
  const short = balance != null && balance < needed;
  const details = el('dl', { className: 'details' },
    row('Payout requested', formatNaira(p.amount_kobo)),
    row('Platform fee', formatNaira(p.platform_fee_kobo)),
    row('Paystack transfer fee', `${formatNaira(p.transfer_fee_kobo)}${p.transfer_fee_actual_kobo != null ? ` (actual ${formatNaira(p.transfer_fee_actual_kobo)})` : ''}`),
    row('Sent to the rep', formatNaira(p.net_kobo)),
    row('To account', p.bank_label),
    row('Rep balances now', `Pending ${formatNaira(p.rep_pending_kobo)} · Available ${formatNaira(p.rep_available_kobo)} · On hold ${formatNaira(p.rep_held_kobo)}`),
    row('Requested', formatDateTime(p.created_at)),
    row('Reference', p.transfer_reference),
  );
  if (p.reviewed_at) details.append(row('Decided', `${formatDateTime(p.reviewed_at)} by ${p.reviewed_by_email ?? '—'}`));
  if (p.paid_at) details.append(row('Paid', formatDateTime(p.paid_at)));
  if (p.reason) details.append(row('Reason', p.reason));

  const section = el('section', { className: 'panel' },
    el('div', { className: 'page-head' },
      el('h2', { textContent: `${p.rep_name} · ${formatNaira(p.net_kobo)}` }),
      el('span', { className: 'badge', textContent: STATUS_LABEL[p.status] })),
    el('p', { className: 'muted small', textContent: `${p.class_name} · ${p.rep_email}` }),
    details);

  if (p.history.length) {
    section.append(el('p', { className: 'muted small', textContent:
      `Earlier payouts: ${p.history.map((h) => `${formatNaira(h.amount_kobo)} ${STATUS_LABEL[h.status].toLowerCase()} (${new Date(h.created_at).toLocaleDateString('en-NG', { dateStyle: 'medium' })})`).join(', ')}` }));
  }
  if (p.status === 'requested') {
    if (p.bank_change_pending) {
      section.append(el('p', { className: 'alert alert-warning small', textContent: 'This rep has a bank account change waiting. Decide that first (Bank account changes tab).' }));
    }
    if (short) {
      section.append(el('p', { className: 'alert alert-error small', textContent: `The Paystack balance (${formatNaira(balance)}) can't cover this transfer and its fee (${formatNaira(needed)}). Top it up first.` }));
    }
    section.append(decide({
      approveLabel: `Approve and send ${formatNaira(p.net_kobo)}`,
      approveDisabled: short || p.bank_change_pending,
      onApprove: async () => {
        const res = await api.post(`/payouts/${p.id}/approve`);
        return res.status === 'failed'
          ? `Paystack refused the transfer: ${res.reason}. The money is back in the rep's Available balance.`
          : `Transfer started: ${formatNaira(res.net_kobo)} to ${res.bank_label}. It shows as Paid when Paystack confirms.`;
      },
      onReject: async (reason) => {
        await api.post(`/payouts/${p.id}/reject`, { reason });
        return `Rejected. ${formatNaira(p.amount_kobo)} is back in the rep's Available balance.`;
      },
    }));
  }
  return section;
}

function changeCard(c) {
  const details = el('dl', { className: 'details' },
    row('Current account', c.current_account),
    row('New account', `${c.bank_name} · ${c.account_name} · ••••${c.last4}`),
    row('Asked', formatDateTime(c.created_at)),
  );
  if (c.reviewed_at) details.append(row('Decided', `${formatDateTime(c.reviewed_at)} by ${c.reviewed_by_email ?? '—'}`));
  if (c.reject_reason) details.append(row('Reason', c.reject_reason));
  const section = el('section', { className: 'panel' },
    el('div', { className: 'page-head' },
      el('h2', { textContent: `${c.rep_name}: new payout account` }),
      el('span', { className: 'badge', textContent: c.status })),
    el('p', { className: 'muted small', textContent: `${c.class_name} · ${c.rep_email}` }),
    details,
    el('p', { className: 'muted small', textContent: 'Check the new account name belongs to the rep. Changing where money goes is a common fraud route; if unsure, contact the rep first.' }));
  if (c.status === 'pending') {
    section.append(decide({
      approveLabel: 'Approve new account',
      onApprove: async () => {
        await api.post(`/bank-changes/${c.id}/approve`);
        return `${c.rep_name}'s payouts now go to ${c.bank_name} ••••${c.last4}.`;
      },
      onReject: async (reason) => {
        await api.post(`/bank-changes/${c.id}/reject`, { reason });
        return 'Rejected. The rep keeps their previous account.';
      },
    }));
  }
  return section;
}

async function load() {
  showError(errorEl, null);
  try {
    balance = (await api.get('/paystack/balance')).balance_kobo;
    document.getElementById('balance').textContent = formatNaira(balance);
  } catch (err) {
    balance = null;
    document.getElementById('balance').textContent = 'unavailable';
  }
  if (tab === 'bank') {
    const changes = [
      ...(await api.get('/bank-changes?status=pending')),
      ...(await api.get('/bank-changes?status=approved')).slice(0, 10),
      ...(await api.get('/bank-changes?status=rejected')).slice(0, 10),
    ];
    list.replaceChildren(...changes.map(changeCard));
    if (!changes.length) list.append(el('p', { className: 'panel muted', textContent: 'No bank account changes.' }));
    return;
  }
  const page = await api.get(`/payouts?status=${tab}`);
  list.replaceChildren(...page.items.map(payoutCard));
  if (!page.items.length) list.append(el('p', { className: 'panel muted', textContent: 'Nothing here.' }));
}

document.querySelectorAll('.tabs a').forEach((a) => {
  if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page');
});
renderLayout('/payouts.html').then(load).catch((err) => showError(errorEl, err));
