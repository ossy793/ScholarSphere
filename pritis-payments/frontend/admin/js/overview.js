// Overview (design section 10): approvals waiting and counts for everyone; for the
// accountant and super admin also today's money and the last 30 days' graphs.
import { api } from './api.js';
import { renderLayout } from './layout.js';
import { drawChart, shortDay } from '/shared/charts.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');

function tile(label, value, note, href) {
  const box = document.createElement('div');
  box.className = 'stat';
  const l = document.createElement('span');
  l.className = 'stat-label';
  l.textContent = label;
  const v = document.createElement('span');
  v.className = 'stat-value';
  v.textContent = value;
  box.append(l, v);
  if (note) {
    const n = document.createElement('span');
    n.className = 'muted small';
    n.textContent = note;
    box.append(n);
  }
  if (href) {
    const a = document.createElement('a');
    a.href = href;
    a.textContent = 'Open';
    box.append(a);
  }
  return box;
}

function emptyChart(canvas, text) {
  const div = document.createElement('div');
  div.className = 'empty-chart';
  div.textContent = text;
  canvas.replaceWith(div);
}

async function init() {
  const me = await renderLayout('/index.html');
  document.getElementById('name').textContent = me.full_name || me.email;
  const money = me.role === 'super_admin' || me.role === 'accountant';
  const counts = await api.get('/reports/overview');

  const waiting = [];
  waiting.push(tile('Rep applications', counts.rep_applications_waiting, 'to verify', '/rep-applications.html'));
  waiting.push(tile('Refunds', counts.refunds_waiting, money ? 'to approve' : 'waiting for approval', '/refunds.html'));
  if (money) {
    waiting.push(tile('Dues and changes', counts.dues_waiting, 'to approve', '/dues.html'));
    waiting.push(tile('Payouts', counts.payouts_waiting, 'to approve', '/payouts.html'));
    waiting.push(tile('Bank account changes', counts.bank_changes_waiting, 'to check', '/payouts.html?tab=bank'));
  }
  document.getElementById('waiting').replaceChildren(...waiting);
  document.getElementById('counts').replaceChildren(
    tile('Students', counts.students), tile('Verified reps', counts.reps),
    tile('Classes', counts.classes), tile('Live dues', counts.live_dues), tile('Staff', counts.staff),
  );

  if (!money) return;
  const s = await api.get('/reports/summary');
  document.getElementById('money-tiles').replaceChildren(
    tile('Payments today', String(s.payments_today_count), formatNaira(s.payments_today_kobo)),
    tile('Revenue, all time', formatNaira(s.revenue_kobo), 'After Paystack\'s actual fees', '/reports.html'),
    tile('Owed to reps', formatNaira(s.owed_to_reps_kobo), `${formatNaira(s.owed_available_kobo)} available to pay out`),
    tile('Nightly check', s.reconciliation ? s.reconciliation.status : 'not run yet',
      s.reconciliation ? `${s.reconciliation.items} item(s)` : 'Runs at 02:00 Lagos', '/settlements.html'),
  );
  document.getElementById('money').hidden = false;

  const t = await api.get('/reports/trends');
  const days = t.payments_per_day.map((d) => shortDay(d.day));
  document.getElementById('charts').hidden = false;
  drawChart(document.getElementById('c-count'), {
    type: 'bar', labels: days, valueLabel: 'Day',
    series: [{ label: 'Payments', values: t.payments_per_day.map((d) => d.count) }],
  });
  drawChart(document.getElementById('c-value'), {
    type: 'line', labels: days, money: true, valueLabel: 'Day',
    series: [{ label: 'Value paid', values: t.payments_per_day.map((d) => d.value_kobo) }],
  });
  const classCanvas = document.getElementById('c-class');
  if (t.collections_by_class.length) {
    drawChart(classCanvas, {
      type: 'bar', horizontal: true, money: true, valueLabel: 'Class',
      labels: t.collections_by_class.map((c) => c.class_name),
      series: [{ label: 'Collected', values: t.collections_by_class.map((c) => c.collected_kobo) }],
    });
  } else {
    emptyChart(classCanvas, 'No payments in the last 30 days.');
  }
}

init().catch((err) => showError(errorEl, err));
