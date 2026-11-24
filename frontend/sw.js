// Only cache static GET resources. Account and transit responses always use the network.
const CACHE_NAME = 'delhi-transit-v5';
const STATIC_ASSETS = ['/', '/manifest.json', '/assets/styles.css', '/assets/redesign.css', '/assets/layout.css', '/assets/vehicle-motion.js', '/assets/rider.js', '/assets/mobile-flow.js', '/assets/icon.svg'];
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith('delhi-transit-') && k !== CACHE_NAME).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin ||
      !(url.pathname === '/' || url.pathname === '/manifest.json' || url.pathname.startsWith('/assets/'))) return;
  // Network first ensures a deploy does not leave old HTML using mismatched scripts.
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) {
      const copy = response.clone();
      event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)));
    }
    return response;
  }).catch(() => caches.match(event.request).then(cached => cached || Response.error())));
});
