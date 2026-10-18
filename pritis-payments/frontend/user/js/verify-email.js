import { api } from './api.js';
import { safeNext, setBusy, showError } from '/shared/ui.js';

const next = safeNext(new URLSearchParams(location.search).get('next')) ?? '/dashboard.html';

const form = document.getElementById('code-form');
const input = form.elements.code;
const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
const resendButton = document.getElementById('resend');
const RESEND_COOLDOWN_SECONDS = 60;

document.getElementById('later').href = next;

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

function cooldown(seconds) {
  resendButton.disabled = true;
  const label = 'send a new code';
  const tick = () => {
    if (seconds <= 0) {
      resendButton.textContent = label;
      resendButton.disabled = false;
      return;
    }
    resendButton.textContent = `${label} (${seconds}s)`;
    seconds -= 1;
    setTimeout(tick, 1000);
  };
  tick();
}

async function init() {
  const me = await api.get('/me');
  if (me.email_verified) {
    location.replace(next);
    return;
  }
  document.getElementById('email').textContent = me.email;
  input.focus();
}

// Keep only digits, and submit as soon as six are in.
input.addEventListener('input', () => {
  input.value = input.value.replace(/\D/g, '').slice(0, 6);
  if (input.value.length === 6) form.requestSubmit();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  notice('');
  const code = input.value.trim();
  if (!/^\d{6}$/.test(code)) {
    showError(errorEl, 'Enter the 6-digit code from the email.');
    return;
  }
  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Checking…');
  try {
    await api.post('/auth/verify-email', { code });
    notice('Email verified. Taking you on…');
    setTimeout(() => location.replace(next), 800);
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
    input.select();
  }
});

resendButton.addEventListener('click', async () => {
  showError(errorEl, null);
  resendButton.disabled = true;
  try {
    await api.post('/auth/resend-code');
    notice('A new code is on its way. Earlier codes no longer work.');
    input.value = '';
    input.focus();
    cooldown(RESEND_COOLDOWN_SECONDS);
  } catch (err) {
    showError(errorEl, err);
    resendButton.disabled = false;
  }
});

init().catch((err) => showError(errorEl, err));
