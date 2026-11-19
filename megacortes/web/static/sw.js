// MegaCortes Service Worker para suporte PWA
const CACHE_NAME = 'megacortes-v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Pass-through com fallback simples
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
