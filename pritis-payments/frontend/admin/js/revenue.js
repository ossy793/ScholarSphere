// Revenue: Pritis's share of the Paystack balance, its bank account, and withdrawals.
// The accountant can look; the super admin saves the account and withdraws.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { formatNaira, koboToNairaInput, nairaToKobo } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
const withdrawForm = document.getElementById('withdraw-form');
const accountForm = document.getElementById('account-form');
const STATUS = { processing: 'Being sent', paid: 'Paid', failed: 'Failed' };
let data = null;
let isBoss = false;

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

function tile(label, value, note) {
  return el('div', { className: 'stat' },
    el('span', { className: 'stat-label', textContent: label }),
    el('span', { className: 'stat-value', textContent: value }),
    el('span', { className: 'muted small', textContent: note }));
}

/** Two clicks for anything that moves money. */
function confirmTwice(button, label, confirmLabel) {
  if (button.dataset.armed === 'yes') return true;
  button.dataset.armed = 'yes';
  button.textContent = confirmLabel;
  setTimeout(() => { button.dataset.armed = ''; button.textContent = label; }, 6000);
  return false;
}

function render() {
  const d = data;
  document.getElementById('mode-note').hidden = d.settlement_mode !== 'bank';
  document.getElementById('tiles').replaceChildren(
    tile('Paystack balance', d.paystack_balance_kobo == null ? 'unavailable' : formatNaira(d.paystack_balance_kobo), 'Reps\' money and Pritis\'s together'),
    tile('Owed to reps', formatNaira(d.owed_to_reps_kobo), 'Stays in the balance'),
    tile('Pritis\'s revenue in Paystack', formatNaira(d.revenue_kobo), 'After the fees Pritis bore'),
    tile('Can withdraw now', formatNaira(d.withdrawable_kobo), d.processing_kobo ? `${formatNaira(d.processing_kobo)} being sent` : 'Paystack\'s transfer fee included'),
    tile('Withdrawn so far', formatNaira(d.withdrawn_kobo), 'To Pritis\'s bank'),
  );

  document.getElementById('account-now').textContent = d.account
    ? `${d.account.label} · saved by ${d.account.set_by} on ${formatDateTime(d.account.set_at)}`
    : 'No account saved yet. Revenue can\'t be withdrawn until one is.';
  accountForm.hidden = !isBoss;
  document.getElementById('withdraw-panel').hidden = !isBoss;
  document.getElementById('to-account').textContent = d.account ? `Goes to ${d.account.label}.` : 'Save Pritis\'s bank account below first.';

  const body = document.getElementById('history');
  body.replaceChildren(...d.withdrawals.map((w) => {
    const status = el('td', {}, el('span', { className: 'badge', textContent: STATUS[w.status] }));
    if (w.failure_reason) status.append(el('div', { className: 'muted small', textContent: w.failure_reason }));
    return el('tr', {},
      el('td', { textContent: formatDateTime(w.created_at) }),
      el('td', { className: 'num', textContent: formatNaira(w.amount_kobo) }),
      el('td', { className: 'num', textContent: formatNaira(w.transfer_fee_actual_kobo ?? w.transfer_fee_kobo) }),
      el('td', {}, el('div', { textContent: w.bank_label }), w.note ? el('div', { className: 'muted small', textContent: w.note }) : ''),
      status,
      el('td', { textContent: w.requested_by_email }));
  }));
  if (!d.withdrawals.length) body.append(el('tr', {}, el('td', { colSpan: 6, className: 'muted', textContent: 'No withdrawals yet.' })));
}

async function load() {
  data = await api.get('/revenue');
  render();
}

// The fee for the amount being typed.
let feeTimer = null;
withdrawForm.elements.amount.addEventListener('input', () => {
  clearTimeout(feeTimer);
  const kobo = nairaToKobo(withdrawForm.elements.amount.value);
  const line = document.getElementById('fee-line');
  if (!kobo) { line.textContent = ''; return; }
  feeTimer = setTimeout(async () => {
    const f = await api.get(`/revenue/fee?amount_kobo=${kobo}`);
    const total = kobo + f.transfer_fee_kobo;
    line.textContent = `Paystack's transfer fee: ${formatNaira(f.transfer_fee_kobo)}. ${formatNaira(total)} leaves the balance`
      + (total > data.withdrawable_kobo ? ` (more than the ${formatNaira(data.withdrawable_kobo)} available).` : '.');
  }, 250);
});

document.getElementById('max-btn').addEventListener('click', async () => {
  // The fee depends on the amount, so take it off the available figure and check again.
  let amount = data.withdrawable_kobo;
  for (let i = 0; i < 3 && amount > 0; i += 1) {
    const f = await api.get(`/revenue/fee?amount_kobo=${amount}`);
    amount = data.withdrawable_kobo - f.transfer_fee_kobo;
  }
  withdrawForm.elements.amount.value = amount > 0 ? koboToNairaInput(amount) : '';
  withdrawForm.elements.amount.dispatchEvent(new Event('input'));
});

withdrawForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  notice('');
  const kobo = nairaToKobo(withdrawForm.elements.amount.value);
  if (!kobo) return showError(errorEl, 'Enter the amount in naira.');
  const button = document.getElementById('withdraw-btn');
  if (!confirmTwice(button, 'Withdraw', `Click again to send ${formatNaira(kobo)}`)) return;
  button.disabled = true;
  try {
    data = await api.post('/revenue/withdrawals', { amount_kobo: kobo, note: withdrawForm.elements.note.value.trim() || null });
    const w = data.withdrawals[0];
    notice(w.status === 'failed' ? `Paystack refused it: ${w.failure_reason}` : `${formatNaira(w.amount_kobo)} is on its way to ${w.bank_label}.`);
    withdrawForm.reset();
    document.getElementById('fee-line').textContent = '';
    render();
  } catch (err) {
    showError(errorEl, err);
  } finally {
    button.disabled = false;
    button.dataset.armed = '';
    button.textContent = 'Withdraw';
  }
});

accountForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  notice('');
  const bank = accountForm.elements.bank_code.value;
  const number = accountForm.elements.account_number.value.trim();
  if (!bank) return showError(errorEl, 'Choose the bank.');
  if (!/^\d{10}$/.test(number)) return showError(errorEl, 'The account number is 10 digits.');
  const button = document.getElementById('account-btn');
  if (!confirmTwice(button, 'Look up and save', 'Click again to make this Pritis\'s account')) return;
  button.disabled = true;
  try {
    data = await api.put('/revenue/account', { bank_code: bank, account_number: number });
    notice(`Saved: ${data.account.label}. Check the name is Pritis's.`);
    accountForm.reset();
    render();
  } catch (err) {
    showError(errorEl, err);
  } finally {
    button.disabled = false;
    button.dataset.armed = '';
    button.textContent = 'Look up and save';
  }
});

renderLayout('/revenue.html').then(async (me) => {
  isBoss = me.role === 'super_admin';
  await load();
  if (isBoss) {
    const banks = await api.get('/revenue/banks');
    const select = accountForm.elements.bank_code;
    select.replaceChildren(el('option', { value: '', textContent: 'Choose a bank' }),
      ...banks.map((b) => el('option', { value: b.code, textContent: b.name })));
  }
}).catch((err) => showError(errorEl, err));
