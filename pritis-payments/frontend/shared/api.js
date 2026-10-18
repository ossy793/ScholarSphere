// Shared fetch wrapper for both sites. Sends the session cookies, adds the CSRF
// header, refreshes the session once on a 401, and turns error bodies into ApiError.

export class ApiError extends Error {
  constructor(status, code, message, details = []) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function readCookie(name) {
  const row = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`));
  return row ? decodeURIComponent(row.slice(name.length + 1)) : null;
}

/**
 * @param {{prefix: string, loginPage: string}} options
 *   prefix: the site's API root, e.g. '/api/user'
 *   loginPage: where to send the user when their session has ended
 */
export function createApi({ prefix, loginPage }) {
  let refreshing = null;

  async function csrfToken() {
    const existing = readCookie('csrf_token');
    if (existing) return existing;
    const res = await fetch(`${prefix}/auth/csrf`, { credentials: 'same-origin' });
    return (await res.json()).csrf_token;
  }

  async function send(method, path, body) {
    const headers = { Accept: 'application/json' };
    // FormData (file uploads) sets its own multipart Content-Type.
    const isForm = body instanceof FormData;
    if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = await csrfToken();
    try {
      return await fetch(prefix + path, {
        method,
        headers,
        credentials: 'same-origin',
        body: body === undefined || isForm ? body : JSON.stringify(body),
      });
    } catch {
      throw new ApiError(0, 'network_error', 'Could not reach the server. Check your connection and try again.');
    }
  }

  // Several requests may hit a 401 at once; they share one refresh call.
  function refresh() {
    refreshing ??= send('POST', '/auth/refresh')
      .then((res) => res.ok)
      .catch(() => false)
      .finally(() => { refreshing = null; });
    return refreshing;
  }

  function redirectToLogin() {
    const next = location.pathname + location.search;
    location.href = `${loginPage}?next=${encodeURIComponent(next)}`;
  }

  /**
   * @param {object} [opts]
   * @param {boolean} [opts.redirectOn401=true] send the user to the login page if the session has ended
   */
  async function request(method, path, body, { redirectOn401 = true } = {}) {
    const isAuthCall = path.startsWith('/auth/') && path !== '/auth/verify-email' && path !== '/auth/resend-code';
    let res = await send(method, path, body);

    if (res.status === 401 && !isAuthCall && (await refresh())) {
      res = await send(method, path, body);
    }
    if (res.status === 401 && !isAuthCall && redirectOn401) {
      redirectToLogin();
      throw new ApiError(401, 'not_authenticated', 'Please log in');
    }

    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) {
      const err = data?.error ?? {};
      throw new ApiError(
        res.status,
        err.code ?? 'unknown_error',
        err.message ?? 'Something went wrong. Please try again.',
        err.details ?? [],
      );
    }
    return data;
  }

  return {
    get: (path, opts) => request('GET', path, undefined, opts),
    post: (path, body, opts) => request('POST', path, body, opts),
    put: (path, body, opts) => request('PUT', path, body, opts),
    del: (path, opts) => request('DELETE', path, undefined, opts),
    redirectToLogin,
  };
}
