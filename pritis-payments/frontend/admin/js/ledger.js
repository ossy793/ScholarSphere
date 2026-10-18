// Ledger explorer: accounts with balances, and entries filtered by account, kind and dates.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const form = document.getElementById('filters');
const PAGE_SIZE = 50;
let page = 1;
let txn = null; // when set, show one whole transaction

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function lagosDate(offsetDays = 0) {
  return new Date(Date.now() + 3600 * 1000 + offsetDays * 86400000).toISOString().slice(0, 10);
}

async function loadAccounts() {
  const accounts = await api.get('/ledger/accounts');
  document.getElementById('accounts').replaceChildren(...accounts.map((a) => {
    const link = el('button', { type: 'button', className: 'btn-link', textContent: a.code });
    link.addEventListener('click', () => {
      form.elements.account.value = a.code;
      txn = null;
      page = 1;
      loadEntries();
    });
    return el('tr', {},
      el('td', {}, link, el('div', { className: 'muted small', textContent: a.name })),
      el('td', { textContent: a.type }),
      el('td', { className: 'num', textContent: String(a.entries) }),
      el('td', { className: 'num', textContent: formatNaira(a.balance_kobo) }));
  }));
}

async function loadEntries() {
  showError(errorEl, null);
  const params = new URLSearchParams({ page, page_size: PAGE_SIZE });
  if (txn) {
    params.set('txn_id', txn);
  } else {
    if (form.elements.account.value.trim()) params.set('account', form.elements.account.value.trim());
    if (form.elements.ref_type.value) params.set('ref_type', form.elements.ref_type.value);
    if (form.elements.from.value) params.set('from', form.elements.from.value);
    if (form.elements.to.value) params.set('to', form.elements.to.value);
  }
  try {
    const data = await api.get(`/ledger?${params}`);
    document.getElementById('showing').textContent = txn ? `Showing one transaction (${txn}).` : '';
    document.getElementById('entries').replaceChildren(...data.items.map((e) => {
      const t = el('button', { type: 'button', className: 'btn-link mono', textContent: e.txn_id.slice(0, 8) });
      t.addEventListener('click', () => { txn = e.txn_id; page = 1; loadEntries(); });
      return el('tr', {},
        el('td', { textContent: formatDateTime(e.created_at) }),
        el('td', {}, t),
        el('td', { className: 'mono', textContent: e.account_code }),
        el('td', { className: 'num', textContent: e.direction === 'debit' ? formatNaira(e.amount_kobo) : '' }),
        el('td', { className: 'num', textContent: e.direction === 'credit' ? formatNaira(e.amount_kobo) : '' }),
        el('td', { textContent: e.ref_type }),
        el('td', { className: 'small', textContent: e.memo ?? '' }));
    }));
    if (!data.items.length) {
      document.getElementById('entries').append(el('tr', {}, el('td', { colSpan: 7, className: 'muted', textContent: 'No entries match.' })));
    }
    const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    document.getElementById('page-info').textContent = `Page ${page} of ${pages} · ${data.total} entries`;
    document.getElementById('prev').disabled = page <= 1;
    document.getElementById('next').disabled = page >= pages;
  } catch (err) {
    showError(errorEl, err);
  }
}

form.addEventListener('submit', (e) => { e.preventDefault(); txn = null; page = 1; loadEntries(); });
document.getElementById('clear').addEventListener('click', () => {
  form.reset();
  form.elements.from.value = lagosDate(-29);
  form.elements.to.value = lagosDate();
  txn = null;
  page = 1;
  loadEntries();
});
document.getElementById('prev').addEventListener('click', () => { page -= 1; loadEntries(); });
document.getElementById('next').addEventListener('click', () => { page += 1; loadEntries(); });

renderLayout('/ledger.html')
  .then(() => {
    form.elements.from.value = lagosDate(-29);
    form.elements.to.value = lagosDate();
    return Promise.all([loadAccounts(), loadEntries()]);
  })
  .catch((err) => showError(errorEl, err));
