// Small DOM helpers. User text always goes in with textContent, never innerHTML.

/** A same-site path from ?next=, or null if missing or unsafe. */
export function safeNext(value) {
  if (value && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\')) {
    return value;
  }
  return null;
}

/** Shows an error (ApiError or string) in an alert element, with field details if any. */
export function showError(el, err) {
  if (!err) {
    el.hidden = true;
    el.textContent = '';
    return;
  }
  const message = typeof err === 'string' ? err : err.message;
  const fields = (err.details ?? [])
    .filter((d) => d.field && d.message)
    .map((d) => `${d.field.replace(/^body\./, '').replace(/_/g, ' ')}: ${d.message}`);
  el.textContent = fields.length ? `${message}. ${fields.join('. ')}` : message;
  el.hidden = false;
}

/** Disables a button and swaps its label while work is in progress. */
export function setBusy(button, busy, busyLabel = 'Please wait…') {
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyLabel;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.label ?? button.textContent;
    button.disabled = false;
  }
}

/** localStorage that never throws (private windows, blocked storage). */
export const store = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* ignore */ }
  },
};
