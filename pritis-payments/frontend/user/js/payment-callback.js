// Where Paystack sends the student back. This page decides nothing: it only asks our server,
// which checks with Paystack itself (and accepts Paystack's signed webhook).
import { api } from './api.js';
import { formatNaira } from '/shared/money.js';
import { showError } from '/shared/ui.js';

const paymentId = new URLSearchParams(location.search).get('payment');
const POLL_MS = 2500;
const GIVE_UP_MS = 90_000;
const started = Date.now();

function show(id) {
  for (const section of ['checking', 'success', 'failed', 'slow']) {
    document.getElementById(section).hidden = section !== id;
  }
}

async function poll() {
  const p = await api.get(`/payments/${paymentId}`);
  if (p.status === 'success') {
    document.getElementById('success-text').textContent =
      `${formatNaira(p.total_kobo)} for ${p.due_title} (${p.class_name}). Your receipt ${p.receipt_no} is on its way to your email.`;
    show('success');
    return;
  }
  if (p.status === 'failed' || p.status === 'abandoned') {
    document.getElementById('retry-link').href = `/due.html?id=${p.due_id}`;
    show('failed');
    return;
  }
  if (Date.now() - started > GIVE_UP_MS) {
    show('slow');
    return;
  }
  setTimeout(() => poll().catch(onError), POLL_MS);
}

function onError(err) {
  // A network blip shouldn't end the wait; keep trying until the time limit.
  if (err.status === 0 && Date.now() - started < GIVE_UP_MS) {
    setTimeout(() => poll().catch(onError), POLL_MS);
    return;
  }
  show('slow');
  showError(document.getElementById('error'), err);
}

if (!paymentId) {
  show('slow');
} else {
  poll().catch(onError);
}
