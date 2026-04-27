import { api, setToken, setUser, getToken } from './api.js';

// Redirect to dashboard if already logged in
if (getToken()) {
  window.location.href = 'dashboard.html';
}

// ── Google Sign-In ────────────────────────────────────────────────────────────

async function initGoogleAuth() {
  try {
    const cfg = await api.get('/auth/config');
    if (!cfg.google_client_id) return;

    await new Promise((resolve, reject) => {
      if (window.google?.accounts?.id) { resolve(); return; }
      const start = Date.now();
      const t = setInterval(() => {
        if (window.google?.accounts?.id) { clearInterval(t); resolve(); }
        else if (Date.now() - start > 6000)  { clearInterval(t); reject(); }
      }, 100);
    });

    google.accounts.id.initialize({
      client_id: cfg.google_client_id,
      callback:  window.handleGoogleCredential,
    });

    const el = document.getElementById('google-signin-btn');
    if (!el) return;

    function _renderGoogleBtn() {
      // Use the container's actual width, capped between 200-400px (GIS limits)
      const w = Math.min(400, Math.max(200, el.offsetWidth || 300));
      el.innerHTML = '';   // clear previous render before re-rendering
      google.accounts.id.renderButton(el, {
        theme: 'outline', size: 'large', type: 'standard',
        text: 'signin_with', width: w,
      });
    }

    _renderGoogleBtn();

    // Re-render on resize so the button always fits the container
    let _resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(_resizeTimer);
      _resizeTimer = setTimeout(_renderGoogleBtn, 150);
    });
  } catch { /* GIS unavailable — hide Google button */ }
}
initGoogleAuth();

window.handleGoogleCredential = async function(response) {
  try {
    const data = await api.post('/auth/google', { credential: response.credential });
    setToken(data.access_token);
    const user = await api.get('/auth/me');
    setUser(user);
    window.location.href = 'dashboard.html';
  } catch (err) {
    showAlert('login-alert', err.message || 'Google sign-in failed. Please try again.');
  }
};

// ── 2FA Modal ─────────────────────────────────────────────────────────────────

let _preAuthToken = '';

function show2FAModal(preAuthToken) {
  _preAuthToken = preAuthToken;
  document.getElementById('tfa-overlay').style.display = 'flex';
  document.getElementById('tfa-code').value = '';
  document.getElementById('tfa-alert').style.display = 'none';
  setTimeout(() => document.getElementById('tfa-code').focus(), 100);
}

window.closeTwoFA = function() {
  document.getElementById('tfa-overlay').style.display = 'none';
  _preAuthToken = '';
  setLoading('login-btn', false);
};

window.submitTwoFA = async function() {
  const code = document.getElementById('tfa-code').value.trim();
  if (code.length !== 6) {
    const al = document.getElementById('tfa-alert');
    al.textContent = 'Please enter the 6-digit code.';
    al.style.display = 'block';
    return;
  }
  const btn = document.getElementById('tfa-btn');
  btn.disabled = true;
  btn.textContent = 'Verifying…';
  document.getElementById('tfa-alert').style.display = 'none';

  try {
    const data = await api.post('/auth/2fa/verify', {
      pre_auth_token: _preAuthToken, totp_code: code,
    });
    setToken(data.access_token);
    const user = await api.get('/auth/me');
    setUser(user);
    window.location.href = 'dashboard.html';
  } catch (err) {
    const al = document.getElementById('tfa-alert');
    al.textContent = err.message || 'Invalid code. Please try again.';
    al.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Verify';
    document.getElementById('tfa-code').value = '';
    document.getElementById('tfa-code').focus();
  }
};

document.getElementById('tfa-code')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') window.submitTwoFA();
});

window.showTab = function(tab) {
  const loginPanel = document.getElementById('login-panel');
  const registerPanel = document.getElementById('register-panel');
  const loginTab = document.getElementById('tab-login');
  const registerTab = document.getElementById('tab-register');

  if (tab === 'login') {
    loginPanel.classList.remove('hidden');
    registerPanel.classList.add('hidden');
    loginTab.classList.add('active');
    registerTab.classList.remove('active');
  } else {
    loginPanel.classList.add('hidden');
    registerPanel.classList.remove('hidden');
    loginTab.classList.remove('active');
    registerTab.classList.add('active');
  }
};

function showAlert(id, message) {
  const el = document.getElementById(id);
  el.textContent = message;
  el.classList.remove('hidden');
}

function hideAlert(id) {
  document.getElementById(id).classList.add('hidden');
}

function setLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  btn.disabled = loading;
  btn.textContent = loading ? 'Please wait...' : (btnId === 'login-btn' ? 'Sign In' : 'Create Account');
}

// ── Per-field error helpers ──────────────────────────────────────────────────

function fieldError(inputId, errId, message) {
  const input = document.getElementById(inputId);
  const err   = document.getElementById(errId);
  if (input) input.classList.add('input-error');
  if (err)  { err.textContent = message; err.classList.remove('hidden'); }
}

function clearRegErrors() {
  document.querySelectorAll('#register-form .field-error').forEach(el => {
    el.textContent = '';
    el.classList.add('hidden');
  });
  document.querySelectorAll('#register-form .form-control').forEach(el => {
    el.classList.remove('input-error');
  });
  hideAlert('register-alert');
}

// Clear a field's error as soon as the user edits it
['reg-name','reg-username','reg-email','reg-password','reg-confirm-password','reg-department'].forEach(id => {
  document.getElementById(id)?.addEventListener('input', () => {
    document.getElementById(id)?.classList.remove('input-error');
    const err = document.getElementById('err-' + id);
    if (err) { err.textContent = ''; err.classList.add('hidden'); }
  });
});
['reg-university','reg-level'].forEach(id => {
  document.getElementById(id)?.addEventListener('change', () => {
    document.getElementById(id)?.classList.remove('input-error');
    const err = document.getElementById('err-' + id);
    if (err) { err.textContent = ''; err.classList.add('hidden'); }
  });
});
document.getElementById('tc-agree')?.addEventListener('change', () => {
  const err = document.getElementById('err-tc-agree');
  if (err) { err.textContent = ''; err.classList.add('hidden'); }
});

// ── Login ────────────────────────────────────────────────────────────────────

window.handleLogin = async function(e) {
  e.preventDefault();
  hideAlert('login-alert');
  setLoading('login-btn', true);

  const email    = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;

  if (!email)    { showAlert('login-alert', 'Please enter your email address.'); setLoading('login-btn', false); return; }
  if (!password) { showAlert('login-alert', 'Please enter your password.');       setLoading('login-btn', false); return; }

  try {
    const data = await api.post('/auth/login', { email, password });
    if (data.requires_2fa) {
      show2FAModal(data.pre_auth_token);
      setLoading('login-btn', false);
      return;
    }
    setToken(data.access_token);
    const user = await api.get('/auth/me');
    setUser(user);
    window.location.href = 'dashboard.html';
  } catch (err) {
    showAlert('login-alert', err.message);
    setLoading('login-btn', false);
  }
};

// ── Register ─────────────────────────────────────────────────────────────────

window.handleRegister = async function(e) {
  e.preventDefault();
  clearRegErrors();
  setLoading('register-btn', true);

  // Collect values
  const full_name       = document.getElementById('reg-name').value.trim();
  const username        = (document.getElementById('reg-username')?.value ?? '').trim().toLowerCase();
  const email           = document.getElementById('reg-email').value.trim();
  const password        = document.getElementById('reg-password').value;
  const confirmPassword = document.getElementById('reg-confirm-password')?.value ?? '';
  const department      = (document.getElementById('reg-department')?.value ?? '').trim();
  const university      = document.getElementById('reg-university')?.value ?? '';
  const level           = document.getElementById('reg-level')?.value ?? '';
  const tcAgree         = document.getElementById('tc-agree')?.checked;

  let hasErrors = false;

  // Full name
  if (!full_name) {
    fieldError('reg-name', 'err-reg-name', 'Please enter your full name.');
    hasErrors = true;
  } else if (full_name.length > 100) {
    fieldError('reg-name', 'err-reg-name', 'Full name must be 100 characters or fewer.');
    hasErrors = true;
  }

  // Username
  if (!username) {
    fieldError('reg-username', 'err-reg-username', 'Please choose a username.');
    hasErrors = true;
  } else if (username.length < 3) {
    fieldError('reg-username', 'err-reg-username', 'Username must be at least 3 characters.');
    hasErrors = true;
  } else if (username.length > 30) {
    fieldError('reg-username', 'err-reg-username', 'Username must be 30 characters or fewer.');
    hasErrors = true;
  } else if (!/^[a-z0-9_]+$/.test(username)) {
    fieldError('reg-username', 'err-reg-username', 'Letters, numbers, and underscores only — no spaces.');
    hasErrors = true;
  }

  // Email
  if (!email) {
    fieldError('reg-email', 'err-reg-email', 'Please enter your email address.');
    hasErrors = true;
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    fieldError('reg-email', 'err-reg-email', 'Please enter a valid email address.');
    hasErrors = true;
  }

  // Password
  if (!password) {
    fieldError('reg-password', 'err-reg-password', 'Please enter a password.');
    hasErrors = true;
  } else if (password.length < 8) {
    fieldError('reg-password', 'err-reg-password', 'Password must be at least 8 characters.');
    hasErrors = true;
  } else if (!/[A-Z]/.test(password)) {
    fieldError('reg-password', 'err-reg-password', 'Password must contain at least one uppercase letter (e.g. A).');
    hasErrors = true;
  } else if (!/[a-z]/.test(password)) {
    fieldError('reg-password', 'err-reg-password', 'Password must contain at least one lowercase letter (e.g. a).');
    hasErrors = true;
  } else if (!/[0-9!@#$%^&*()\-_=+\[\]{};:'",.<>?/\\|`~]/.test(password)) {
    fieldError('reg-password', 'err-reg-password', 'Password must contain at least one number or special character.');
    hasErrors = true;
  }

  // Confirm password
  if (!confirmPassword) {
    fieldError('reg-confirm-password', 'err-reg-confirm-password', 'Please confirm your password.');
    hasErrors = true;
  } else if (confirmPassword !== password) {
    fieldError('reg-confirm-password', 'err-reg-confirm-password', 'Passwords do not match.');
    hasErrors = true;
  }

  // Department
  if (!department) {
    fieldError('reg-department', 'err-reg-department', 'Please enter your course or department.');
    hasErrors = true;
  }

  // University
  if (!university || university === '— Select your institution —') {
    fieldError('reg-university', 'err-reg-university', 'Please select your university or institution.');
    hasErrors = true;
  }

  // Level
  if (!level || level === '— Select your level —') {
    fieldError('reg-level', 'err-reg-level', 'Please select your current level or year.');
    hasErrors = true;
  }

  // Terms
  if (!tcAgree) {
    fieldError('tc-agree', 'err-tc-agree', 'You must agree to the Terms & Conditions to continue.');
    hasErrors = true;
  }

  if (hasErrors) {
    // Scroll to the first visible error
    const firstErr = document.querySelector('#register-form .field-error:not(.hidden)');
    if (firstErr) firstErr.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setLoading('register-btn', false);
    return;
  }

  try {
    const data = await api.post('/auth/register', { email, password, full_name, username, department, university, level });
    setToken(data.access_token);
    const user = await api.get('/auth/me');
    setUser(user);
    window.location.href = 'dashboard.html';
  } catch (err) {
    const msg = err.message || 'Registration failed. Please try again.';
    // Route API errors to the most relevant field
    if (/email/i.test(msg)) {
      fieldError('reg-email', 'err-reg-email', msg);
      document.getElementById('reg-email')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (/username/i.test(msg)) {
      fieldError('reg-username', 'err-reg-username', msg);
      document.getElementById('reg-username')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      showAlert('register-alert', msg);
    }
    setLoading('register-btn', false);
  }
};
