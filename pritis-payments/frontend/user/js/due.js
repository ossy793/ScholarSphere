// A due: /due.html?s=CODE (the rep's shared link) or /due.html?id=... (from the dashboard).
// Logged out -> log in (or sign up) and come back. Opening the link joins the class.
// Paying: the student sees the total, then goes to Paystack.
import { api } from './api.js';
import { formatDeadline } from './due-format.js';
import { formatNaira } from '/shared/money.js';
import { setBusy, showError } from '/shared/ui.js';

const params = new URLSearchParams(location.search);
const slug = (params.get('s') ?? '').trim();
const dueId = params.get('id');
const errorEl = document.getElementById('error');
const form = document.getElementById('pay-form');
let due = null;

function showDue(d) {
  document.getElementById('class-name').textContent = d.class_name;
  document.getElementById('due-title').textContent = d.title;
  document.getElementById('due-description').textContent = d.description;
  document.getElementById('due-description').hidden = !d.description;
  document.getElementById('due-deadline').textContent =
    `${d.status === 'closed' ? 'Closed' : 'Closes'} ${formatDeadline(d.deadline)}`;
  document.title = `${d.title} · Pritis Class Dues`;
  document.getElementById('due').hidden = false;
}

async function showPayment(d, me) {
  if (d.status === 'paid') {
    // Receipts go by email only.
    document.getElementById('receipt-note').textContent =
      `Your receipt was emailed to ${me.email}. Can't find it? Check your spam folder.`;
    const resend = document.getElementById('resend-receipt');
    resend.addEventListener('click', async () => {
      showError(errorEl, null);
      setBusy(resend, true, 'Sending…');
      try {
        const res = await api.post(`/payments/${d.payment_id}/email-receipt`);
        resend.textContent = `Sent to ${res.email}`;
      } catch (err) {
        showError(errorEl, err);
        setBusy(resend, false);
      }
    });
    document.getElementById('paid').hidden = false;
    return;
  }
  if (d.status === 'closed') {
    document.getElementById('closed-note').hidden = false;
    return;
  }
  // Students see only the total (owner's decision: no due / service charge breakdown).
  const q = await api.get(`/dues/${d.id}/quote`);
  document.getElementById('q-total').textContent = formatNaira(q.total_kobo);
  document.getElementById('pay').textContent = `Pay ${formatNaira(q.total_kobo)}`;
  if (q.require_matric) {
    document.getElementById('matric-field').hidden = false;
    if (me.matric_no) form.elements.matric_no.value = me.matric_no;
  }
  // Paying needs a verified email and a complete profile.
  if (!me.email_verified || !me.profile_complete) {
    const here = encodeURIComponent(location.pathname + location.search);
    const note = document.getElementById('verify-note');
    note.textContent = me.email_verified
      ? 'Complete your profile before paying.'
      : 'Verify your email before paying.';
    const link = document.createElement('a');
    link.href = me.email_verified ? `/complete-profile.html?next=${here}` : `/verify-email.html?next=${here}`;
    link.textContent = me.email_verified ? ' Complete profile' : ' Enter your code';
    note.append(link);
    note.hidden = false;
    document.getElementById('pay').disabled = true;
  }
  form.hidden = false;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const body = { due_id: due.id };
  if (!document.getElementById('matric-field').hidden) {
    const matric = String(form.elements.matric_no.value).trim();
    if (matric.length < 3) return showError(errorEl, 'Enter your matric number.');
    body.matric_no = matric;
  }
  const button = document.getElementById('pay');
  setBusy(button, true, 'Opening Paystack…');
  try {
    const res = await api.post('/payments', body);
    location.href = res.authorization_url; // Paystack's checkout; it sends them back to the callback page
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
  }
});

async function init() {
  if (!slug && !dueId) throw new Error('This link is incomplete. Ask your class rep to send it again.');
  // Not logged in? api sends them to /login.html?next=<this page>, and signup keeps "next" too.
  due = dueId ? await api.get(`/dues/${dueId}`) : await api.post(`/dues/by-link/${encodeURIComponent(slug)}`);
  const me = await api.get('/me');
  showDue(due);
  await showPayment(due, me);
}

init()
  .catch((err) => showError(errorEl, err))
  .finally(() => { document.getElementById('loading').hidden = true; });
