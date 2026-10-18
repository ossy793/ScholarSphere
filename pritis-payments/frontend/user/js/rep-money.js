// The rep's wallet: balances, requesting a payout (with its one service charge line),
// payout history, and asking to change the payout bank account.
import { api } from './api.js';
import { formatNaira, koboToNairaInput, nairaToKobo } from '/shared/money.js';
import { setBusy, showError } from '/shared/ui.js';

const STATUS = {
  requested: { label: 'Waiting for approval', badge: 'badge badge-invited' },
  processing: { label: 'Being sent', badge: 'badge badge-invited' },
  paid: { label: 'Paid', badge: 'badge badge-active' },
  rejected: { label: 'Not approved', badge: 'badge badge-disabled' },
  failed: { label: 'Failed · money returned', badge: 'badge badge-disabled' },
  reversed: { label: 'Reversed · money returned', badge: 'badge badge-disabled' },
};

let errorEl;
let noticeFn;
let wallet = null;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function date(iso) {
  return new Date(iso).toLocaleDateString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium' });
}

export async function loadWallet() {
  wallet = await api.get('/rep/wallet');
  document.getElementById('pending').textContent = formatNaira(wallet.pending_kobo);
  document.getElementById('available').textContent = formatNaira(wallet.available_kobo);
  document.getElementById('held').textContent = formatNaira(wallet.held_kobo);
  const refundHeld = document.getElementById('refund-held');
  refundHeld.hidden = wallet.refund_held_kobo <= 0;
  refundHeld.textContent = `${formatNaira(wallet.refund_held_kobo)} is set aside for a refund to a student that Paystack is sending.`;

  const blocked = document.getElementById('payout-blocked');
  blocked.textContent = wallet.blocked_reason ?? '';
  blocked.hidden = wallet.can_request;
  document.getElementById('payout-form').hidden = !wallet.can_request;

  const rows = document.getElementById('payouts');
  rows.replaceChildren(...wallet.payouts.map((p) => {
    const s = STATUS[p.status];
    const status = el('td', {}, el('span', { className: s.badge, textContent: s.label }));
    if (p.reason && ['rejected', 'failed', 'reversed'].includes(p.status)) {
      status.append(el('div', { className: 'muted small', textContent: p.reason }));
    }
    return el('tr', {},
      el('td', { textContent: date(p.created_at) }),
      el('td', { className: 'num', textContent: formatNaira(p.amount_kobo) }),
      el('td', { className: 'num', textContent: formatNaira(p.net_kobo) }),
      status);
  }));
  if (!wallet.payouts.length) {
    rows.append(el('tr', {}, el('td', { colSpan: 4, className: 'muted', textContent: 'No payouts yet.' })));
  }
}

let quoteTimer = null;
async function updateQuote() {
  const form = document.getElementById('payout-form');
  const button = document.getElementById('payout-button');
  const kobo = nairaToKobo(form.elements.amount.value);
  const set = (id, text) => { document.getElementById(id).textContent = text; };
  button.disabled = true;
  if (!kobo) {
    ['pq-amount', 'pq-charge', 'pq-net'].forEach((id) => set(id, '—'));
    return;
  }
  if (kobo > wallet.available_kobo) {
    set('pq-amount', formatNaira(kobo));
    set('pq-charge', '—');
    set('pq-net', `Up to ${formatNaira(wallet.available_kobo)}`);
    return;
  }
  const q = await api.get(`/rep/payouts/quote?amount_kobo=${kobo}`);
  set('pq-amount', formatNaira(q.amount_kobo));
  set('pq-charge', `− ${formatNaira(q.service_charge_kobo)}`);
  set('pq-net', q.net_kobo > 0 ? formatNaira(q.net_kobo) : 'Too small');
  button.disabled = q.net_kobo <= 0;
  button.textContent = `Request payout of ${formatNaira(q.amount_kobo)}`;
}

function setupPayoutForm() {
  const form = document.getElementById('payout-form');
  const button = document.getElementById('payout-button');
  form.elements.amount.addEventListener('input', () => {
    clearTimeout(quoteTimer);
    quoteTimer = setTimeout(() => updateQuote().catch((e) => showError(errorEl, e)), 300);
  });
  document.getElementById('payout-all').addEventListener('click', () => {
    form.elements.amount.value = koboToNairaInput(wallet.available_kobo);
    updateQuote().catch((e) => showError(errorEl, e));
  });
  let armed = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    showError(errorEl, null);
    const kobo = nairaToKobo(form.elements.amount.value);
    if (!kobo) return;
    if (!armed) {
      armed = true;
      button.textContent = 'Click again to confirm';
      setTimeout(() => { armed = false; updateQuote().catch(() => {}); }, 5000);
      return;
    }
    armed = false;
    setBusy(button, true, 'Requesting…');
    try {
      await api.post('/rep/payouts', { amount_kobo: kobo });
      noticeFn('Payout requested. The money is on hold until Pritis approves it; we\'ll email you.');
      form.reset();
      await loadWallet();
    } catch (err) {
      showError(errorEl, err);
    } finally {
      setBusy(button, false);
    }
  });
}

async function loadChange() {
  const change = await api.get('/rep/bank-account/change');
  const status = document.getElementById('change-status');
  const toggle = document.getElementById('change-toggle');
  if (change?.status === 'pending') {
    status.textContent = `New account waiting for approval: ${change.bank_name} · ${change.account_name} · ••••${change.last4}. Payouts are paused until it's checked.`;
    status.className = 'alert alert-warning small';
    status.hidden = false;
    toggle.hidden = true;
  } else if (change?.status === 'rejected') {
    status.textContent = `Your last account change wasn't approved: ${change.reject_reason}`;
    status.className = 'alert alert-error small';
    status.hidden = false;
    toggle.hidden = false;
  } else {
    status.hidden = true;
    toggle.hidden = false;
  }
}

function setupBankChange() {
  const form = document.getElementById('change-form');
  const nameEl = document.getElementById('change-name');
  let resolvedFor = null;
  let timer = null;

  document.getElementById('change-toggle').addEventListener('click', async () => {
    form.hidden = !form.hidden;
    if (!form.hidden && form.elements.bank_code.options.length <= 1) {
      const banks = await api.get('/banks');
      form.elements.bank_code.replaceChildren(new Option('Choose your bank', ''), ...banks.map((b) => new Option(b.name, b.code)));
    }
  });

  const lookUp = async () => {
    const bank = form.elements.bank_code.value;
    const number = form.elements.account_number.value;
    resolvedFor = null;
    if (!bank || !/^\d{10}$/.test(number)) { nameEl.hidden = true; return; }
    nameEl.className = 'alert alert-success';
    nameEl.textContent = 'Checking account…';
    nameEl.hidden = false;
    try {
      const res = await api.post('/bank-accounts/resolve', { bank_code: bank, account_number: number });
      resolvedFor = `${bank}|${number}`;
      nameEl.textContent = `Account name: ${res.account_name}`;
    } catch (err) {
      nameEl.className = 'alert alert-error';
      nameEl.textContent = err.message;
    }
  };
  const schedule = () => { clearTimeout(timer); timer = setTimeout(lookUp, 400); };
  form.elements.bank_code.addEventListener('change', schedule);
  form.elements.account_number.addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 10);
    schedule();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    showError(errorEl, null);
    const bank = form.elements.bank_code.value;
    const number = form.elements.account_number.value;
    if (resolvedFor !== `${bank}|${number}`) return showError(errorEl, 'Wait for the account name to show, and check it is yours.');
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, 'Submitting…');
    try {
      await api.post('/rep/bank-account/change', { bank_code: bank, account_number: number });
      noticeFn('New payout account submitted. Pritis will check it; payouts are paused until then.');
      form.reset();
      form.hidden = true;
      nameEl.hidden = true;
      await Promise.all([loadChange(), loadWallet()]);
    } catch (err) {
      showError(errorEl, err);
    } finally {
      setBusy(button, false);
    }
  });
}

export async function setupMoney(errorElement, notice) {
  errorEl = errorElement;
  noticeFn = notice;
  setupPayoutForm();
  setupBankChange();
  await Promise.all([loadWallet(), loadChange()]);
}
