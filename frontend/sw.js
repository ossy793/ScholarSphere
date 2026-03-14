const CACHE = 'pritis-v1';

const APP_SHELL = [
  '/index.html',
  '/dashboard.html',
  '/brainstorm.html',
  '/ai-generate.html',
  '/input-questions.html',
  '/my-questions.html',
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

// Install: cache app shell
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(APP_SHELL)).then(() => self.skipWaiting())
  );
});

// Activate: remove old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch: network-first for API, cache-first for static assets
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Always go to network for API calls and non-GET requests
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;

  // Cache-first for static assets (JS, CSS, images, fonts)
  if (
    url.pathname.match(/\.(js|css|svg|png|jpg|ico|woff2?)$/) ||
    url.hostname !== location.hostname
  ) {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
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
