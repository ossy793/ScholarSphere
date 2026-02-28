import { api, setToken, setUser, getToken, getUser } from './api.js';

// Redirect to dashboard if already logged in
if (getToken()) {
  window.location.href = 'dashboard.html';
}

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

window.handleLogin = async function(e) {
  e.preventDefault();
  hideAlert('login-alert');
  setLoading('login-btn', true);

  const email = document.getElementById('login-email').value;
  const password = document.getElementById('login-password').value;

  try {
    const data = await api.post('/auth/login', { email, password });
    setToken(data.access_token);
    const user = await api.get('/auth/me');
    setUser(user);
    window.location.href = 'dashboard.html';
  } catch (err) {
    showAlert('login-alert', err.message);
    setLoading('login-btn', false);
  }
};

window.handleRegister = async function(e) {
  e.preventDefault();
  hideAlert('register-alert');
  setLoading('register-btn', true);

  const full_name   = document.getElementById('reg-name').value;
  const email       = document.getElementById('reg-email').value;
  const password    = document.getElementById('reg-password').value;
  const department  = document.getElementById('reg-department')?.value.trim() || null;
  const university  = document.getElementById('reg-university')?.value.trim() || null;
  const level       = document.getElementById('reg-level')?.value.trim() || null;

  try {
    const data = await api.post('/auth/register', { email, password, full_name, department, university, level });
    setToken(data.access_token);
    const user = await api.get('/auth/me');
    setUser(user);
    window.location.href = 'dashboard.html';
  } catch (err) {
    showAlert('register-alert', err.message);
    setLoading('register-btn', false);
  }
};
