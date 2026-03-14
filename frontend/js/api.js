/* ── Pritis API Client ── */
const BASE_URL = "https://www.pritis.name.ng/api";

// Register service worker for PWA support
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

export function getToken() {
  return localStorage.getItem('pritis_token');
}

export function setToken(token) {
  localStorage.setItem('pritis_token', token);
}

export function clearToken() {
  localStorage.removeItem('pritis_token');
  localStorage.removeItem('pritis_user');
}

export function getUser() {
  const raw = localStorage.getItem('pritis_user');
  return raw ? JSON.parse(raw) : null;
}

export function setUser(user) {
  localStorage.setItem('pritis_user', JSON.stringify(user));
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
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, { method: 'GET', headers, signal: controller.signal });
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('Request timed out. Please try again.');
    throw err;
  } finally {
    clearTimeout(timer);
  }
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

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 300000); // 5 minutes for large AI processing
  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, { ...options, headers, signal: controller.signal });
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('The server took too long to respond. It may be starting up — please wait a moment and try again.');
    }
    throw new Error('Network error. Please check your connection.');
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 401 || res.status === 403) {
    clearToken();
    // Read the error detail before deciding what to do
    let errMsg = 'Incorrect email or password.';
    try { const e = await res.json(); if (e?.detail) errMsg = e.detail; } catch {}
    // If already on the auth page, surface the error — don't redirect to itself
    const onAuthPage = /\/(index\.html)?$/.test(window.location.pathname) ||
                       window.location.pathname.endsWith('/frontend/');
    if (onAuthPage) throw new Error(errMsg);
    const base = window.location.pathname.replace(/\/frontend\/.*$/, '/frontend/');
    window.location.href = base + 'index.html';
    return;
  }

  if (res.status === 204) return null;

  if (res.status === 502 || res.status === 503 || res.status === 504) {
    throw new Error('The server is starting up. Please wait a moment and try again.');
  }

  let data;
  try {
    data = await res.json();
  } catch {
    // Server returned non-JSON (e.g. nginx 500 page)
    if (!res.ok) throw new Error(`Something went wrong (${res.status}). Please try again.`);
    throw new Error('Unexpected response from server. Please try again.');
  }
  if (!res.ok) {
    const detail = data?.detail;
    let msg;
    if (typeof detail === 'string') {
      msg = detail;
    } else if (Array.isArray(detail)) {
      msg = detail.map(e => e.msg.replace(/^Value error,\s*/i, '')).join(' · ');
    } else {
      msg = `Error ${res.status}`;
    }
    throw new Error(msg);
  }
  return data;
}

// ── XHR-based form upload with real upload-progress tracking ─────────────────
// callbacks: { onProgress(ratio 0-1), onUploadComplete() }
function apiFetchFormWithProgress(path, form, { onProgress, onUploadComplete } = {}) {
  return new Promise((resolve, reject) => {
    const token = getToken();
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BASE_URL}${path}`);
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);

    if (onProgress) {
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      });
    }
    if (onUploadComplete) {
      xhr.upload.addEventListener('load', onUploadComplete);
    }

    xhr.addEventListener('load', () => {
      if (xhr.status === 401) {
        clearToken();
        const onAuthPage = /\/(index\.html)?$/.test(window.location.pathname) ||
                           window.location.pathname.endsWith('/frontend/');
        if (onAuthPage) { reject(new Error('Incorrect email or password.')); return; }
        const base = window.location.pathname.replace(/\/frontend\/.*$/, '/frontend/');
        window.location.href = base + 'index.html';
        resolve(undefined);
        return;
      }
      if (xhr.status === 204) { resolve(null); return; }
      let data;
      try { data = JSON.parse(xhr.responseText); } catch { data = {}; }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data);
      } else {
        const detail = data?.detail;
        let msg;
        if (typeof detail === 'string') msg = detail;
        else if (Array.isArray(detail)) msg = detail.map(e => e.msg.replace(/^Value error,\s*/i, '')).join(' · ');
        else if (xhr.status === 504)
          msg = 'The file took too long to process. Try a shorter document or try again.';
        else if (xhr.status === 502 || xhr.status === 503)
          msg = 'The server is temporarily unavailable. Please try again shortly.';
        else msg = `Error ${xhr.status}`;
        reject(new Error(msg));
      }
    });
    xhr.addEventListener('error', () => reject(new Error('Unable to reach the server. Please check your connection and try again.')));
    xhr.addEventListener('timeout', () => reject(new Error('The file took too long to process. Please try a smaller file or try again.')));
    xhr.timeout = 300000; // 5-minute timeout for large file uploads + AI processing
    xhr.send(form);
  });
}

export const api = {
  get:              (path)            => apiFetch(path, { method: 'GET' }),
  post:             (path, body)      => apiFetch(path, { method: 'POST',   body: JSON.stringify(body) }),
  put:              (path, body)      => apiFetch(path, { method: 'PUT',    body: JSON.stringify(body) }),
  patch:            (path, body)      => apiFetch(path, { method: 'PATCH',  body: JSON.stringify(body) }),
  del:              (path)            => apiFetch(path, { method: 'DELETE' }),
  postForm:         (path, form)      => apiFetch(path, { method: 'POST',   body: form }),
  postFormProgress: (path, form, cbs) => apiFetchFormWithProgress(path, form, cbs || {}),
  getBuffer:        (path)            => apiFetchBuffer(path),
};
