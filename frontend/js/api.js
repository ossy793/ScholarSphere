/* ── Pritis API Client ── */
const _isLocal = ['localhost', '127.0.0.1', '0.0.0.0'].includes(window.location.hostname);
const BASE_URL = _isLocal ? 'http://127.0.0.1:8000/api' : 'https://www.pritis.name.ng/api';

// Register service worker for PWA support
if ('serviceWorker' in navigator) {
  // Resolve path relative to this script so it works on both Live Server (/frontend/)
  // and production (served from root). sw.js lives in the same folder as index.html.
  const _swPath = new URL('../sw.js', import.meta.url).pathname;
  navigator.serviceWorker.register(_swPath).catch(err => console.warn('SW registration failed:', err));
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

/**
 * Like requireAuth(), but also blocks access when the user's profile is
 * incomplete. Call this instead of requireAuth() on every protected page.
 * Returns false (and redirects) when the user should not proceed.
 */
export function requireCompleteProfile() {
  if (!requireAuth()) return false;
  const user = getUser();
  // profile_completed may be missing on old cached tokens — treat as false
  if (!user?.profile_completed) {
    const current = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `settings.html?redirect=${current}&setup=1`;
    return false;
  }
  return true;
}

// ── Plan helpers ──────────────────────────────────────────────────────────────

export function getUserPlan() {
  return getUser()?.subscription_plan || 'free';
}

/** Returns true if user's plan is at least the given tier. */
export function hasPlan(minPlan) {
  const order = { free: 0, basic: 1, pro: 2, max: 2 };
  const user  = getUser();
  if (user?.is_admin) return true;
  return (order[getUserPlan()] || 0) >= (order[minPlan] || 0);
}

/** Redirect to upgrade page if user is below the required plan. */
export function requirePlan(minPlan = 'basic') {
  if (!getToken()) { window.location.href = 'index.html'; return false; }
  if (!hasPlan(minPlan)) { window.location.href = 'upgrade.html'; return false; }
  return true;
}

/** Legacy alias — kept so old call sites still compile. */
export function requirePremium() {
  return requirePlan('basic');
}

// ── Upgrade Required modal ────────────────────────────────────────────────────

export function showUpgradeModal(info = {}) {
  document.getElementById('_upgrade-modal')?.remove();

  const feature  = (info.feature  || '').replace(/_/g, ' ');
  const required = (info.required_plan || 'basic');
  const message  = info.message || `Upgrade to ${required.charAt(0).toUpperCase() + required.slice(1)} to unlock this feature.`;
  const usageLine = (info.limit != null)
    ? `<p style="font-size:.8rem;color:#6b7280;margin:0 0 16px">
         Used <strong>${info.current_usage}/${info.limit}</strong> of your ${feature} allowance.
       </p>`
    : '';

  const modal = document.createElement('div');
  modal.id    = '_upgrade-modal';
  modal.style.cssText = `
    position:fixed;inset:0;background:rgba(0,0,0,.6);backdrop-filter:blur(4px);
    z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;
  `;
  modal.innerHTML = `
    <div style="background:#fff;border-radius:18px;padding:32px 28px;max-width:400px;
                width:100%;box-shadow:0 20px 60px rgba(0,0,0,.3);text-align:center">
      <div style="font-size:2.5rem;margin-bottom:12px">🔒</div>
      <h2 style="font-size:1.1rem;font-weight:800;color:#111827;margin:0 0 8px">
        Feature Locked
      </h2>
      <p style="font-size:.88rem;color:#6b7280;line-height:1.6;margin:0 0 12px">
        ${message}
      </p>
      ${usageLine}
      <a href="upgrade.html"
         style="display:block;background:#6366f1;color:#fff;padding:13px;border-radius:10px;
                font-weight:700;font-size:.9rem;text-decoration:none;margin-bottom:10px">
        View Plans &amp; Upgrade →
      </a>
      <button onclick="document.getElementById('_upgrade-modal').remove()"
              style="background:none;border:1px solid #e5e7eb;color:#6b7280;
                     padding:11px;border-radius:10px;font-weight:600;font-size:.88rem;
                     cursor:pointer;width:100%">
        Maybe Later
      </button>
    </div>
  `;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
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

  // 402 Payment Required — feature access denied (upgrade needed)
  if (res.status === 402) {
    let data;
    try { data = await res.json(); } catch {}
    const detail = data?.detail || {};
    const err    = new Error(detail.message || 'Upgrade required to use this feature.');
    err.upgradeRequired = true;
    err.upgradeInfo     = typeof detail === 'object' ? detail : { message: String(detail) };
    throw err;
  }

  if (res.status === 401 || res.status === 403) {
    let errData;
    try { errData = await res.json(); } catch {}
    const detail = errData?.detail || '';
    // Feature-access 403 (not auth) — surface as error without logging out
    if (res.status === 403 && typeof detail === 'string' &&
        !detail.toLowerCase().includes('deactivated') &&
        !detail.toLowerCase().includes('admin')) {
      throw new Error(detail || 'Access denied.');
    }
    clearToken();
    let errMsg = 'Incorrect email or password.';
    if (typeof detail === 'string' && detail) errMsg = detail;
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
    } else if (detail && typeof detail === 'object' && detail.message) {
      msg = detail.message;
    } else {
      msg = `Something went wrong (${res.status}). Please try again.`;
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
      if (xhr.status === 402) {
        const detail = data?.detail || {};
        const err = new Error(detail.message || 'Upgrade required to use this feature.');
        err.upgradeRequired = true;
        err.upgradeInfo = typeof detail === 'object' ? detail : { message: String(detail) };
        reject(err); return;
      }
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

async function apiFetchBlob(path, form) {
  const token = getToken();
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 300000);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      method: 'POST', headers, body: form, signal: controller.signal,
    });
    if (!res.ok) {
      let msg = `Error ${res.status}`;
      try { const d = await res.json(); if (d?.detail) msg = typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail); } catch {}
      throw new Error(msg);
    }
    return res.blob();
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('Request timed out. Please try again.');
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  get:              (path)            => apiFetch(path, { method: 'GET' }),
  post:             (path, body)      => apiFetch(path, { method: 'POST',   body: JSON.stringify(body) }),
  put:              (path, body)      => apiFetch(path, { method: 'PUT',    body: JSON.stringify(body) }),
  patch:            (path, body)      => apiFetch(path, { method: 'PATCH',  body: JSON.stringify(body) }),
  del:              (path)            => apiFetch(path, { method: 'DELETE' }),
  postForm:         (path, form)      => apiFetch(path, { method: 'POST',   body: form }),
  postFormProgress: (path, form, cbs) => apiFetchFormWithProgress(path, form, cbs || {}),
  postFormBlob:     (path, form)      => apiFetchBlob(path, form),
  getBuffer:        (path)            => apiFetchBuffer(path),

  /** POST and return the raw Response so the caller can read it as a stream. */
  stream: async (path, body) => {
    const token = getToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 300000);
    try {
      const res = await fetch(`${BASE_URL}${path}`, {
        method: 'POST', headers, body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (res.status === 401) {
        clearToken();
        const base = window.location.pathname.replace(/\/frontend\/.*$/, '/frontend/');
        window.location.href = base + 'index.html';
        return null;
      }
      return res;
    } catch (err) {
      if (err.name === 'AbortError') throw new Error('Request timed out. Please try again.');
      throw new Error('Network error. Please check your connection.');
    } finally {
      clearTimeout(timer);
    }
  },
};
