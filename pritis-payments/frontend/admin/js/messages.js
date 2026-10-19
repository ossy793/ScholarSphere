// Messages from support to many users: a group, or people picked by search.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { showError } from '/shared/ui.js';

const form = document.getElementById('message-form');
const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
const sendButton = document.getElementById('send');
const AUDIENCE = { all: 'Everyone', students: 'Students', reps: 'Reps', selected: 'Chosen people' };
const chosen = new Map(); // user id -> label
let recipients = 0;
let armed = false;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function audience() {
  return form.elements.audience.value;
}

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

function disarm() {
  armed = false;
  sendButton.textContent = recipients ? `Send to ${recipients} ${recipients === 1 ? 'person' : 'people'}` : 'Send';
  sendButton.disabled = recipients === 0;
}

let countTimer = null;
function refreshCount() {
  clearTimeout(countTimer);
  countTimer = setTimeout(async () => {
    try {
      const res = await api.post('/announcements/count', { audience: audience(), user_ids: [...chosen.keys()] });
      recipients = res.recipients;
      document.getElementById('count').textContent = `${recipients} ${recipients === 1 ? 'person' : 'people'}`;
    } catch (err) {
      showError(errorEl, err);
    }
    disarm();
  }, 250);
}

function renderChosen() {
  const box = document.getElementById('chosen');
  box.replaceChildren(...[...chosen].map(([id, label]) => {
    const remove = el('button', { type: 'button', textContent: '×', title: `Remove ${label}` });
    remove.setAttribute('aria-label', `Remove ${label}`);
    remove.addEventListener('click', () => { chosen.delete(id); renderChosen(); refreshCount(); });
    return el('span', { className: 'chip' }, label, remove);
  }));
  document.getElementById('chosen-empty').hidden = chosen.size > 0;
}

let findTimer = null;
document.getElementById('find').addEventListener('input', (event) => {
  clearTimeout(findTimer);
  const q = event.target.value.trim();
  const found = document.getElementById('found');
  if (q.length < 2) { found.replaceChildren(); return; }
  findTimer = setTimeout(async () => {
    try {
      const users = await api.get(`/users?q=${encodeURIComponent(q)}`);
      found.replaceChildren(...users.slice(0, 10).map((u) => {
        const label = `${u.full_name} (${u.email})`;
        const add = el('button', { type: 'button', className: 'btn btn-inline', textContent: chosen.has(u.id) ? 'Added' : 'Add',
          disabled: chosen.has(u.id) || !u.email_verified || u.status !== 'active' });
        add.addEventListener('click', () => {
          chosen.set(u.id, label);
          add.textContent = 'Added';
          add.disabled = true;
          renderChosen();
          refreshCount();
        });
        const note = !u.email_verified ? ' · email not verified, can\'t receive' : (u.status !== 'active' ? ' · disabled' : '');
        return el('div', { className: 'page-head' }, el('span', { className: 'small', textContent: label + note }), add);
      }));
      if (!users.length) found.replaceChildren(el('p', { className: 'muted small', textContent: 'Nobody matches.' }));
    } catch (err) {
      showError(errorEl, err);
    }
  }, 300);
});

form.addEventListener('change', (event) => {
  if (event.target.name === 'audience') {
    document.getElementById('picker').hidden = audience() !== 'selected';
    refreshCount();
  }
});
form.addEventListener('input', (event) => { if (event.target.name !== 'audience') disarm(); });

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(errorEl, null);
  notice('');
  const subject = form.elements.subject.value.trim();
  const message = form.elements.message.value.trim();
  if (subject.length < 3) return showError(errorEl, 'Add a subject (at least 3 characters).');
  if (message.length < 5) return showError(errorEl, 'Write the message first.');
  if (!armed) {
    armed = true;
    sendButton.textContent = `Click again to email ${recipients} ${recipients === 1 ? 'person' : 'people'}`;
    setTimeout(() => { if (armed) disarm(); }, 6000);
    return;
  }
  armed = false;
  sendButton.disabled = true;
  sendButton.textContent = 'Sending…';
  try {
    const res = await api.post('/announcements', { subject, message, audience: audience(), user_ids: [...chosen.keys()] });
    notice(`Queued for ${res.recipient_count} ${res.recipient_count === 1 ? 'person' : 'people'}. Emails go out over the next few minutes.`);
    form.elements.subject.value = '';
    form.elements.message.value = '';
    chosen.clear();
    renderChosen();
    await loadHistory();
  } catch (err) {
    showError(errorEl, err);
  }
  disarm();
});

async function loadHistory() {
  const rows = await api.get('/announcements');
  const body = document.getElementById('history');
  body.replaceChildren(...rows.map((a) => el('tr', {},
    el('td', { textContent: formatDateTime(a.created_at) }),
    el('td', {}, el('details', {}, el('summary', { textContent: a.subject }),
      el('div', { className: 'bubble-body small', textContent: a.body }))),
    el('td', { textContent: AUDIENCE[a.audience] }),
    el('td', { className: 'num', textContent: String(a.recipient_count) }),
    el('td', { textContent: a.sent_by_email }))));
  if (!rows.length) body.append(el('tr', {}, el('td', { colSpan: 5, className: 'muted', textContent: 'Nothing sent yet.' })));
}

renderLayout('/messages.html').then(async () => {
  refreshCount();
  await loadHistory();
}).catch((err) => showError(errorEl, err));
