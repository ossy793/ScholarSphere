import { api } from './api.js';
import { renderLayout } from './layout.js';
import { formatNaira, koboToNairaInput, nairaToKobo } from '/shared/money.js';
import { setBusy, showError } from '/shared/ui.js';

const form = document.getElementById('settings-form');
const bandsBody = document.getElementById('bands');
const errorEl = document.getElementById('error');
const savedEl = document.getElementById('saved');
const PERCENT = /^\d{1,2}(\.\d{1,2})?$/;

function bandRow(band = { up_to_kobo: null, fee_kobo: 0 }) {
  const tr = document.createElement('tr');
  const upTo = document.createElement('input');
  upTo.type = 'text';
  upTo.inputMode = 'decimal';
  upTo.placeholder = 'and above';
  upTo.value = band.up_to_kobo == null ? '' : koboToNairaInput(band.up_to_kobo);
  upTo.className = 'band-up-to';
  upTo.setAttribute('aria-label', 'Payouts up to, in naira');

  const fee = document.createElement('input');
  fee.type = 'text';
  fee.inputMode = 'decimal';
  fee.value = koboToNairaInput(band.fee_kobo);
  fee.className = 'band-fee';
  fee.setAttribute('aria-label', 'Transfer fee, in naira');

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'btn btn-inline';
  remove.textContent = 'Remove';
  remove.addEventListener('click', () => {
    if (bandsBody.children.length > 1) tr.remove();
  });

  for (const node of [upTo, fee, remove]) {
    const td = document.createElement('td');
    td.append(node);
    tr.append(td);
  }
  return tr;
}

function updateExample() {
  const paystack = Number(form.elements.paystack_fee_percent.value) || 0;
  const platform = Number(form.elements.platform_fee_percent.value) || 0;
  // Integer kobo maths, rounded half up like the server: example on a ₦5,000 due.
  const due = 500_000;
  const charge = Math.round(due * paystack / 100) + Math.round(due * platform / 100);
  document.getElementById('charge-example').textContent =
    `Example: on a ${formatNaira(due)} due the student pays ${formatNaira(due + charge)} ` +
    `(${formatNaira(due)} + ${formatNaira(charge)} service charge, ${(paystack + platform).toFixed(2).replace(/\.?0+$/, '')}%).`;
}

function fill(settings) {
  form.elements.paystack_fee_percent.value = settings.paystack_fee_percent;
  form.elements.platform_fee_percent.value = settings.platform_fee_percent;
  form.elements.payout_fee_percent.value = settings.payout_fee_percent;
  form.elements.max_due_amount.value = koboToNairaInput(settings.max_due_amount_kobo);
  form.elements.settlement_mode.value = settings.settlement_mode;
  form.elements.available_after_hours.value = String(settings.available_after_hours);
  bandsBody.replaceChildren(...settings.transfer_fee_bands.map(bandRow));
  updateExample();
}

/** Reads the form into the API shape, or throws a message for the person to fix. */
function read() {
  const body = {};
  for (const name of ['paystack_fee_percent', 'platform_fee_percent', 'payout_fee_percent']) {
    const value = String(form.elements[name].value).trim();
    if (!PERCENT.test(value) || Number(value) > 20) {
      throw new Error('Percentages must be between 0 and 20, with at most 2 decimal places.');
    }
    body[name] = value;
  }
  const maxDue = nairaToKobo(form.elements.max_due_amount.value);
  if (maxDue == null || maxDue < 10_000) throw new Error('Enter a maximum due amount of at least ₦100.');
  body.max_due_amount_kobo = maxDue;
  body.settlement_mode = form.elements.settlement_mode.value || 'balance';
  const hours = Number(String(form.elements.available_after_hours.value).trim());
  if (!Number.isInteger(hours) || hours < 0 || hours > 240) throw new Error('Hours before Available must be a whole number from 0 to 240.');
  body.available_after_hours = hours;

  const rows = [...bandsBody.children];
  body.transfer_fee_bands = rows.map((tr, i) => {
    const upToText = tr.querySelector('.band-up-to').value.trim();
    const fee = nairaToKobo(tr.querySelector('.band-fee').value);
    if (fee == null) throw new Error(`Enter a transfer fee in naira on row ${i + 1}.`);
    const last = i === rows.length - 1;
    if (last && upToText) throw new Error('Leave "up to" empty on the last transfer fee row.');
    const upTo = last ? null : nairaToKobo(upToText);
    if (!last && !upTo) throw new Error(`Enter the "up to" amount on row ${i + 1}.`);
    return { up_to_kobo: upTo, fee_kobo: fee };
  });
  return body;
}

document.getElementById('add-band').addEventListener('click', () => {
  // A new band goes before the open-ended last one.
  const last = bandsBody.lastElementChild;
  const row = bandRow({ up_to_kobo: null, fee_kobo: 0 });
  bandsBody.insertBefore(row, last);
  row.querySelector('.band-up-to').placeholder = '';
  row.querySelector('.band-up-to').focus();
});

form.addEventListener('input', () => {
  savedEl.hidden = true;
  updateExample();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  savedEl.hidden = true;
  let body;
  try {
    body = read();
  } catch (err) {
    return showError(errorEl, err.message);
  }
  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Saving…');
  try {
    fill(await api.put('/settings', body));
    savedEl.hidden = false;
  } catch (err) {
    showError(errorEl, err);
  } finally {
    setBusy(button, false);
  }
});

async function init() {
  await renderLayout('/settings.html');
  fill(await api.get('/settings'));
}

init().catch((err) => showError(errorEl, err));
