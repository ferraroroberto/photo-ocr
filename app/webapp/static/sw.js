/* Photo OCR — service worker: the Android share-sheet target, nothing else.
 *
 * The manifest's share_target POSTs the shared photos to /share-target as a
 * navigation. That request carries no bearer token (the token lives in the
 * page's localStorage), so the server must not be the one to take the files.
 * This worker answers the POST itself: it parks the files in Cache Storage and
 * redirects to the app, whose boot reads them back and uploads them through
 * the normal, authenticated Capture path (share_inbox.js).
 *
 * Every other request falls through to the network untouched: no offline
 * cache, so a deploy is never hidden behind a stale shell.
 */

'use strict';

// Keep in step with manifest.webmanifest's share_target and share_inbox.js.
const SHARE_PATH = '/share-target';
const SHARE_FIELD = 'photos';
const SHARE_CACHE = 'photo-ocr-share';
const SHARE_ENTRY = '/share-inbox/';
const SHARE_PARAM = 'share-target';

// A new worker takes over on the next navigation instead of waiting for every
// tab to close; a share is always a navigation, so it reaches the new code.
self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('fetch', function (event) {
  const request = event.request;
  if (request.method !== 'POST') return;
  if (new URL(request.url).pathname !== SHARE_PATH) return;
  event.respondWith(stashShare(request));
});

async function stashShare(request) {
  let outcome = 'empty';
  try {
    const form = await request.formData();
    const files = form.getAll(SHARE_FIELD).filter(function (f) {
      return f && typeof f !== 'string';
    });
    const cache = await caches.open(SHARE_CACHE);
    // One share at a time: a share the page never collected is replaced.
    for (const key of await cache.keys()) await cache.delete(key);
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      await cache.put(
        SHARE_ENTRY + i,
        new Response(file, {
          headers: {
            'Content-Type': file.type || 'application/octet-stream',
            'X-File-Name': encodeURIComponent(file.name || 'shared-' + (i + 1)),
          },
        })
      );
    }
    if (files.length) outcome = 'photos';
  } catch (exc) {
    console.warn('⚠️ share-target: could not read the shared files', exc);
    outcome = 'unreadable';
  }
  return Response.redirect('/?' + SHARE_PARAM + '=' + outcome, 303);
}
