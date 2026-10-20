import { api } from './api.js';
import { safeNext, setBusy, showError, store } from '/shared/ui.js';
import '/shared/password-toggle.js';

const params = new URLSearchParams(location.search);
const next = safeNext(params.get('next'));

const form = document.getElementById('login-form');
const errorEl = document.getElementById('error');
const signupLink = document.getElementById('signup-link');
const forgotLink = document.getElementById('forgot-link');
const roleButtons = document.querySelectorAll('.role-switch button');

const GOOGLE_ERRORS = {
  google_failed: 'Google sign-in did not complete. Please try again.',
  google_not_configured: 'Google sign-in is not available yet. Use email and password.',
  google_email_unverified: 'Your Google email is not verified. Sign up with email and password instead.',
  google_account_conflict: 'This email is already linked to a different Google account.',
  google_no_account: "There's no account with this Google email yet. Create an account first, then you can log in with Google.",
  account_disabled: 'This account has been disabled. Contact support.',
};

let role = store.get('dpp_role') === 'rep' ? 'rep' : 'student';

// Both buttons log into the same account; the choice only picks the dashboard view.
function landing() {
  return next ?? `/dashboard.html?view=${role}`;
}

function selectRole(value) {
  role = value;
  store.set('dpp_role', role);
  roleButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.role === role)));
  signupLink.href = `/signup.html?next=${encodeURIComponent(landing())}`;
  forgotLink.href = `/forgot-password.html?next=${encodeURIComponent(landing())}`;
}

roleButtons.forEach((b) => b.addEventListener('click', () => selectRole(b.dataset.role)));
selectRole(role);

const errorCode = params.get('error');
if (errorCode) showError(errorEl, GOOGLE_ERRORS[errorCode] ?? 'Sign-in failed. Please try again.');

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const data = new FormData(form);
  const email = String(data.get('email')).trim();
  const password = String(data.get('password'));
  if (!email || !password) {
    showError(errorEl, 'Enter your email and password.');
    return;
  }

  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Logging in…');
  try {
    await api.post('/auth/login', { email, password });
    location.href = landing();
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
  }
});
