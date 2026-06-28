/* Service Worker for Sports Meet PWA
   Strategy:
   - Static assets (/static/*): cache-first, update in background
   - HTML pages: network-first, fall back to /about for offline
   - Version bump CACHE_NAME to force a full refresh on deploy
*/
const CACHE_NAME = 'sports-meet-v1';

const PRECACHE_URLS = [
  '/static/style.css',
  '/static/app.js',
  '/about',
];

// ---- Install: precache core assets ----------------------------------------
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(c => c.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// ---- Activate: delete stale caches ----------------------------------------
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ---- Fetch ----------------------------------------------------------------
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;          // never intercept POST/etc.

  const url = new URL(req.url);

  // Static assets: cache-first, then update cache in background
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.open(CACHE_NAME).then(cache =>
        cache.match(req).then(cached => {
          const networkFetch = fetch(req).then(res => {
            if (res.ok) cache.put(req, res.clone());
            return res;
          });
          return cached || networkFetch;
        })
      )
    );
    return;
  }

  // HTML: network-first; on failure serve /about (always cached, works offline)
  e.respondWith(
    fetch(req).catch(() => caches.match('/about'))
  );
});
