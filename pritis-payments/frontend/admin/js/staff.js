import { api } from './api.js';
import { ROLE_LABELS, formatDateTime, renderLayout } from './layout.js';
import { setBusy, showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const form = document.getElementById('invite-form');
const rows = document.getElementById('staff-rows');
let me = null;

function cell(text, className) {
  const td = document.createElement('td');
  if (className) td.className = className;
  if (text instanceof Node) td.append(text);
  else td.textContent = text;
  return td;
}

function badge(status) {
  const span = document.createElement('span');
  span.className = `badge badge-${status}`;
  span.textContent = { active: 'Active', invited: 'Invited', disabled: 'Disabled' }[status] ?? status;
  return span;
}

function showLink(url, name, note = null) {
  document.getElementById('link-note').textContent = note
    ?? `Emailed to ${name}. You can also send them this link yourself; it works once.`;
  document.getElementById('invite-link').value = url;
  document.getElementById('link-panel').hidden = false;
}

/**
 * A row action. With confirmLabel, the first click only arms the button (for actions that
 * sign someone out); a second click within 5 seconds runs it.
 */
function actionButton(label, onClick, confirmLabel = null) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-inline';
  button.textContent = label;
  let armed = false;
  button.addEventListener('click', async () => {
    if (confirmLabel && !armed) {
      armed = true;
      button.textContent = confirmLabel;
      setTimeout(() => { armed = false; button.textContent = label; }, 5000);
      return;
    }
    showError(errorEl, null);
    button.disabled = true;
    try {
      await onClick();
      await load();
    } catch (err) {
      showError(errorEl, err);
      button.disabled = false;
    }
  });
  return button;
}

async function load() {
  const staff = await api.get('/admin-users');
  rows.replaceChildren(...staff.map((s) => {
    const actions = document.createElement('div');
    actions.className = 'actions';
    if (s.status === 'invited') {
      actions.append(actionButton('Resend invite', async () => {
        const res = await api.post(`/admin-users/${s.id}/resend-invite`);
        showLink(res.invite_url, s.full_name || s.email);
      }));
    }
    if (s.id !== me.id && s.status === 'active') {
      actions.append(actionButton('Reset authenticator', async () => {
        const res = await api.post(`/admin-users/${s.id}/reset-authenticator`);
        showLink(res.invite_url, s.full_name || s.email,
          'Their old password and authenticator no longer work, and they have been signed out. ' +
          'This set-up link was emailed to them; you can also send it yourself. It works once.');
      }, 'Click again to reset'));
    }
    if (s.id !== me.id) {
      actions.append(s.status === 'disabled'
        ? actionButton('Enable', () => api.post(`/admin-users/${s.id}/enable`))
        : actionButton('Disable', () => api.post(`/admin-users/${s.id}/disable`), 'Click again to disable'));
    }
    const tr = document.createElement('tr');
    tr.append(
      cell(s.full_name || '—'),
      cell(s.email),
      cell(ROLE_LABELS[s.role]),
      cell(badge(s.status)),
      cell(formatDateTime(s.last_login_at)),
      cell(actions),
    );
    return tr;
  }));
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const body = {
    full_name: String(form.elements.full_name.value).trim(),
    email: String(form.elements.email.value).trim(),
    role: form.elements.role.value,
  };
  if (body.full_name.length < 2) return showError(errorEl, 'Enter their full name.');
  if (!body.email.includes('@')) return showError(errorEl, 'Enter a valid email address.');

  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Sending…');
  try {
    const res = await api.post('/admin-users', body);
    showLink(res.invite_url, body.full_name);
    form.reset();
    await load();
  } catch (err) {
    showError(errorEl, err);
  } finally {
    setBusy(button, false);
  }
});

document.getElementById('copy-link').addEventListener('click', async (event) => {
  const input = document.getElementById('invite-link');
  try {
    await navigator.clipboard.writeText(input.value);
    event.target.textContent = 'Copied';
  } catch {
    input.select();
  }
});

async function init() {
  me = await renderLayout('/staff.html');
  await load();
}

init().catch((err) => showError(errorEl, err));
