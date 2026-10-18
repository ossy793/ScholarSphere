import { api } from './api.js';
import { readProfileFields, setupUniversityPicker } from './profile-fields.js';
import { safeNext, setBusy, showError } from '/shared/ui.js';

const next = safeNext(new URLSearchParams(location.search).get('next')) ?? '/dashboard.html';

const form = document.getElementById('profile-form');
const errorEl = document.getElementById('error');
let findUniversity = () => null;

async function init() {
  const [me, find] = await Promise.all([
    api.get('/me'),
    setupUniversityPicker(document.getElementById('universities')),
  ]);
  findUniversity = find;
  if (me.profile_complete) {
    location.replace(next);
    return;
  }
  // Pre-fill whatever we already have (the name comes from Google).
  form.elements.full_name.value = me.full_name ?? '';
  if (me.phone) form.elements.phone.value = me.phone;
  if (me.university) form.elements.university.value = me.university.name;
  if (me.level) form.elements.level.value = me.level;
  form.elements.phone.focus();
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const fullName = String(form.elements.full_name.value).trim();
  if (fullName.length < 2) return showError(errorEl, 'Enter your full name.');
  const profile = readProfileFields(form, findUniversity);
  if (profile.error) return showError(errorEl, profile.error);

  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Saving…');
  try {
    await api.put('/me/profile', { full_name: fullName, ...profile.values });
    location.replace(next);
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
  }
});

init().catch((err) => showError(errorEl, err));
