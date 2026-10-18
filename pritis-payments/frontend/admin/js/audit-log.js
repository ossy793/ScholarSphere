import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { showError } from '/shared/ui.js';

const PAGE_SIZE = 50;
const ACTIONS = {
  'settings.update': 'Changed settings',
  'admin.login': 'Logged in',
  'admin.invite': 'Invited a staff member',
  'admin.invite_resent': 'Resent an invitation',
  'admin.invite_accepted': 'Accepted their invitation',
  'admin.disable': 'Disabled a staff account',
  'admin.enable': 'Enabled a staff account',
  'admin.reset_authenticator': 'Reset a staff authenticator',
  'rep_application.approve': 'Approved a rep application',
  'rep_application.reject': 'Rejected a rep application',
  'due.approve': 'Approved a due',
  'due.reject': 'Rejected a due',
  'settlement.mark': 'Marked payments settled',
  'payout.approve': 'Approved a payout',
  'payout.reject': 'Rejected a payout',
  'bank_change.approve': 'Approved a payout account change',
  'bank_change.reject': 'Rejected a payout account change',
};

const errorEl = document.getElementById('error');
const rows = document.getElementById('rows');
const filter = document.getElementById('filter');
let page = 1;

function show(value) {
  if (value === null || value === undefined) return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function changesList(before, after) {
  const ul = document.createElement('ul');
  ul.className = 'changes';
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]);
  for (const key of keys) {
    const li = document.createElement('li');
    const from = before && key in before ? show(before[key]) : null;
    const to = after && key in after ? show(after[key]) : null;
    li.textContent = from !== null && to !== null
      ? `${key}: ${from} → ${to}`
      : `${key}: ${to ?? from}`;
    ul.append(li);
  }
  return keys.size ? ul : document.createTextNode('—');
}

function td(content, className) {
  const cell = document.createElement('td');
  if (className) cell.className = className;
  if (content instanceof Node) cell.append(content);
  else cell.textContent = content;
  return cell;
}

async function load() {
  const params = new URLSearchParams({ page, page_size: PAGE_SIZE });
  if (filter.value) params.set('action', filter.value);
  const data = await api.get(`/audit-logs?${params}`);

  rows.replaceChildren(...data.items.map((item) => {
    const tr = document.createElement('tr');
    tr.append(
      td(formatDateTime(item.created_at)),
      td(item.actor_email ?? (item.actor_type === 'system' ? 'System' : item.actor_type)),
      td(ACTIONS[item.action] ?? item.action),
      td(changesList(item.before, item.after)),
      td(item.ip ?? '—', 'mono'),
    );
    return tr;
  }));
  if (!data.items.length) {
    const tr = document.createElement('tr');
    const cell = td('Nothing recorded yet.', 'muted');
    cell.colSpan = 5;
    tr.append(cell);
    rows.append(tr);
  }

  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  document.getElementById('page-info').textContent = `Page ${page} of ${pages} · ${data.total} entries`;
  document.getElementById('prev').disabled = page <= 1;
  document.getElementById('next').disabled = page >= pages;
}

function reload() {
  showError(errorEl, null);
  load().catch((err) => showError(errorEl, err));
}

filter.addEventListener('change', () => { page = 1; reload(); });
document.getElementById('prev').addEventListener('click', () => { page -= 1; reload(); });
document.getElementById('next').addEventListener('click', () => { page += 1; reload(); });

renderLayout('/audit-log.html').then(load).catch((err) => showError(errorEl, err));
