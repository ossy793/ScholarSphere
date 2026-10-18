// Accountant dashboard (design section 10): all-time cards, a period with its figures,
// the graphs, CSV / PDF exports and the nightly checks. Accountant and super admin.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { drawChart, monthLabel, shortDay } from '/shared/charts.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const form = document.getElementById('range');
const CHANNELS = { card: 'Card', bank_transfer: 'Bank transfer', bank: 'Bank', ussd: 'USSD', qr: 'QR',
  mobile_money: 'Mobile money', apple_pay: 'Apple Pay', unknown: 'Unknown' };
const PAYOUT_STATUS = { requested: 'Waiting', processing: 'Being sent', paid: 'Paid', rejected: 'Rejected',
  failed: 'Failed', reversed: 'Reversed' };

function tile(label, value, note) {
  const box = document.createElement('div');
  box.className = 'stat';
  const parts = [['stat-label', label], ['stat-value', value]];
  if (note) parts.push(['muted small', note]);
  for (const [cls, text] of parts) {
    const span = document.createElement('span');
    span.className = cls;
    span.textContent = text;
    box.append(span);
  }
  return box;
}

function lagosToday() {
  return new Date(Date.now() + 3600 * 1000).toISOString().slice(0, 10);
}

function setRange(preset) {
  const today = lagosToday();
  let from;
  if (preset === 'mtd') from = `${today.slice(0, 8)}01`;
  else from = new Date(Date.parse(`${today}T12:00:00Z`) - (Number(preset) - 1) * 86400000).toISOString().slice(0, 10);
  form.elements.from.value = from;
  form.elements.to.value = today;
  document.querySelectorAll('.tabs a').forEach((a) => a.toggleAttribute('aria-current', a.dataset.days === String(preset)));
}

function canvasOrEmpty(id, hasData, text) {
  const canvas = document.getElementById(id);
  const box = canvas?.closest('.chart-canvas');
  box.querySelector('.empty-chart')?.remove();
  canvas.hidden = !hasData;
  if (!hasData) {
    const div = document.createElement('div');
    div.className = 'empty-chart';
    div.textContent = text;
    box.append(div);
  }
  return hasData ? canvas : null;
}

async function loadAllTime() {
  const s = await api.get('/reports/summary');
  document.getElementById('tiles').replaceChildren(
    tile('Revenue', formatNaira(s.revenue_kobo),
      `Service charges ${formatNaira(s.service_charge_revenue_kobo)} · payout fees ${formatNaira(s.payout_fee_revenue_kobo)}`),
    tile('Gross payment volume', formatNaira(s.gross_volume_kobo), `${s.payments_count} payments`),
    tile('Owed to reps', formatNaira(s.owed_to_reps_kobo),
      `Pending ${formatNaira(s.owed_pending_kobo)} · Available ${formatNaira(s.owed_available_kobo)} · On hold ${formatNaira(s.owed_held_kobo)}`),
    tile('Payouts waiting', String(s.payouts_waiting_count), formatNaira(s.payouts_waiting_kobo)),
    tile('Paystack fees paid', formatNaira(s.paystack_fees_kobo)),
    tile('Paid out to reps', formatNaira(s.paid_out_kobo)),
    tile('Refunded to students', formatNaira(s.refunded_kobo),
      `${s.refunded_count} refunds · Paystack fees Pritis absorbed ${formatNaira(s.refund_fee_expense_kobo)}`
      + (s.refunds_in_progress_kobo ? ` · ${formatNaira(s.refunds_in_progress_kobo)} being sent` : '')),
  );
}

async function loadPeriod() {
  showError(errorEl, null);
  const qs = `from=${form.elements.from.value}&to=${form.elements.to.value}`;
  document.getElementById('csv').href = `/api/admin/reports/export.csv?${qs}`;
  document.getElementById('pdf').href = `/api/admin/reports/statement.pdf?${qs}`;
  const [t, rev] = await Promise.all([api.get(`/reports/trends?${qs}`), api.get(`/reports/revenue?${qs}`)]);
  const p = rev.period;
  document.getElementById('period-tiles').replaceChildren(
    tile('Payments', String(p.payments_count), formatNaira(p.gross_volume_kobo)),
    tile('Platform revenue', formatNaira(p.service_charge_revenue_kobo + p.payout_fee_revenue_kobo)),
    tile('Paystack fees', formatNaira(p.paystack_fees_kobo)),
    tile('Paid out', formatNaira(p.paid_out_kobo), `${p.payouts_paid_count} payouts`),
    tile('Refunded', formatNaira(p.refunded_kobo), `${p.refunds_count} refunds`),
  );

  const days = t.payments_per_day.map((d) => shortDay(d.day));
  drawChart(document.getElementById('c-count'), {
    type: 'bar', labels: days, valueLabel: 'Day',
    series: [{ label: 'Payments', values: t.payments_per_day.map((d) => d.count) }],
  });
  drawChart(document.getElementById('c-value'), {
    type: 'line', labels: days, money: true, valueLabel: 'Day',
    series: [{ label: 'Value paid', values: t.payments_per_day.map((d) => d.value_kobo) }],
  });
  drawChart(document.getElementById('c-owed'), {
    type: 'line', labels: t.owed_vs_paid_out.map((d) => shortDay(d.day)), money: true, valueLabel: 'Day',
    series: [
      { label: 'Owed to reps', values: t.owed_vs_paid_out.map((d) => d.owed_kobo) },
      { label: 'Paid out', values: t.owed_vs_paid_out.map((d) => d.paid_out_kobo) },
    ],
  });

  const revenue = canvasOrEmpty('c-revenue', rev.by_month.length > 0, 'No revenue in this period.');
  if (revenue) {
    drawChart(revenue, {
      type: 'bar', stacked: true, money: true, valueLabel: 'Month', labels: rev.by_month.map((m) => monthLabel(m.month)),
      series: [
        { label: 'Service charges', values: rev.by_month.map((m) => m.service_charge_kobo) },
        { label: 'Payout fees', values: rev.by_month.map((m) => m.payout_fee_kobo) },
      ],
    });
  }
  const channel = canvasOrEmpty('c-channel', t.channel_mix.length > 0, 'No payments in this period.');
  if (channel) {
    drawChart(channel, {
      type: 'bar', horizontal: true, valueLabel: 'Channel',
      labels: t.channel_mix.map((c) => CHANNELS[c.channel] ?? c.channel),
      series: [{ label: 'Payments', values: t.channel_mix.map((c) => c.count) }],
    });
  }
  const payouts = canvasOrEmpty('c-payouts', t.payouts_by_status.length > 0, 'No payout requests in this period.');
  if (payouts) {
    drawChart(payouts, {
      type: 'bar', valueLabel: 'Status',
      labels: t.payouts_by_status.map((s) => PAYOUT_STATUS[s.status] ?? s.status),
      series: [{ label: 'Requests', values: t.payouts_by_status.map((s) => s.count) }],
    });
  }
  const byClass = canvasOrEmpty('c-class', t.collections_by_class.length > 0, 'No payments in this period.');
  if (byClass) {
    drawChart(byClass, {
      type: 'bar', horizontal: true, money: true, valueLabel: 'Class',
      labels: t.collections_by_class.map((c) => c.class_name),
      series: [{ label: 'Collected', values: t.collections_by_class.map((c) => c.collected_kobo) }],
    });
  }
}

async function loadRecon() {
  const runs = await api.get('/reconciliation?limit=14');
  const body = document.getElementById('recon');
  body.replaceChildren(...runs.map((r) => {
    const tr = document.createElement('tr');
    const cells = [
      formatDateTime(r.created_at),
      new Date(r.period_start).toLocaleDateString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium' }),
      { clean: 'Clean', mismatch: 'Problems found', error: 'Could not finish' }[r.status],
      r.items.length ? r.items.slice(0, 3).map((i) => `${i.kind}: ${i.ref}`).join('; ') + (r.items.length > 3 ? ` (+${r.items.length - 3} more)` : '') : '—',
    ];
    for (const text of cells) {
      const td = document.createElement('td');
      td.textContent = text;
      tr.append(td);
    }
    return tr;
  }));
  if (!runs.length) {
    const td = document.createElement('td');
    td.colSpan = 4;
    td.className = 'muted';
    td.textContent = 'No nightly checks yet. They run at 02:00 Lagos while the worker is running.';
    const tr = document.createElement('tr');
    tr.append(td);
    body.append(tr);
  }
}

document.querySelectorAll('.tabs a').forEach((a) => a.addEventListener('click', (e) => {
  e.preventDefault();
  setRange(a.dataset.days);
  loadPeriod().catch((err) => showError(errorEl, err));
}));
form.addEventListener('submit', (e) => {
  e.preventDefault();
  document.querySelectorAll('.tabs a').forEach((a) => a.removeAttribute('aria-current'));
  loadPeriod().catch((err) => showError(errorEl, err));
});

renderLayout('/reports.html')
  .then(() => {
    setRange('30');
    return Promise.all([loadAllTime(), loadPeriod(), loadRecon()]);
  })
  .catch((err) => showError(errorEl, err));
