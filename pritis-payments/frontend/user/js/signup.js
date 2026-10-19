import { api } from './api.js';
import { readProfileFields, setupUniversityPicker } from './profile-fields.js';
import { safeNext, setBusy, showError } from '/shared/ui.js';
import '/shared/password-toggle.js';
import { attachPasswordChecklist, passwordIsStrong } from '/shared/password-rules.js';

const next = safeNext(new URLSearchParams(location.search).get('next'));
const landing = next ?? '/dashboard.html?view=student';

const form = document.getElementById('signup-form');
const errorEl = document.getElementById('error');
let findUniversity = () => null;

attachPasswordChecklist(form.elements.password, document.getElementById('password-rules'));

document.getElementById('login-link').href = `/login.html?next=${encodeURIComponent(landing)}`;

setupUniversityPicker(document.getElementById('universities'))
  .then((find) => { findUniversity = find; })
  .catch((err) => showError(errorEl, err));

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const data = new FormData(form);
  const fullName = String(data.get('full_name')).trim();
  const email = String(data.get('email')).trim();
  const password = String(data.get('password'));
  if (fullName.length < 2) return showError(errorEl, 'Enter your full name.');
  if (!email.includes('@')) return showError(errorEl, 'Enter a valid email address.');
  const profile = readProfileFields(form, findUniversity);
  if (profile.error) return showError(errorEl, profile.error);
  if (!passwordIsStrong(password)) {
    form.elements.password.focus();
    return showError(errorEl, 'Your password needs everything in the list below it.');
  }
  if (password !== String(data.get('confirm_password'))) {
    form.elements.confirm_password.focus();
    return showError(errorEl, 'The two passwords do not match.');
  }

  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Creating account…');
  try {
    await api.post('/auth/signup', { full_name: fullName, email, password, ...profile.values });
    // Signed in already; confirm the email with the code we just sent.
    location.href = `/verify-email.html?next=${encodeURIComponent(landing)}`;
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
  }
});
