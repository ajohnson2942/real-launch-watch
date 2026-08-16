// Minimal service worker: enables "Add to Home Screen" installability.
// We deliberately do NOT cache launches.json, so the dashboard always
// shows fresh data instead of a stale offline copy.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {}); // pass-through, no caching
