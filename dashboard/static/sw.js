// Solar Bridge service worker — minimal app-shell cache for installability + fast loads.
const CACHE = 'solar-bridge-v1';
const SHELL = ['/', '/static/icon-192.png', '/static/icon-512.png', '/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Never cache API / socket.io / live data — always go to network.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/socket.io')) {
    return; // default network handling
  }
  // Network-first for the page shell; fall back to cache when offline.
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (e.request.method === 'GET' && res.ok && url.origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match('/')))
  );
});
