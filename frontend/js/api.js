/* ── Pistis API Client ── */
const BASE_URL = "https://scholarsphere-n277.onrender.com/api";

export function getToken() {
  return localStorage.getItem('pistis_token');
}

export function setToken(token) {
  localStorage.setItem('pistis_token', token);
}

export function clearToken() {
  localStorage.removeItem('pistis_token');
  localStorage.removeItem('pistis_user');
}

export function getUser() {
  const raw = localStorage.getItem('pistis_user');
  return raw ? JSON.parse(raw) : null;
}

export function setUser(user) {
  localStorage.setItem('pistis_user', JSON.stringify(user));
}

export function requireAuth() {
  if (!getToken()) {
    window.location.href = 'index.html';
    return false;
  }
  return true;
}

export function requirePremium() {
  if (!getToken()) {
    window.location.href = 'index.html';
    return false;
  }
  if (!getUser()?.is_premium) {
    window.location.href = 'upgrade.html';
    return false;
  }
  return true;
}

async function apiFetchBuffer(path) {
  const token = getToken();
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${BASE_URL}${path}`, { method: 'GET', headers });
  if (res.status === 401) {
    clearToken();
    const base = window.location.pathname.replace(/\/frontend\/.*$/, '/frontend/');
    window.location.href = base + 'index.html';
    return null;
  }
  if (!res.ok) return null;
  return res.arrayBuffer();
}

async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });

  if (res.status === 401) {
    clearToken();
    // Works from any subfolder (e.g. frontend/admin/) by finding the frontend root
    const base = window.location.pathname.replace(/\/frontend\/.*$/, '/frontend/');
    window.location.href = base + 'index.html';
    return;
  }

  if (res.status === 204) return null;

  const data = await res.json();
  if (!res.ok) {
    const detail = data?.detail;
    let msg;
    if (typeof detail === 'string') {
      msg = detail;
    } else if (Array.isArray(detail)) {
      // Pydantic validation errors — extract the human-readable msg from each
      msg = detail.map(e => e.msg.replace(/^Value error,\s*/i, '')).join(' · ');
    } else {
      msg = `Error ${res.status}`;
    }
    throw new Error(msg);
  }
  return data;
}

export const api = {
  get:      (path)         => apiFetch(path, { method: 'GET' }),
  post:     (path, body)   => apiFetch(path, { method: 'POST',   body: JSON.stringify(body) }),
  put:      (path, body)   => apiFetch(path, { method: 'PUT',    body: JSON.stringify(body) }),
  patch:    (path, body)   => apiFetch(path, { method: 'PATCH',  body: JSON.stringify(body) }),
  del:      (path)         => apiFetch(path, { method: 'DELETE' }),
  postForm:  (path, form)  => apiFetch(path, { method: 'POST',   body: form }),
  getBuffer: (path)        => apiFetchBuffer(path),
};
