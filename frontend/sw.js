const CACHE = 'pritis-v9';

const APP_SHELL = [
  '/index.html',
  '/dashboard.html',
  '/brainstorm.html',
  '/ai-generate.html',
  '/input-questions.html',
  '/question-bank.html',
  '/quiz-practice.html',
  '/performance.html',
  '/upgrade.html',
  '/css/main.css',
  '/js/api.js',
  '/js/layout.js',
  '/favicon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

// Install: cache app shell (best-effort — don't fail if a file is missing)
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.allSettled(APP_SHELL.map(url => c.add(url))))
      .then(() => self.skipWaiting())
  );
});

// Activate: remove old caches, then force all open tabs to reload with fresh files
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
      .then(() => self.clients.matchAll({ type: 'window' }))
      .then(clients => clients.forEach(client => client.navigate(client.url)))
  );
});

// Fetch: network-first for API, cache-first for static assets
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Always go to network for API calls and non-GET requests
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;

  // Cache-first for images/fonts only (these rarely change)
  if (
    url.pathname.match(/\.(svg|png|jpg|ico|woff2?)$/) ||
    url.hostname !== location.hostname
  ) {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
    return;
  }

  // Network-first for JS and CSS (so code updates are picked up immediately)
  if (url.pathname.match(/\.(js|css)$/)) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Network-first for HTML pages (so updates are picked up)
  e.respondWith(
    fetch(e.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

// ── Push notifications ────────────────────────────────────────────────────────

self.addEventListener('push', e => {
  let data = { title: 'Pritis', body: 'You have a new notification.', url: '/dashboard.html' };
  try {
    if (e.data) data = { ...data, ...e.data.json() };
  } catch {}

  e.waitUntil(
    self.registration.showNotification(data.title, {
      body:    data.body,
      icon:    '/icons/icon-192.png',
      badge:   '/icons/icon-192.png',
      data:    { url: data.url },
      vibrate: [200, 100, 200],
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data?.url || '/dashboard.html';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const client of list) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
