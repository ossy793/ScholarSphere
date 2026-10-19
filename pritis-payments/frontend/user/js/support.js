// Contact support: write a message, read the answers, and add to a conversation.
import { api } from './api.js';
import { setBusy, showError } from '/shared/ui.js';

const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
const form = document.getElementById('support-form');
const STATUS = {
  open: { label: 'Waiting for support', badge: 'badge badge-invited' },
  answered: { label: 'Answered', badge: 'badge badge-active' },
  closed: { label: 'Resolved', badge: 'badge badge-disabled' },
};

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function when(iso) {
  return new Date(iso).toLocaleString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium', timeStyle: 'short' });
}

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

function ticketCard(t) {
  const s = STATUS[t.status];
  const card = el('div', { className: 'due-card' },
    el('div', { className: 'page-head' },
      el('h3', { textContent: `${t.subject}` }),
      el('span', { className: s.badge, textContent: s.label })),
    el('p', { className: 'muted small', textContent: `Message #${t.ticket_no} · started ${when(t.created_at)}` }));
  const thread = el('div', { className: 'thread' });
  for (const m of t.messages) {
    thread.append(el('div', { className: `bubble ${m.sender === 'staff' ? 'bubble-staff' : 'bubble-user'}` },
      el('div', { className: 'bubble-meta', textContent: `${m.sender === 'staff' ? 'Pritis support' : 'You'} · ${when(m.created_at)}` }),
      el('div', { className: 'bubble-body', textContent: m.body })));
  }
  card.append(thread);

  // Add to the conversation.
  const box = el('textarea', { rows: 2, maxLength: 5000, placeholder: t.status === 'closed' ? 'Still need help? Write here to reopen it.' : 'Write a reply…' });
  box.setAttribute('aria-label', `Reply to message #${t.ticket_no}`);
  const send = el('button', { type: 'button', className: 'btn btn-inline', textContent: 'Send' });
  send.addEventListener('click', async () => {
    showError(errorEl, null);
    notice('');
    if (box.value.trim().length < 5) {
      box.focus();
      return showError(errorEl, 'Write a little more (at least 5 characters).');
    }
    send.disabled = true;
    try {
      await api.post(`/support/${t.id}/messages`, { message: box.value.trim() });
      notice('Sent. Support will answer by email.');
      await load();
    } catch (err) {
      showError(errorEl, err);
      send.disabled = false;
    }
  });
  card.append(el('div', { className: 'reject-box' }, box, send));
  return card;
}

async function load() {
  const tickets = await api.get('/support');
  document.getElementById('history-section').hidden = tickets.length === 0;
  document.getElementById('tickets').replaceChildren(...tickets.map(ticketCard));
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  notice('');
  const subject = form.elements.subject.value.trim();
  const message = form.elements.message.value.trim();
  if (subject.length < 3) return showError(errorEl, 'Add a short subject.');
  if (message.length < 5) return showError(errorEl, 'Tell us a little more in the message.');
  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true);
  try {
    const t = await api.post('/support', { subject, message });
    form.reset();
    notice(`Sent as message #${t.ticket_no}. We'll answer by email to ${t.email}.`);
    await load();
  } catch (err) {
    showError(errorEl, err);
  } finally {
    setBusy(button, false);
  }
});

async function init() {
  const me = await api.get('/me');
  document.getElementById('email').textContent = me.email;
  await load();
  document.getElementById('main').hidden = false;
}

init().catch((err) => showError(errorEl, err));
