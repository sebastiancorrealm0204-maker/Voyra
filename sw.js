/* Voyra Companion — service worker
 * Estrategia:
 *  - Shell (navegación a "/")  → network-first, cae a caché si no hay red.
 *  - Librerías de CDN (GET)    → stale-while-revalidate (abre rápido, refresca en background).
 *  - API (/trips, /health, …)  → nunca se cachea. Los POST ni siquiera entran aquí.
 *  Sube el número de versión cada vez que cambies el shell para forzar refresco.
 */
const VERSION = "voyra-v2";
const SHELL_CACHE = `${VERSION}-shell`;
const RUNTIME_CACHE = `${VERSION}-runtime`;

// CDNs que usa el HTML — cachearlos permite abrir offline tras la primera carga.
const PRECACHE = [
  "/",
  "https://cdn.tailwindcss.com",
  "https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.development.js",
  "https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.development.js",
  "https://cdnjs.cloudflare.com/ajax/libs/babel-standalone/7.23.5/babel.min.js",
];

// Rutas de API que jamás deben servirse desde caché.
const API_PREFIXES = ["/trips", "/health", "/scan", "/nearby", "/signals"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(async (cache) => {
      // Individual para que un CDN caído no tumbe todo el precache.
      await Promise.allSettled(
        PRECACHE.map((url) =>
          fetch(url, { mode: url.startsWith("http") ? "no-cors" : "same-origin" })
            .then((res) => cache.put(url, res))
            .catch(() => {})
        )
      );
      self.skipWaiting();
    })
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return; // POST a la API: que pase directo a la red.

  const url = new URL(request.url);
  const sameOrigin = url.origin === self.location.origin;

  // API → siempre red, sin caché.
  if (sameOrigin && API_PREFIXES.some((p) => url.pathname.startsWith(p))) {
    return; // dejar pasar a la red sin interceptar
  }

  // Navegación al shell → network-first, fallback a caché ("/").
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((c) => c.put("/", copy));
          return res;
        })
        .catch(() => caches.match("/").then((r) => r || caches.match(request)))
    );
    return;
  }

  // CDN / estáticos GET → stale-while-revalidate.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((res) => {
          if (res && (res.ok || res.type === "opaque")) {
            const copy = res.clone();
            caches.open(RUNTIME_CACHE).then((c) => c.put(request, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
