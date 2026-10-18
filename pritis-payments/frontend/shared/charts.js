// Chart helpers for both sites, on the self-hosted Chart.js (/shared/vendor/chart.umd.min.js,
// loaded by a <script> tag before the page module; it defines window.Chart).
//
// Rules followed (see the data-viz notes in the README): series colours in a fixed order
// (Pritis blue first; validated for colour-blind separation), one y-axis per chart, thin
// marks with rounded data-ends, a legend for 2+ series, a hover tooltip, and a "Show as
// table" view under every chart (three palette colours are below 3:1 on white).
import { formatNaira } from './money.js';

export const SERIES = ['#0077FF', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];
const INK = '#0A1628';
const MUTED = '#4A6080';
const GRID = '#E6EDF7';
const AXIS = '#C9D6EA';
const FONT = '"Inter", system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

const charts = new WeakMap();

/** ₦1,200,000 -> "₦1.2m", ₦45,000 -> "₦45k": short labels for axis ticks. */
export function shortNaira(kobo) {
  const naira = kobo / 100;
  const abs = Math.abs(naira);
  if (abs >= 1e6) return `₦${(naira / 1e6).toFixed(abs >= 1e7 ? 0 : 1)}m`;
  if (abs >= 1e3) return `₦${(naira / 1e3).toFixed(abs >= 1e4 ? 0 : 1)}k`;
  return `₦${naira.toFixed(0)}`;
}

function base(Chart) {
  Chart.defaults.font.family = FONT;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = MUTED;
  Chart.defaults.plugins.tooltip.backgroundColor = '#0A1628';
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.titleFont = { weight: '600' };
  Chart.defaults.maintainAspectRatio = false;
}

function axes({ money, horizontal = false, stacked = false }) {
  const valueAxis = {
    beginAtZero: true,
    stacked,
    grid: { color: GRID, drawTicks: false },
    border: { display: false },
    ticks: { padding: 6, maxTicksLimit: 5, callback: (v) => (money ? shortNaira(v) : Number.isInteger(v) ? v : '') },
  };
  const categoryAxis = {
    stacked,
    grid: { display: false },
    border: { color: AXIS },
    ticks: { padding: 6, color: INK, autoSkip: true, maxRotation: 0 },
  };
  return horizontal ? { x: valueAxis, y: categoryAxis } : { x: categoryAxis, y: valueAxis };
}

/** Draws (or redraws) a chart on a canvas inside a .chart-box, and fills its table view.
 * @param {HTMLCanvasElement} canvas
 * @param {{type: 'bar'|'line', labels: string[], series: {label: string, values: number[]}[],
 *          money?: boolean, horizontal?: boolean, stacked?: boolean, valueLabel?: string}} spec
 */
export function drawChart(canvas, spec) {
  const { Chart } = window;
  if (!Chart) throw new Error('Charts could not load.');
  base(Chart);
  charts.get(canvas)?.destroy();

  const format = (v) => (spec.money ? formatNaira(v) : String(v));
  const isLine = spec.type === 'line';
  const datasets = spec.series.map((s, i) => {
    const color = SERIES[i];
    return isLine
      ? { label: s.label, data: s.values, borderColor: color, backgroundColor: color, borderWidth: 2,
          pointRadius: s.values.length <= 14 ? 4 : 0, pointHoverRadius: 5, tension: 0, fill: false }
      : { label: s.label, data: s.values, backgroundColor: color, borderRadius: 4,
          borderSkipped: spec.stacked && i < spec.series.length - 1 ? false : 'start',
          maxBarThickness: 28, categoryPercentage: 0.7, barPercentage: 0.9,
          borderColor: '#ffffff', borderWidth: spec.stacked ? { [spec.horizontal ? 'right' : 'top']: 2 } : 0 };
  });

  const chart = new Chart(canvas, {
    type: spec.type,
    data: { labels: spec.labels, datasets },
    options: {
      indexAxis: spec.horizontal ? 'y' : 'x',
      animation: false,
      interaction: isLine ? { mode: 'index', intersect: false } : { mode: 'nearest', intersect: true },
      scales: axes(spec),
      plugins: {
        // A legend only when there is more than one series; the title names a single one.
        legend: { display: spec.series.length > 1, position: 'top', align: 'start',
                  labels: { color: INK, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'rectRounded' } },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${format(ctx.parsed[spec.horizontal ? 'x' : 'y'])}` } },
      },
    },
  });
  charts.set(canvas, chart);
  fillTable(canvas, spec, format);
  return chart;
}

/** The table view under the chart: same numbers, readable without colour. */
function fillTable(canvas, spec, format) {
  const box = canvas.closest('.chart-box');
  const details = box?.querySelector('details.chart-table');
  if (!details) return;
  const table = document.createElement('table');
  const head = document.createElement('tr');
  for (const h of [spec.valueLabel ?? '', ...spec.series.map((s) => s.label)]) {
    const th = document.createElement('th');
    th.textContent = h;
    head.append(th);
  }
  const thead = document.createElement('thead');
  thead.append(head);
  const tbody = document.createElement('tbody');
  spec.labels.forEach((label, i) => {
    const tr = document.createElement('tr');
    const first = document.createElement('td');
    first.textContent = label;
    tr.append(first);
    for (const s of spec.series) {
      const td = document.createElement('td');
      td.className = 'num';
      td.textContent = format(s.values[i]);
      tr.append(td);
    }
    tbody.append(tr);
  });
  table.append(thead, tbody);
  const wrap = document.createElement('div');
  wrap.className = 'table-wrap';
  wrap.append(table);
  details.querySelector('.table-wrap')?.remove();
  details.append(wrap);
}

/** "2026-10-17" -> "17 Oct" */
export function shortDay(iso) {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-NG', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

/** "2026-10" -> "Oct 2026" */
export function monthLabel(ym) {
  return new Date(`${ym}-15T12:00:00Z`).toLocaleDateString('en-NG', { month: 'short', year: 'numeric', timeZone: 'UTC' });
}
