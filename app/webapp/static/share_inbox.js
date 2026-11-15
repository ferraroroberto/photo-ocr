/* Photo OCR — the page half of the Android share-sheet target.
 *
 * sw.js answers the share POST, parks the photos in Cache Storage and
 * redirects to /?share-target=<outcome>. This module registers that worker,
 * reads the outcome off the URL (then strips it, so a reload doesn't replay the
 * share) and hands the parked photos back as File objects for the normal
 * Capture upload.
 */

'use strict';

// Keep in step with sw.js.
const SHARE_CACHE = 'photo-ocr-share';
const SHARE_ENTRY = '/share-inbox/';
const SHARE_PARAM = 'share-target';

// The messages for a share that brought no photos to upload. 'missed' is the
// server's own fallback: the POST reached it because no worker was active yet.
export const SHARE_PROBLEMS = {
  empty: 'No photos came with that share.',
  unreadable: "Couldn't read the shared photos. Share them again.",
  missed: 'That share arrived before the app was ready. Share the photos again.',
};

export function registerShareWorker() {
  // Service workers need a secure context; a plain-http LAN load has none.
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.register('/sw.js').catch(function (exc) {
    console.warn('⚠️ share-target worker not registered', exc);
  });
}

/** The share outcome this load was opened with, or null; strips it from the URL. */
export function takeShareOutcome() {
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get(SHARE_PARAM);
  if (outcome === null) return null;
  params.delete(SHARE_PARAM);
  const query = params.toString();
  window.history.replaceState(
    {},
    '',
    window.location.pathname + (query ? '?' + query : '') + window.location.hash
  );
  return outcome;
}

/** The parked photos in share order, emptied from the cache as they are read. */
export async function collectSharedFiles() {
  if (!('caches' in window)) return [];
  const cache = await caches.open(SHARE_CACHE);
  const entries = [];
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname;
    if (!path.startsWith(SHARE_ENTRY)) continue;
    const res = await cache.match(req);
    if (res) {
      const blob = await res.blob();
      const name = decodeURIComponent(res.headers.get('X-File-Name') || 'shared');
      entries.push({
        index: Number(path.slice(SHARE_ENTRY.length)),
        file: new File([blob], name, { type: blob.type }),
      });
    }
    await cache.delete(req);
  }
  entries.sort(function (a, b) { return a.index - b.index; });
  return entries.map(function (e) { return e.file; });
}
