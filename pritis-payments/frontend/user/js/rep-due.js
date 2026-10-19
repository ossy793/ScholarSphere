// Create a due, edit a draft, or propose a change to an approved due (which waits for approval).
import { api } from './api.js';
import { isoToLagosInput, lagosInputToIso } from './due-format.js';
import { formatNaira, koboToNairaInput, nairaToKobo } from '/shared/money.js';
import { setBusy, showError } from '/shared/ui.js';

const dueId = new URLSearchParams(location.search).get('id');
const form = document.getElementById('due-form');
const errorEl = document.getElementById('error');
let existing = null;

function fill(terms) {
  form.elements.title.value = terms.title;
  form.elements.description.value = terms.description;
  form.elements.amount.value = koboToNairaInput(terms.amount_kobo);
  form.elements.deadline.value = isoToLagosInput(terms.deadline);
  form.elements.require_matric.checked = terms.require_matric;
}

function read() {
  const f = form.elements;
  const amount = nairaToKobo(f.amount.value);
  const terms = {
    title: String(f.title.value).trim(),
    description: String(f.description.value).trim(),
    amount_kobo: amount,
    deadline: lagosInputToIso(f.deadline.value),
    require_matric: f.require_matric.checked,
  };
  if (terms.title.length < 3) throw new Error('Give the due a title of at least 3 characters.');
  if (amount == null || amount < 10_000) throw new Error('Enter an amount of at least ₦100.');
  if (!terms.deadline) throw new Error('Choose a deadline.');
  if (new Date(terms.deadline) <= new Date()) throw new Error('The deadline must be in the future.');
  return terms;
}

async function init() {
  const me = await api.get('/me');
  if (!me.is_rep) {
    location.replace('/rep-apply.html');
    return;
  }
  if (dueId) {
    existing = await api.get(`/rep/dues/${dueId}`);
    const approved = Boolean(existing.live_terms);
    document.getElementById('title').textContent = existing.closed ? 'Extend or change this due' : 'Edit due';
    document.getElementById('live-note').hidden = !approved;
    fill(existing.open_revision?.terms ?? existing.live_terms ?? existing.latest_revision.terms);
    // An approved due's change always goes for approval; no draft step.
    if (approved) {
      document.getElementById('draft-button').hidden = true;
      document.getElementById('submit-button').textContent = 'Submit change for approval';
    }
  } else {
    // A sensible default: two weeks from now, end of day.
    const d = new Date(Date.now() + 14 * 24 * 3600 * 1000);
    form.elements.deadline.value = `${isoToLagosInput(d.toISOString()).slice(0, 10)}T23:59`;
  }
  form.hidden = false;
  showQuote();
}

// While the rep types the amount: what each student will pay, and a message to announce it.
let quoteTimer = null;
async function showQuote() {
  const box = document.getElementById('quote-box');
  const kobo = nairaToKobo(form.elements.amount.value);
  if (!kobo || kobo < 10_000) { box.hidden = true; return; }
  try {
    const q = await api.get(`/rep/dues/quote?amount_kobo=${kobo}`);
    const title = form.elements.title.value.trim() || 'the due';
    document.getElementById('q-amount').textContent = formatNaira(q.amount_kobo);
    document.getElementById('q-charge').textContent = `+ ${formatNaira(q.service_charge_kobo)}`;
    document.getElementById('q-total').textContent = formatNaira(q.total_kobo);
    document.getElementById('q-message').textContent =
      `Hi everyone, ${title} is ${formatNaira(q.amount_kobo)}. When you pay online on Pritis DPP you'll pay `
      + `${formatNaira(q.total_kobo)}: the extra ${formatNaira(q.service_charge_kobo)} covers bank and payment `
      + `processing charges, so they don't come out of the ${formatNaira(q.amount_kobo)}. I'll share the payment link soon.`;
    box.hidden = false;
  } catch {
    box.hidden = true; // the quote is a help, never a blocker
  }
}
function queueQuote() {
  clearTimeout(quoteTimer);
  quoteTimer = setTimeout(showQuote, 300);
}
form.elements.amount.addEventListener('input', queueQuote);
form.elements.title.addEventListener('input', queueQuote);
document.getElementById('q-copy').addEventListener('click', async (event) => {
  const text = document.getElementById('q-message').textContent;
  try {
    await navigator.clipboard.writeText(text);
    event.target.textContent = 'Copied';
    setTimeout(() => { event.target.textContent = 'Copy message'; }, 2000);
  } catch {
    window.prompt('Copy this message:', text);
  }
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  const submit = event.submitter?.dataset.submit !== 'false';
  let terms;
  try {
    terms = read();
  } catch (err) {
    return showError(errorEl, err.message);
  }
  const button = event.submitter ?? form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Saving…');
  try {
    if (!existing) {
      await api.post('/rep/dues', { ...terms, submit });
    } else {
      await api.put(`/rep/dues/${existing.id}`, terms);
      // Editing a draft (or a rejected due) keeps it as a draft until submitted.
      if (submit && !existing.live_terms) await api.post(`/rep/dues/${existing.id}/submit`);
    }
    location.href = `/rep.html?saved=${submit ? 'submitted' : 'draft'}`;
  } catch (err) {
    showError(errorEl, err);
    setBusy(button, false);
  }
});

init().catch((err) => showError(errorEl, err));
