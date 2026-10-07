/* Photo OCR — the extract action, the result textarea, copy-to-
 * clipboard, and resetting the current take. */

'use strict';

import { state, els, toast, modelLabel, copyText } from './state.js';
import { jsonApi } from './api.js';
import { renderThumbnails, setStatus, syncPhotoOrder } from './capture.js';
import { loadHistory } from './sessions.js';
import { pollUntilDone, extractStatusLine } from './poll.js';
import { icon } from './_vendored/icons/icons.js';

export function renderExtracted() {
  els.extracted.value = state.extracted || '';
  els.copyExtracted.disabled = !state.extracted;
  // The one action for a partly read take; absent when nothing is missing.
  els.retryMissing.hidden = !(state.sessionId && state.missingPhotos.length);
}

// Render one poll body. The poll loop (poll.js) filters terminal phases,
// so during polling only the queued/running/merging branch fires here; the
// succeeded branch runs once at the end with the final body.
function renderExtractStatus(body, startedAt) {
  if (body.phase === 'succeeded') {
    state.extracted = body.extracted || '';
    state.missingPhotos = body.missing_photos || [];
    renderExtracted();
    const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1);
    const seconds = body.duration_s ? Number(body.duration_s).toFixed(1) : elapsed;
    if (body.reused) {
      setStatus('Already extracted — showing cached result · ' + seconds + ' s');
    } else if (!state.extracted) {
      setStatus('No readable text detected · ' + seconds + ' s');
    } else {
      setStatus('Done in ' + seconds + ' s — tap Copy');
    }
  } else if (body.phase === 'failed') {
    throw new Error(body.error || 'extract failed');
  } else {
    setStatus(extractStatusLine(body, ''));
  }
}

// ----------------------------------------------------------- job scaffold
// The three OCR jobs — Extract, Redo (History) and Retry missing — share one
// shape: start POST, poll to a terminal phase, show the outcome, and keep the
// busy flag and the status line honest on success and failure alike.
export function postJob(sessionId, action, payload) {
  const opts = { method: 'POST', timeoutMs: 15000 };
  if (payload) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(payload);
  }
  return jsonApi('/api/sessions/' + encodeURIComponent(sessionId) + '/' + action, opts);
}

// The terminal body of a job whose start answer was `started`. `prefix` words
// the progress line per surface ('' | 'Redo ' | 'Retry · ').
export function followJob(sessionId, started, prefix) {
  if (started.phase === 'succeeded') return Promise.resolve(started);
  return pollUntilDone(sessionId, function (b) {
    setStatus(extractStatusLine(b, prefix));
  });
}

// Run `run()` inside the busy cycle. `begin` / `end` toggle the caller's own
// button; `failLabel` heads the failure toast ('Extract failed'), and
// `failStatus` heads the status line when it differs ('Failed: ' by default).
// The final status line survives the thumbnail re-render that ends the cycle.
export async function runTakeJob(opts) {
  if (state.busy) return;
  state.busy = true;
  if (opts.begin) opts.begin();
  renderThumbnails();
  setStatus(opts.startStatus);
  let finalStatusText = null;
  try {
    await opts.run();
    finalStatusText = els.captureStatus.textContent;
  } catch (exc) {
    setStatus((opts.failStatus || 'Failed: ') + (exc.message || exc));
    finalStatusText = els.captureStatus.textContent;
    toast(opts.failLabel + ': ' + (exc.message || exc), 'error');
  } finally {
    state.busy = false;
    if (opts.end) opts.end();
    renderThumbnails();
    if (finalStatusText) setStatus(finalStatusText);
  }
}

// ----------------------------------------------------------- extract
export async function extract() {
  if (!state.sessionId) {
    toast('No session yet — add a photo first.', 'error');
    return;
  }
  if (state.busy) return;
  const readyPhotos = state.photos.filter(function (p) { return p.status === 'ready'; });
  if (!readyPhotos.length) {
    toast('No photos ready yet.', 'error');
    return;
  }
  const t0 = Date.now();
  await runTakeJob({
    failLabel: 'Extract failed',
    startStatus:
      'LLM hub → ' + modelLabel(state.model) + ' · extracting from ' + readyPhotos.length + ' photo(s)…',
    begin: function () { els.extractBtn.classList.add('busy'); },
    end: function () { els.extractBtn.classList.remove('busy'); },
    run: async function () {
      await syncPhotoOrder();
      const body = await postJob(state.sessionId, 'extract', {
        model: state.model,
        prompt_id: state.promptId,
      });
      renderExtractStatus(body, t0);
      if (body.phase !== 'succeeded') {
        const finalBody = await pollUntilDone(state.sessionId, function (b) {
          renderExtractStatus(b, t0);
        });
        renderExtractStatus(finalBody, t0);
      }
      loadHistory(0);
    },
  });
}

// ----------------------------------------------------------- retry missing
// Re-read only the photos a partly read take left unread. Shared by the
// Capture result card and the History row menu; the outcome is one toast.
export async function retryMissing(sessionId) {
  await runTakeJob({
    failLabel: 'Retry failed',
    failStatus: 'Retry failed: ',
    startStatus: 'Queued retry…',
    begin: function () { els.retryMissing.disabled = true; },
    end: function () { els.retryMissing.disabled = false; },
    run: async function () {
      const started = await postJob(sessionId, 'retry-missing');
      const before = (started.missing_photos || []).length;
      const body = await followJob(sessionId, started, 'Retry · ');
      adoptTake(sessionId, body);
      loadHistory(0);
      const left = state.missingPhotos.length;
      if (body.retry_error) {
        setStatus('Retry failed: ' + body.retry_error);
        toast('Retry failed: ' + body.retry_error, 'error');
      } else if (!left) {
        setStatus('All photos read — tap Copy');
        toast('All photos read.');
      } else {
        setStatus(left + ' photo(s) still missing');
        toast('Read ' + (before - left) + ' of ' + before + ' missing; ' + left + ' still missing.');
      }
    },
  });
}

// ----------------------------------------------------------- copy
export async function copyExtracted() {
  const txt = state.extracted || '';
  if (!txt) return;
  try {
    await copyText(txt);
    els.copyExtracted.classList.add('copied');
    const original = els.copyExtracted.innerHTML;
    els.copyExtracted.innerHTML = icon('check') + ' Copied';
    setTimeout(function () {
      els.copyExtracted.classList.remove('copied');
      els.copyExtracted.innerHTML = original;
    }, 1200);
  } catch (exc) {
    toast('Copy failed: ' + (exc.message || exc), 'error');
  }
}

// ----------------------------------------------------------- reset
function dropPhotos() {
  state.photos.forEach(function (p) {
    if (p.previewUrl) {
      try { URL.revokeObjectURL(p.previewUrl); } catch (_) {}
    }
  });
  state.photos = [];
}

// Show a History take's result in the Capture card (Redo / Retry missing).
// When it is a different take from the one in the strip, the strip's photos
// aren't its photos: drop them, and let the next added photo start a new take.
export function adoptTake(sessionId, body) {
  if (state.sessionId !== sessionId) {
    dropPhotos();
    state.takeAdopted = true;
  }
  state.sessionId = sessionId;
  state.extracted = body.extracted || '';
  state.missingPhotos = body.missing_photos || [];
  renderExtracted();
}

export function resetTake() {
  dropPhotos();
  state.takeAdopted = false;
  state.sessionId = null;
  state.sessionIdPromise = null;
  state.extracted = '';
  state.missingPhotos = [];
  renderThumbnails();
  renderExtracted();
  setStatus('Add a photo to begin');
}
