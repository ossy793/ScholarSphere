import { api } from './api.js';
import { safeNext, setBusy, showError } from '/shared/ui.js';
import '/shared/password-toggle.js';

const next = safeNext(new URLSearchParams(location.search).get('next')) ?? '/index.html';

const passwordForm = document.getElementById('password-form');
const codeForm = document.getElementById('code-form');
const codeInput = codeForm.elements.code;
const errorEl = document.getElementById('error');

function showStep(step) {
  passwordForm.hidden = step !== 'password';
  codeForm.hidden = step !== 'code';
  (step === 'password' ? passwordForm.elements.email : codeInput).focus();
}

passwordForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const email = String(passwordForm.elements.email.value).trim();
  const password = String(passwordForm.elements.password.value);
  if (!email || !password) return showError(errorEl, 'Enter your email and password.');

  const button = passwordForm.querySelector('button[type="submit"]');
  setBusy(button, true, 'Checking…');
  try {
    await api.post('/auth/login', { email, password });
    passwordForm.elements.password.value = '';
    codeInput.value = '';
    showStep('code');
  } catch (err) {
    showError(errorEl, err);
  } finally {
    setBusy(button, false);
  }
});

codeInput.addEventListener('input', () => {
  codeInput.value = codeInput.value.replace(/\D/g, '').slice(0, 6);
  if (codeInput.value.length === 6) codeForm.requestSubmit();
});

codeForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const code = codeInput.value.trim();
  if (!/^\d{6}$/.test(code)) return showError(errorEl, 'Enter the 6-digit code from your authenticator app.');

  const button = codeForm.querySelector('button[type="submit"]');
  setBusy(button, true, 'Logging in…');
  try {
    await api.post('/auth/totp', { code });
    location.replace(next);
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
    if (err.code === 'mfa_expired') showStep('password');
    else codeInput.select();
  }
});

document.getElementById('back').addEventListener('click', () => {
  showError(errorEl, null);
  showStep('password');
});

passwordForm.elements.email.focus();
