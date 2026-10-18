import { api } from './api.js';
import { setupUniversityPicker } from './profile-fields.js';
import { setBusy, showError } from '/shared/ui.js';

const form = document.getElementById('apply-form');
const errorEl = document.getElementById('error');
const nameEl = document.getElementById('account-name');
const MAX_PROOF_BYTES = 5 * 1024 * 1024;
let findUniversity = () => null;
let resolvedFor = null; // "bank|number" whose account name is showing
let lookupTimer = null;

function sessions() {
  // Nigerian sessions start around September: offer last, current and next.
  const now = new Date();
  const start = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1;
  return [start - 1, start, start + 1].map((y) => `${y}/${y + 1}`);
}

function fillSessions(selected) {
  const select = document.getElementById('session');
  const values = sessions();
  if (selected && !values.includes(selected)) values.unshift(selected);
  select.replaceChildren(...values.map((v) => new Option(v, v)));
  select.value = selected ?? values[1];
}

function showAccountName(text, ok) {
  nameEl.textContent = text;
  nameEl.className = `alert ${ok ? 'alert-success' : 'alert-error'}`;
  nameEl.hidden = !text;
}

async function lookUpAccount() {
  const bank = form.elements.bank_code.value;
  const number = form.elements.account_number.value;
  resolvedFor = null;
  if (!bank || !/^\d{10}$/.test(number)) {
    showAccountName('', true);
    return;
  }
  showAccountName('Checking account…', true);
  try {
    const res = await api.post('/bank-accounts/resolve', { bank_code: bank, account_number: number });
    resolvedFor = `${bank}|${number}`;
    showAccountName(`Account name: ${res.account_name}`, true);
  } catch (err) {
    showAccountName(err.message, false);
  }
}

function scheduleLookup() {
  clearTimeout(lookupTimer);
  lookupTimer = setTimeout(lookUpAccount, 400);
}

async function loadBanks() {
  const banks = await api.get('/banks');
  const select = form.elements.bank_code;
  select.replaceChildren(new Option('Choose your bank', ''), ...banks.map((b) => new Option(b.name, b.code)));
}

function show(id) {
  document.getElementById(id).hidden = false;
}

async function init() {
  const me = await api.get('/me');
  if (me.is_rep) {
    location.replace('/rep.html');
    return;
  }
  if (!me.email_verified || !me.profile_complete) {
    const here = encodeURIComponent('/rep-apply.html');
    document.getElementById('not-ready-text').textContent = me.email_verified
      ? 'Add your phone number, university and level to your profile before applying.'
      : 'Verify your email address before applying to be a class rep.';
    const link = document.getElementById('not-ready-link');
    link.textContent = me.email_verified ? 'Complete profile' : 'Verify email';
    link.href = me.email_verified ? `/complete-profile.html?next=${here}` : `/verify-email.html?next=${here}`;
    show('not-ready');
    return;
  }

  const application = await api.get('/rep/application');
  if (application?.status === 'pending') {
    document.getElementById('pending-class').textContent = application.class.name;
    show('pending');
    return;
  }

  findUniversity = await setupUniversityPicker(document.getElementById('universities'));
  await loadBanks();

  // Pre-fill from the profile, or from the rejected application being fixed.
  form.elements.full_name.value = me.full_name;
  form.elements.phone.value = me.phone ?? '';
  form.elements.university.value = me.university?.name ?? '';
  form.elements.level.value = me.level ?? '';
  fillSessions(application?.class.session);
  if (application?.status === 'rejected') {
    const rejected = document.getElementById('rejected');
    rejected.textContent = `Your last application wasn't approved: ${application.reject_reason} Please fix this and apply again.`;
    rejected.hidden = false;
    form.elements.matric_no.value = application.matric_no ?? '';
    form.elements.university.value = application.class.school;
    form.elements.department.value = application.class.department;
    form.elements.level.value = application.class.level;
    form.elements.class_name.value = application.class.name;
  }
  show('apply-form');
}

form.elements.bank_code.addEventListener('change', scheduleLookup);
form.elements.account_number.addEventListener('input', (event) => {
  event.target.value = event.target.value.replace(/\D/g, '').slice(0, 10);
  scheduleLookup();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const f = form.elements;
  const universityId = findUniversity(f.university.value);
  const file = f.proof.files[0];
  const problems = [
    [String(f.full_name.value).trim().length < 2, 'Enter your full name.'],
    [String(f.matric_no.value).trim().length < 3, 'Enter your matric number.'],
    [!universityId, 'Choose your university from the list.'],
    [String(f.department.value).trim().length < 2, 'Enter your department.'],
    [!f.level.value, 'Choose your level.'],
    [String(f.class_name.value).trim().length < 2, 'Enter your class name.'],
    [!file, 'Upload your school ID or student email screenshot.'],
    [file && file.size > MAX_PROOF_BYTES, 'The proof file must be 5 MB or smaller.'],
    [!f.bank_code.value, 'Choose your bank.'],
    [!/^\d{10}$/.test(f.account_number.value), 'Enter your 10-digit account number.'],
    [resolvedFor !== `${f.bank_code.value}|${f.account_number.value}`, 'Wait for the account name to show, and check it is yours.'],
  ];
  const problem = problems.find(([bad]) => bad);
  if (problem) return showError(errorEl, problem[1]);

  const data = new FormData();
  for (const name of ['full_name', 'matric_no', 'phone', 'department', 'level', 'class_name', 'session', 'bank_code', 'account_number']) {
    data.append(name, String(f[name].value).trim());
  }
  data.append('university_id', universityId);
  data.append('proof', file);

  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Submitting…');
  try {
    const res = await api.post('/rep/applications', data);
    form.hidden = true;
    document.getElementById('pending-class').textContent = res.class.name;
    show('pending');
    window.scrollTo(0, 0);
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
    window.scrollTo(0, 0);
  }
});

init()
  .catch((err) => showError(errorEl, err))
  .finally(() => { document.getElementById('loading').hidden = true; });
