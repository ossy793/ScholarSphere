// Admin shell: top bar with the signed-in staff member, and the navigation for their role.
// The server enforces every permission; hiding links here is only for tidiness.
import { api } from './api.js';
import { store } from '/shared/ui.js';

export const ROLE_LABELS = {
  super_admin: 'Super admin',
  accountant: 'Accountant',
  support: 'Customer support',
};

const NAV = [
  { href: '/index.html', label: 'Overview', roles: ['super_admin', 'accountant', 'support'] },
  { href: '/reports.html', label: 'Reports', roles: ['super_admin', 'accountant'] },
  { href: '/ledger.html', label: 'Ledger', roles: ['super_admin', 'accountant'] },
  { href: '/users.html', label: 'Users', roles: ['super_admin', 'accountant', 'support'] },
  { href: '/payments.html', label: 'Payments', roles: ['super_admin', 'accountant', 'support'] },
  { href: '/dues.html', label: 'Due approvals', roles: ['super_admin', 'accountant'] },
  { href: '/payouts.html', label: 'Payouts', roles: ['super_admin', 'accountant'] },
  { href: '/refunds.html', label: 'Refunds', roles: ['super_admin', 'accountant', 'support'] },
  { href: '/settlements.html', label: 'Settlements', roles: ['super_admin', 'accountant'] },
  { href: '/rep-applications.html', label: 'Rep applications', roles: ['super_admin', 'accountant', 'support'] },
  { href: '/staff.html', label: 'Staff accounts', roles: ['super_admin'] },
  { href: '/settings.html', label: 'Settings', roles: ['super_admin'] },
  { href: '/audit-log.html', label: 'Audit log', roles: ['super_admin'] },
];

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

/**
 * Loads the signed-in staff member and draws the shell around <main class="shell-main">.
 * Sends people whose role can't see this page back to the overview.
 * @param {string} page the href of the current page, e.g. '/staff.html'
 */
export async function renderLayout(page) {
  const me = await api.get('/me');
  const item = NAV.find((n) => n.href === page);
  if (item && !item.roles.includes(me.role)) {
    location.replace('/index.html');
    throw new Error('not allowed');
  }

  const logo = el('img', { src: '/shared/img/logo.svg', alt: '', width: 36, height: 36 });
  const brand = el('a', { className: 'brand', href: '/index.html' },
    logo, el('span', {}, 'Pritis ', el('span', { className: 'brand-sub' }, 'Admin')));

  const logout = el('button', { type: 'button', className: 'btn btn-inline', textContent: 'Log out' });
  logout.addEventListener('click', async () => {
    try { await api.post('/auth/logout'); } finally { location.href = '/login.html'; }
  });
  const who = el('span', { className: 'topbar-user' },
    el('span', { textContent: `${me.full_name || me.email} · ${ROLE_LABELS[me.role]}` }), logout);

  const nav = el('nav', { className: 'side-nav', id: 'side-nav', ariaLabel: 'Admin' });
  for (const n of NAV.filter((x) => x.roles.includes(me.role))) {
    const a = el('a', { href: n.href, textContent: n.label });
    if (n.href === page) a.setAttribute('aria-current', 'page');
    nav.append(a);
  }

  const main = document.querySelector('.shell-main');
  const body = el('div', { className: 'shell-body' }, nav);
  main.replaceWith(body);
  body.append(main);

  const toggle = el('button', { type: 'button', className: 'nav-toggle' },
    el('span'), el('span'), el('span'));
  toggle.setAttribute('aria-controls', 'side-nav');
  const header = el('header', { className: 'topbar' },
    el('div', { className: 'topbar-start' }, toggle, brand), who);
  document.body.prepend(header);
  setupNavToggle(toggle, body, header, nav);
  main.hidden = false;
  return me;
}

// Desktop: the sidebar is open unless the person collapsed it (remembered).
// Phones: the menu starts closed and closes again after each visit.
const desktop = window.matchMedia('(min-width: 900px)');

function setupNavToggle(toggle, body, header, nav) {
  const setOpen = (open) => {
    body.dataset.nav = open ? 'open' : 'closed';
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Hide menu' : 'Show menu');
    toggle.title = open ? 'Hide menu' : 'Show menu';
  };
  const initial = () => setOpen(desktop.matches && store.get('dpa_nav') !== 'closed');
  initial();
  desktop.addEventListener('change', initial);

  toggle.addEventListener('click', () => {
    const open = body.dataset.nav !== 'open';
    setOpen(open);
    if (desktop.matches) store.set('dpa_nav', open ? 'open' : 'closed');
  });

  // The sidebar sits under the sticky top bar, so it needs the bar's height.
  const measure = () => document.documentElement.style.setProperty('--topbar-h', `${header.offsetHeight}px`);
  measure();
  new ResizeObserver(measure).observe(header);

  // Keep the current page's link in view when the list is longer than the screen.
  nav.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest' });
}

/** "2026-10-16T09:30:00Z" -> local date and time, shown in Lagos time. */
export function formatDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-NG', {
    timeZone: 'Africa/Lagos', dateStyle: 'medium', timeStyle: 'short',
  });
}
