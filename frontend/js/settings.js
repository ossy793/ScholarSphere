import { requireAuth, api, getUser, setUser } from './api.js';
import { renderLayout } from './layout.js';

if (!requireAuth()) throw new Error('Not authenticated');
renderLayout('Settings', 'Settings');

// ── Schools list ───────────────────────────────────────────────────────────────
const SCHOOLS = [
  // Federal Universities
  "Ahmadu Bello University (ABU), Zaria",
  "Bayero University, Kano (BUK)",
  "Federal University of Agriculture, Abeokuta (FUNAAB)",
  "Federal University of Petroleum Resources, Effurun (FUPRE)",
  "Federal University of Technology, Akure (FUTA)",
  "Federal University of Technology, Minna (FUTMinna)",
  "Federal University of Technology, Owerri (FUTO)",
  "Federal University, Birnin Kebbi",
  "Federal University, Dutse",
  "Federal University, Dutsin-Ma",
  "Federal University, Gashua",
  "Federal University, Gusau",
  "Federal University, Kashere",
  "Federal University, Lafia",
  "Federal University, Lokoja",
  "Federal University, Ndifu-Alike (FUNAI)",
  "Federal University, Otuoke",
  "Federal University, Oye-Ekiti (FUOYE)",
  "Federal University, Wukari",
  "Michael Okpara University of Agriculture, Umudike",
  "Modibbo Adama University of Technology, Yola",
  "National Open University of Nigeria (NOUN)",
  "Nigerian Defence Academy, Kaduna",
  "Nnamdi Azikiwe University, Awka (NAU)",
  "Obafemi Awolowo University, Ile-Ife (OAU)",
  "University of Abuja",
  "University of Agriculture, Makurdi (UAM)",
  "University of Benin (UNIBEN)",
  "University of Calabar (UNICAL)",
  "University of Ibadan (UI)",
  "University of Ilorin (UNILORIN)",
  "University of Jos (UNIJOS)",
  "University of Lagos (UNILAG)",
  "University of Maiduguri (UNIMAID)",
  "University of Nigeria, Nsukka (UNN)",
  "University of Port Harcourt (UNIPORT)",
  "Usman Danfodiyo University, Sokoto (UDUS)",
  // State Universities
  "Abia State University, Uturu (ABSU)",
  "Adekunle Ajasin University, Akungba-Akoko (AAUA)",
  "Akwa Ibom State University (AKSU)",
  "Ambrose Alli University, Ekpoma (AAU)",
  "Anambra State University (ANSU)",
  "Benue State University, Makurdi",
  "Cross River University of Technology (CRUTECH)",
  "Delta State University, Abraka (DELSU)",
  "Ebonyi State University (EBSU)",
  "Ekiti State University (EKSU)",
  "Enugu State University of Science and Technology (ESUT)",
  "Gombe State University",
  "Ibrahim Badamasi Babangida University (IBBU)",
  "Imo State University, Owerri (IMSU)",
  "Kaduna State University (KASU)",
  "Kano University of Science and Technology, Wudil (KUST)",
  "Kebbi State University of Science and Technology (KSUSTA)",
  "Kogi State University (KSU)",
  "Kwara State University (KWASU)",
  "Lagos State University (LASU)",
  "Nasarawa State University, Keffi (NSUK)",
  "Niger Delta University (NDU)",
  "Olabisi Onabanjo University (OOU)",
  "Ondo State University of Science and Technology (OSUSTECH)",
  "Osun State University (UNIOSUN)",
  "Oyo State Technical University",
  "Paul University, Awka",
  "Plateau State University, Bokkos",
  "Rivers State University (RSU)",
  "Sokoto State University (SSU)",
  "Tai Solarin University of Education (TASUED)",
  "Taraba State University (TSU)",
  "Umaru Musa Yar'adua University (UMYU)",
  "Yobe State University (YSU)",
  "Zamfara State University",
  // Private Universities
  "African University of Science and Technology (AUST)",
  "Achievers University, Owo",
  "Afe Babalola University, Ado-Ekiti (ABUAD)",
  "Al-Qalam University, Katsina",
  "American University of Nigeria (AUN)",
  "Augustine University, Ilara-Epe",
  "Babcock University, Ilishan-Remo",
  "Bells University of Technology, Ota",
  "Benson Idahosa University (BIU)",
  "Bowen University, Iwo",
  "Caritas University, Enugu",
  "Chrisland University",
  "Covenant University, Ota",
  "Crawford University, Igbesa",
  "Dominican University, Ibadan",
  "Edwin Clark University, Kiagbodo",
  "Elizade University, Ilara-Mokin",
  "Evangel University, Akaeze",
  "Fountain University, Oshogbo",
  "Hallmark University, Ijebu-Itele",
  "Joseph Ayo Babalola University (JABU)",
  "Landmark University, Omu-Aran",
  "Lead City University, Ibadan",
  "Madonna University, Okija",
  "McPherson University, Seriki",
  "Novena University, Ogume",
  "Obong University, Obong Ntak",
  "Oduduwa University, Ipetumodu",
  "Pan-Atlantic University, Lagos",
  "Paul University, Awka",
  "Redeemer's University, Ede",
  "Renaissance University, Enugu",
  "Rhema University, Obeama-Asa",
  "Salem University, Lokoja",
  "Samuel Adegboyega University (SAU)",
  "Southwestern University, Oku-Owa",
  "Summit University, Offa",
  "Tansian University, Umunya",
  "Thomas Adewumi University (TAU)",
  "Veritas University, Abuja",
  "Wesley University, Ondo",
  "Western Delta University, Oghara",
  "Wellspring University, Benin City",
  // Polytechnics
  "Federal Polytechnic, Ado-Ekiti",
  "Federal Polytechnic, Bida",
  "Federal Polytechnic, Idah",
  "Federal Polytechnic, Ilaro",
  "Federal Polytechnic, Mubi",
  "Federal Polytechnic, Nasarawa",
  "Federal Polytechnic, Nekede",
  "Federal Polytechnic, Offa",
  "Federal Polytechnic, Oko",
  "Kaduna Polytechnic",
  "Lagos State Polytechnic (LASPOTECH)",
  "Moshood Abiola Polytechnic (MAPOLY)",
  "Rufus Giwa Polytechnic",
  "The Polytechnic, Ibadan",
  "Yaba College of Technology (YABATECH)",
  // International / Other
  "Other (not listed)",
];

// ── Populate school dropdown ──────────────────────────────────────────────────
const schoolSelect = document.getElementById('f-school');
SCHOOLS.forEach(s => {
  const opt = document.createElement('option');
  opt.value = s;
  opt.textContent = s;
  schoolSelect.appendChild(opt);
});

// ── Required fields config ────────────────────────────────────────────────────
const REQUIRED = ['fullname', 'username', 'school', 'level', 'course'];

// ── State ─────────────────────────────────────────────────────────────────────
let _currentUser    = null;
let _pendingAvatar  = null;   // base64 data URL of newly selected image
let _removeAvatar   = false;  // user clicked "Remove Photo"
let _usernameTimer  = null;
let _usernameOk     = false;
let _saving         = false;

// ── Init ──────────────────────────────────────────────────────────────────────
(async () => {
  try {
    _currentUser = await api.get('/users/me');
    setUser(_currentUser); // keep localStorage fresh
    _populate();
    _updateProgress();
    _initTFA();
  } catch (e) {
    showToast('Failed to load profile: ' + e.message, 'error');
  }
})();

// ── Populate form from server data ────────────────────────────────────────────
function _populate() {
  const u = _currentUser;

  // Avatar
  if (u.profile_picture_url) {
    _showAvatar(u.profile_picture_url);
  } else {
    document.getElementById('avatar-initials').textContent = _initials(u.full_name || u.email);
  }

  // Fields
  document.getElementById('f-fullname').value = u.full_name  || '';
  document.getElementById('f-username').value = u.username   || '';
  document.getElementById('f-level').value    = u.level      || '';
  document.getElementById('f-course').value   = u.course     || '';

  // School dropdown
  if (u.school) {
    const existing = Array.from(schoolSelect.options).find(o => o.value === u.school);
    if (existing) {
      schoolSelect.value = u.school;
    } else {
      // School not in list — add it as a custom option
      const opt = document.createElement('option');
      opt.value = u.school; opt.textContent = u.school;
      schoolSelect.appendChild(opt);
      schoolSelect.value = u.school;
    }
  }

  // Username pre-validated (it's already saved)
  _usernameOk = !!u.username;

  // Account info
  document.getElementById('acct-email').textContent = u.email;
  document.getElementById('acct-type').textContent  = u.is_premium ? '⭐ Premium' : 'Free';
  document.getElementById('acct-since').textContent = new Date(u.created_at)
    .toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' });

  // Setup banner
  if (!u.profile_completed) {
    document.getElementById('setup-banner').classList.add('visible');
  }

  onFieldChange();
}

// ── Progress calculation ──────────────────────────────────────────────────────
function _updateProgress() {
  const fields = {
    fullname: document.getElementById('f-fullname').value.trim(),
    username: document.getElementById('f-username').value.trim(),
    school:   document.getElementById('f-school').value,
    level:    document.getElementById('f-level').value,
    course:   document.getElementById('f-course').value.trim(),
  };
  const filled = REQUIRED.filter(k => fields[k]).length;
  const pct    = Math.round((filled / REQUIRED.length) * 100);

  document.getElementById('profile-progress-bar').style.width = pct + '%';
  document.getElementById('profile-progress-status').textContent =
    `${filled} of ${REQUIRED.length} required fields completed`;

  const badge = document.getElementById('profile-badge');
  if (pct === 100) {
    badge.innerHTML = '<span class="completed-badge">✓ Complete</span>';
    document.getElementById('setup-banner').classList.remove('visible');
  } else {
    badge.innerHTML = `<span class="incomplete-badge">${pct}% complete</span>`;
  }

  // Save button enabled only when all required + username ok
  const allFilled = filled === REQUIRED.length;
  const saveOk    = allFilled && (_usernameOk || fields.username === (_currentUser?.username || ''));
  document.getElementById('save-btn').disabled = !saveOk;
}

window.onFieldChange = function () { _updateProgress(); };

// ── Username live check ───────────────────────────────────────────────────────
window.onUsernameInput = function () {
  clearTimeout(_usernameTimer);
  _usernameOk = false;
  const val = document.getElementById('f-username').value.trim().toLowerCase();
  const err = document.getElementById('err-username');

  if (!val) { err.textContent = ''; _updateProgress(); return; }
  if (val.length < 3) { err.textContent = 'Minimum 3 characters'; _updateProgress(); return; }
  if (!/^[a-z0-9_]+$/.test(val)) {
    err.textContent = 'Only letters, numbers, underscores';
    _updateProgress(); return;
  }

  // If unchanged from saved value — no need to check
  if (val === _currentUser?.username) {
    _usernameOk = true;
    err.className = 'field-ok';
    err.textContent = '✓ Your current username';
    _updateProgress(); return;
  }

  err.className = 'field-error';
  err.textContent = 'Checking…';
  _usernameTimer = setTimeout(async () => {
    // We rely on the server to report conflicts; optimistic UI here
    _usernameOk = true;
    err.className = 'field-ok';
    err.textContent = '✓ Looks good';
    _updateProgress();
  }, 500);
};

// ── Avatar handling ───────────────────────────────────────────────────────────
window.onAvatarSelected = async function (e) {
  const file = e.target.files?.[0];
  if (!file) return;

  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    showToast('Please upload a JPG, PNG, or WebP image.', 'error'); return;
  }
  if (file.size > 2 * 1024 * 1024) {
    showToast('Image must be under 2 MB.', 'error'); return;
  }

  try {
    const compressed = await _compressImage(file, 256, 0.82);
    _pendingAvatar  = compressed;
    _removeAvatar   = false;
    _showAvatar(compressed);
  } catch {
    showToast('Could not process image. Please try another.', 'error');
  }
  e.target.value = ''; // reset so same file can be re-selected
};

window.removeAvatar = function () {
  _pendingAvatar = null;
  _removeAvatar  = true;
  const preview = document.getElementById('avatar-preview');
  preview.innerHTML = `<span id="avatar-initials">${_initials(_currentUser?.full_name || '?')}</span>`;
  document.getElementById('avatar-remove-btn').style.display = 'none';
};

function _showAvatar(src) {
  const preview = document.getElementById('avatar-preview');
  preview.innerHTML = `<img src="${src}" alt="avatar">`;
  document.getElementById('avatar-remove-btn').style.display = '';
}

async function _compressImage(file, size, quality) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const s      = Math.min(size / img.width, size / img.height, 1);
      const canvas = document.createElement('canvas');
      canvas.width  = Math.round(img.width  * s);
      canvas.height = Math.round(img.height * s);
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL('image/jpeg', quality));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(); };
    img.src = url;
  });
}

// ── Save profile ──────────────────────────────────────────────────────────────
window.saveProfile = async function () {
  if (_saving) return;
  _saving = true;
  const btn    = document.getElementById('save-btn');
  const status = document.getElementById('save-status');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  status.textContent = '';

  const payload = {
    full_name: document.getElementById('f-fullname').value.trim(),
    username:  document.getElementById('f-username').value.trim().toLowerCase(),
    school:    document.getElementById('f-school').value,
    level:     document.getElementById('f-level').value,
    course:    document.getElementById('f-course').value.trim(),
  };

  if (_pendingAvatar) {
    payload.profile_picture_url = _pendingAvatar;
  } else if (_removeAvatar) {
    payload.profile_picture_url = '';
  }

  // Clear field errors
  ['fullname','username','school','level','course'].forEach(k => {
    const el = document.getElementById(`err-${k}`);
    if (el) { el.textContent = ''; el.className = 'field-error'; }
  });

  try {
    const updated = await api.patch('/users/me', payload);
    _currentUser   = updated;
    _pendingAvatar = null;
    _removeAvatar  = false;
    _usernameOk    = true;
    setUser(updated);
    _updateProgress();
    showToast('Profile saved successfully! ✓', 'success');

    // If just completed, remove the banner and redirect after a moment
    if (updated.profile_completed) {
      document.getElementById('setup-banner').classList.remove('visible');
      const params = new URLSearchParams(window.location.search);
      const redirect = params.get('redirect');
      if (redirect) {
        setTimeout(() => { window.location.href = redirect; }, 1200);
      }
    }
  } catch (err) {
    // Surface field-level errors if possible
    const msg = err.message || 'Save failed. Please try again.';
    if (msg.toLowerCase().includes('username')) {
      document.getElementById('err-username').textContent = msg;
      _usernameOk = false;
    } else {
      showToast(msg, 'error');
    }
  } finally {
    _saving = false;
    btn.textContent = 'Save Profile';
    _updateProgress();
  }
};

// ── Toast ─────────────────────────────────────────────────────────────────────
window.showToast = function (msg, type = '') {
  const el = document.getElementById('settings-toast');
  el.textContent = msg;
  el.className = `settings-toast visible ${type}`;
  setTimeout(() => { el.className = 'settings-toast'; }, 3500);
};

// ── Two-Factor Authentication ─────────────────────────────────────────────────

function _renderTFAStatus(enabled) {
  const statusText = document.getElementById('tfa-status-text');
  const badge      = document.getElementById('tfa-badge');
  const disabledUI = document.getElementById('tfa-disabled-ui');
  const enabledUI  = document.getElementById('tfa-enabled-ui');
  if (!statusText) return;

  if (enabled) {
    statusText.textContent = 'Two-factor authentication is on';
    badge.textContent = 'ENABLED';
    badge.style.cssText = 'font-size:.72rem;font-weight:700;padding:4px 10px;border-radius:999px;background:#dcfce7;color:#16a34a';
    disabledUI.style.display = 'none';
    enabledUI.style.display  = '';
  } else {
    statusText.textContent = 'Two-factor authentication is off';
    badge.textContent = 'DISABLED';
    badge.style.cssText = 'font-size:.72rem;font-weight:700;padding:4px 10px;border-radius:999px;background:#f3f4f6;color:#6b7280';
    disabledUI.style.display = '';
    enabledUI.style.display  = 'none';
  }
}

// Initialise 2FA status from the already-loaded user
function _initTFA() {
  _renderTFAStatus(!!(_currentUser?.totp_enabled));
}

let _tfaSecret = '';

window.startTwoFASetup = async function() {
  try {
    const data = await api.post('/auth/2fa/setup');
    _tfaSecret = data.secret;

    document.getElementById('tfa-secret-text').textContent = data.secret.match(/.{1,4}/g).join(' ');
    document.getElementById('tfa-setup-code').value = '';
    document.getElementById('tfa-setup-alert').style.display = 'none';
    document.getElementById('tfa-step1').style.display = '';
    document.getElementById('tfa-step2').style.display = 'none';
    document.getElementById('tfa-setup-overlay').style.display = 'flex';

    const qrEl = document.getElementById('tfa-qr');
    qrEl.innerHTML = '';
    if (window.QRCode) {
      new QRCode(qrEl, { text: data.uri, width: 180, height: 180 });
    }
  } catch (err) {
    showToast(err.message || 'Could not start 2FA setup.', 'error');
  }
};

window.closeTFASetup = function() {
  document.getElementById('tfa-setup-overlay').style.display = 'none';
  _tfaSecret = '';
};

window.confirmTwoFASetup = async function() {
  const code = document.getElementById('tfa-setup-code').value.trim();
  if (code.length !== 6) {
    const al = document.getElementById('tfa-setup-alert');
    al.textContent = 'Please enter the 6-digit code from your app.';
    al.style.display = 'block';
    return;
  }
  const btn = document.getElementById('tfa-confirm-btn');
  btn.disabled = true; btn.textContent = 'Verifying…';
  document.getElementById('tfa-setup-alert').style.display = 'none';

  try {
    await api.post('/auth/2fa/enable', { totp_code: code, secret: _tfaSecret });
    document.getElementById('tfa-step1').style.display = 'none';
    document.getElementById('tfa-step2').style.display = '';
    _renderTFAStatus(true);
    if (_currentUser) _currentUser.totp_enabled = true;
  } catch (err) {
    const al = document.getElementById('tfa-setup-alert');
    al.textContent = err.message || 'Invalid code. Please try again.';
    al.style.display = 'block';
    document.getElementById('tfa-setup-code').value = '';
  } finally {
    btn.disabled = false; btn.textContent = 'Confirm & Enable';
  }
};

window.showDisableTwoFA = function() {
  document.getElementById('tfa-disable-code').value = '';
  document.getElementById('tfa-disable-alert').style.display = 'none';
  document.getElementById('tfa-disable-overlay').style.display = 'flex';
};

window.confirmDisableTwoFA = async function() {
  const code = document.getElementById('tfa-disable-code').value.trim();
  if (code.length !== 6) {
    const al = document.getElementById('tfa-disable-alert');
    al.textContent = 'Please enter the 6-digit code.';
    al.style.display = 'block';
    return;
  }
  const btn = document.getElementById('tfa-disable-btn');
  btn.disabled = true; btn.textContent = 'Disabling…';
  document.getElementById('tfa-disable-alert').style.display = 'none';

  try {
    await api.post('/auth/2fa/disable', { totp_code: code });
    document.getElementById('tfa-disable-overlay').style.display = 'none';
    _renderTFAStatus(false);
    if (_currentUser) _currentUser.totp_enabled = false;
    showToast('Two-factor authentication disabled.', 'success');
  } catch (err) {
    const al = document.getElementById('tfa-disable-alert');
    al.textContent = err.message || 'Invalid code. Please try again.';
    al.style.display = 'block';
    document.getElementById('tfa-disable-code').value = '';
  } finally {
    btn.disabled = false; btn.textContent = 'Disable 2FA';
  }
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function _initials(name) {
  return (name || '?').split(' ').filter(n => n).map(n => n[0]).join('').toUpperCase().slice(0, 2) || '?';
}
