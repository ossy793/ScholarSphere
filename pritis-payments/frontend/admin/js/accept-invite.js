import { api } from './api.js';
import { ROLE_LABELS } from './layout.js';
import { setBusy, showError } from '/shared/ui.js';
import { attachPasswordChecklist, passwordIsStrong } from '/shared/password-rules.js';
import '/shared/password-toggle.js';

const token = new URLSearchParams(location.search).get('token') ?? '';
// Keep the secret link out of the address bar and browser history once it's read.
history.replaceState(null, '', location.pathname);

const form = document.getElementById('accept-form');
const errorEl = document.getElementById('error');

attachPasswordChecklist(form.elements.password, document.getElementById('password-rules'));

async function init() {
  if (!token) throw new Error('This page needs the link from your invitation email.');
  const setup = await api.post('/auth/invite/start', { token });
  document.getElementById('who').textContent = setup.full_name || setup.email;
  document.getElementById('role').textContent = ROLE_LABELS[setup.role];
  form.elements.email.value = setup.email;
  document.getElementById('qr').src = setup.qr_svg;
  // Grouped in fours so it's easier to type into the app.
  document.getElementById('secret').textContent = setup.secret.match(/.{1,4}/g).join(' ');
  form.hidden = false;
  form.elements.password.focus();
}

form.elements.code.addEventListener('input', (event) => {
  event.target.value = event.target.value.replace(/\D/g, '').slice(0, 6);
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const password = String(form.elements.password.value);
  const code = String(form.elements.code.value).trim();
  if (!passwordIsStrong(password)) return showError(errorEl, 'Your password needs everything in the list below it.');
  if (password !== String(form.elements.confirm_password.value)) return showError(errorEl, 'The two passwords do not match.');
  if (!/^\d{6}$/.test(code)) return showError(errorEl, 'Enter the 6-digit code from your authenticator app.');

  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Saving…');
  try {
    await api.post('/auth/invite/accept', { token, password, code });
    form.hidden = true;
    document.getElementById('done').hidden = false;
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
  }
});

init()
  .catch((err) => showError(errorEl, err))
  .finally(() => { document.getElementById('loading').hidden = true; });
