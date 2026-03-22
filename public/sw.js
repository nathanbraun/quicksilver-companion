const CACHE_NAME = 'quicksilver-v1';
const PRECACHE_URLS = ['/', '/offline'];

// Install: precache shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch: network first, cache fallback
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Cache successful responses
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match('/offline')))
  );
});

// Handle messages from the page
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'CACHE_ALL') {
    const urls = event.data.urls;
    cacheAllPages(urls, event.source);
  }

  if (event.data && event.data.type === 'CHECK_CACHE') {
    checkCacheStatus(event.data.urls, event.source);
  }

  if (event.data && event.data.type === 'CLEAR_CACHE') {
    caches.delete(CACHE_NAME).then(() => {
      caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS));
      if (event.source) {
        event.source.postMessage({ type: 'CACHE_CLEARED' });
      }
    });
  }
});

async function checkCacheStatus(urls, client) {
  const cache = await caches.open(CACHE_NAME);
  const keys = await cache.keys();
  const cachedUrls = new Set(keys.map(r => new URL(r.url).pathname));
  let cached = 0;
  for (const url of urls) {
    // Normalize: check with and without trailing slash
    const path = new URL(url, self.location.origin).pathname;
    if (cachedUrls.has(path) || cachedUrls.has(path.replace(/\/$/, ''))) cached++;
  }
  if (client) {
    client.postMessage({ type: 'CACHE_STATUS', cached, total: urls.length });
  }
}

async function cacheAllPages(urls, client) {
  const cache = await caches.open(CACHE_NAME);
  let done = 0;
  const total = urls.length;
  const BATCH_SIZE = 5;

  for (let i = 0; i < total; i += BATCH_SIZE) {
    const batch = urls.slice(i, i + BATCH_SIZE);
    const results = await Promise.allSettled(
      batch.map(async (url) => {
        // Skip if already cached
        const existing = await cache.match(url);
        if (existing) return;
        const response = await fetch(url);
        if (response.ok) {
          // Use response.url as key to match the final URL after any redirects
          await cache.put(response.url, response);
        }
      })
    );
    done += batch.length;
    if (client) {
      client.postMessage({ type: 'CACHE_PROGRESS', done, total });
    }
  }

  if (client) {
    client.postMessage({ type: 'CACHE_COMPLETE' });
  }
}
