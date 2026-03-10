import { api, getUser, getToken, setToken, setUser } from './api.js';

// If already premium, skip straight to dashboard
if (getToken() && getUser()?.is_premium) {
  window.location.href = 'dashboard.html';
}

// Pre-fill email if the user is logged in
const loggedInUser = getUser();
if (loggedInUser?.email) {
  document.getElementById('req-email').value = loggedInUser.email;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function showAlert(msg, type = 'error') {
  const el = document.getElementById('global-alert');
  el.textContent = msg;
  el.className   = `alert alert-${type}`;
  el.classList.remove('hidden');
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideAlert() {
  document.getElementById('global-alert').classList.add('hidden');
}

function setLoading(btnId, loading, defaultText) {
  const btn = document.getElementById(btnId);
  btn.disabled  = loading;
  btn.textContent = loading ? 'Please wait…' : defaultText;
}

function showStep(step) {
  ['step-request', 'step-verify', 'step-success'].forEach(id => {
    document.getElementById(id).classList.add('hidden');
  });
  document.getElementById(step).classList.remove('hidden');
  hideAlert();
}

// ── Step 1: Request code ──────────────────────────────────────────────────────

window.requestCode = async function () {
  hideAlert();
  const email = document.getElementById('req-email').value.trim();

  if (!email) {
    showAlert('Please enter your Gmail address.');
    return;
  }
  if (!email.endsWith('@gmail.com')) {
    showAlert('Only Gmail addresses (@gmail.com) are supported.');
    return;
  }

  setLoading('req-btn', true, 'Send My Code');
  try {
    const data = await api.post('/promo/request', { email });
    // Move to step 2
    document.getElementById('ver-email').value = email;
    showStep('step-verify');
    showAlert(data.message || 'Code sent! Check your inbox.', 'success');
  } catch (err) {
    showAlert(err.message);
  } finally {
    setLoading('req-btn', false, 'Send My Code');
  }
};

// ── Step 2: Verify code ───────────────────────────────────────────────────────

window.verifyCode = async function () {
  hideAlert();
  const email = document.getElementById('ver-email').value.trim();
  const code  = document.getElementById('ver-code').value.trim();

  if (!code) {
    showAlert('Please enter the code from your email.');
    return;
  }

  setLoading('ver-btn', true, 'Activate Premium');
  try {
    const data = await api.post('/promo/verify', { email, code });

    // Update token first
    if (data.access_token) setToken(data.access_token);

    // Fetch fresh user and update localStorage with is_premium: true
    const freshUser = await api.get('/auth/me');
    if (freshUser) {
      setUser(freshUser);
    } else {
      // Fallback: patch existing user object directly
      const existing = getUser();
      if (existing) setUser({ ...existing, is_premium: true });
    }

    // Show WhatsApp link
    const waLink = document.getElementById('whatsapp-link');
    if (data.whatsapp_link) {
      waLink.href = data.whatsapp_link;
    } else {
      waLink.style.display = 'none';
    }

    showStep('step-success');

    // Auto-redirect to dashboard after 3 seconds — no logout needed
    setTimeout(() => { window.location.href = 'dashboard.html'; }, 3000);
  } catch (err) {
    showAlert(err.message);
  } finally {
    setLoading('ver-btn', false, 'Activate Premium');
  }
};

// ── Back to step 1 ────────────────────────────────────────────────────────────

window.backToRequest = function () {
  document.getElementById('ver-code').value = '';
  showStep('step-request');
};
