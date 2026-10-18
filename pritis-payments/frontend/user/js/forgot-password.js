import { api } from './api.js';
import { safeNext, setBusy, showError } from '/shared/ui.js';
import '/shared/password-toggle.js';
import { attachPasswordChecklist, passwordIsStrong } from '/shared/password-rules.js';

const next = safeNext(new URLSearchParams(location.search).get('next')) ?? '/dashboard.html';

const emailForm = document.getElementById('email-form');
const resetForm = document.getElementById('reset-form');
const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
const resendButton = document.getElementById('resend');
let email = '';

attachPasswordChecklist(resetForm.elements.new_password, document.getElementById('password-rules'));

document.getElementById('login-link').href = `/login.html?next=${encodeURIComponent(next)}`;

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

async function requestCode() {
  await api.post('/auth/forgot-password', { email });
}

function showStep(step) {
  emailForm.hidden = step !== 'email';
  resetForm.hidden = step !== 'reset';
  showError(errorEl, null);
  notice('');
  (step === 'email' ? emailForm.elements.email : resetForm.elements.code).focus();
}

emailForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  email = String(new FormData(emailForm).get('email')).trim();
  if (!email.includes('@')) return showError(errorEl, 'Enter a valid email address.');

  const button = emailForm.querySelector('button[type="submit"]');
  setBusy(button, true, 'Sending…');
  try {
    await requestCode();
    document.getElementById('sent-to').textContent = email;
    showStep('reset');
  } catch (err) {
    showError(errorEl, err);
  } finally {
    setBusy(button, false);
  }
});

resetForm.elements.code.addEventListener('input', (event) => {
  event.target.value = event.target.value.replace(/\D/g, '').slice(0, 6);
});

resetForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  notice('');
  const data = new FormData(resetForm);
  const code = String(data.get('code')).trim();
  const newPassword = String(data.get('new_password'));
  if (!/^\d{6}$/.test(code)) return showError(errorEl, 'Enter the 6-digit code from the email.');
  if (!passwordIsStrong(newPassword)) {
    return showError(errorEl, 'Your new password needs everything in the list below it.');
  }

  const button = resetForm.querySelector('button[type="submit"]');
  setBusy(button, true, 'Saving…');
  try {
    await api.post('/auth/reset-password', { email, code, new_password: newPassword });
    notice('Password changed. Taking you in…');
    setTimeout(() => location.replace(next), 800);
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
  }
});

resendButton.addEventListener('click', async () => {
  showError(errorEl, null);
  resendButton.disabled = true;
  try {
    await requestCode();
    notice('A new code is on its way. Earlier codes no longer work.');
    resetForm.elements.code.value = '';
    resetForm.elements.code.focus();
  } catch (err) {
    showError(errorEl, err);
  } finally {
    setTimeout(() => { resendButton.disabled = false; }, 30_000);
  }
});

document.getElementById('change-email').addEventListener('click', () => showStep('email'));
