// Support inbox: read a conversation, answer (emailed to the user), resolve or reopen.
import { api } from './api.js';
import { formatDateTime, renderLayout } from './layout.js';
import { showError } from '/shared/ui.js';

const TABS = ['open', 'answered', 'closed'];
const params = new URLSearchParams(location.search);
const tab = TABS.includes(params.get('tab')) ? params.get('tab') : 'open';
const only = params.get('ticket'); // a link from the "new message" email
const list = document.getElementById('list');
const errorEl = document.getElementById('error');
const noticeEl = document.getElementById('notice');
const STATUS = { open: 'Waiting', answered: 'Answered', closed: 'Resolved' };

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function notice(text) {
  noticeEl.textContent = text;
  noticeEl.hidden = !text;
}

async function act(button, fn) {
  showError(errorEl, null);
  notice('');
  button.disabled = true;
  try {
    notice(await fn());
    await load();
  } catch (err) {
    showError(errorEl, err);
    button.disabled = false;
  }
}

function ticketCard(t) {
  const card = el('section', { className: 'panel' },
    el('div', { className: 'page-head' },
      el('h2', { textContent: `#${t.ticket_no} · ${t.subject}` }),
      el('span', { className: 'badge', textContent: STATUS[t.status] })),
    el('p', { className: 'muted small', textContent:
      `${t.user_name}${t.is_rep ? ' (rep)' : ''} · ${t.email} · last message ${formatDateTime(t.last_message_at)}` }));

  const thread = el('div', { className: 'thread' });
  for (const m of t.messages) {
    thread.append(el('div', { className: `bubble ${m.sender === 'staff' ? 'bubble-staff' : 'bubble-user'}` },
      el('div', { className: 'bubble-meta', textContent:
        `${m.sender === 'staff' ? (m.staff_name || 'Support') : t.user_name} · ${formatDateTime(m.created_at)}` }),
      el('div', { className: 'bubble-body', textContent: m.body })));
  }
  card.append(thread);

  const box = el('textarea', { rows: 4, maxLength: 5000, placeholder: `Answer ${t.user_name}… (emailed to ${t.email})` });
  box.setAttribute('aria-label', `Answer message #${t.ticket_no}`);
  const resolve = el('input', { type: 'checkbox', checked: t.status === 'open' });
  const send = el('button', { type: 'button', className: 'btn btn-primary btn-inline', textContent: 'Send answer' });
  send.addEventListener('click', () => {
    if (box.value.trim().length < 5) {
      box.focus();
      return showError(errorEl, 'Write the answer first (at least 5 characters).');
    }
    act(send, async () => {
      await api.post(`/support/${t.id}/reply`, { message: box.value.trim(), close: resolve.checked });
      return `Answer emailed to ${t.email}.${resolve.checked ? ' Marked resolved.' : ''}`;
    });
  });
  const toggle = el('button', { type: 'button', className: 'btn btn-inline',
    textContent: t.status === 'closed' ? 'Reopen' : 'Mark resolved without answering' });
  toggle.addEventListener('click', () => act(toggle, async () => {
    await api.post(`/support/${t.id}/${t.status === 'closed' ? 'reopen' : 'close'}`);
    return t.status === 'closed' ? 'Reopened.' : 'Marked resolved.';
  }));

  card.append(box,
    el('label', { className: 'check' }, resolve, 'Mark resolved when sent'),
    el('div', { className: 'panel-actions' }, send, toggle));
  return card;
}

async function load() {
  showError(errorEl, null);
  if (only) {
    list.replaceChildren(ticketCard(await api.get(`/support/${only}`)),
      el('p', {}, el('a', { href: '/support.html', textContent: '← All messages' })));
    return;
  }
  const page = await api.get(`/support?status=${tab}`);
  list.replaceChildren(...page.items.map(ticketCard));
  if (!page.items.length) {
    list.append(el('p', { className: 'panel muted', textContent: tab === 'open' ? 'Nothing waiting. Nice.' : 'Nothing here.' }));
  }
}

document.querySelectorAll('.tabs a').forEach((a) => {
  if (a.dataset.tab === tab && !only) a.setAttribute('aria-current', 'page');
});
renderLayout('/support.html').then(load).catch((err) => showError(errorEl, err));
