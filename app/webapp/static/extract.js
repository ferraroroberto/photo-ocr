/* Photo OCR — the extract action, the result view of the take card (meta
 * line, editable text, Copy text, New take, Retry missing), copy-to-
 * clipboard, and resetting the current take. */

'use strict';

import { state, els, toast, modelLabel, copyText } from './state.js';
import { jsonApi } from './api.js';
import {
  renderThumbnails,
  renderTakeOptions,
  renderView,
  renderHeadline,
  setError,
  setProgress,
  clearProgress,
  syncPhotoOrder,
} from './capture.js';
import { loadHistory } from './sessions.js';
import { pollUntilDone, extractProgress } from './poll.js';
import { defaultModel, defaultPromptId } from './settings.js';
import { icon } from './_vendored/icons/icons.js';

// The result's one meta line, from the outcome the job reported: model,
// time, cached, and whether the take is in History. The photo count is the
// "N photos ›" row's, so it is not repeated here.
function resultMeta(r) {
  const parts = [];
  if (r.cached) parts.push('Cached result');
  if (r.model) parts.push(modelLabel(r.model));
  if (r.seconds) parts.push(r.seconds + ' s');
  parts.push(r.saved ? 'saved to History' : 'not saved to History');
  return parts.join(' · ');
}

export function renderExtracted() {
  const r = state.result;
  els.extracted.value = state.extracted || '';
  els.copyExtracted.disabled = !state.extracted;
  if (r) {
    els.resultMeta.textContent = resultMeta(r);
    // A take that read nothing says so instead of an empty box and a
    // disabled Copy.
    const empty = !state.extracted;
    els.resultEmpty.hidden = !empty;
    els.extracted.hidden = empty;
    els.copyExtracted.hidden = empty;
  }
  // The one action for a partly read take, naming how many; absent when
  // nothing is missing.
  const missing = state.missingPhotos.length;
  els.retryMissing.hidden = !(state.sessionId && missing);
  els.retryMissingLabel.textContent = 'Retry ' + missing + ' unread';
  renderView();
  renderHeadline();
}

// The take's result from a job's terminal body. `startedAt` times it when the
// server did not.
function takeResult(body, startedAt) {
  const elapsed = startedAt ? (Date.now() - startedAt) / 1000 : 0;
  const seconds = body.duration_s ? Number(body.duration_s) : elapsed;
  return {
    model: body.model || state.model,
    seconds: seconds ? seconds.toFixed(1) : '',
    cached: !!body.reused,
    // A History take is in History; this take is unless its switch is off.
    saved: state.takeAdopted || !state.incognito,
  };
}

// Render one poll body. The poll loop (poll.js) filters terminal phases,
// so during polling only the queued/running/merging branch fires here; the
// succeeded branch runs once at the end with the final body.
function renderExtractStatus(body, startedAt) {
  if (body.phase === 'succeeded') {
    state.extracted = body.extracted || '';
    state.missingPhotos = body.missing_photos || [];
    state.result = takeResult(body, startedAt);
    state.showPhotos = false;
    renderExtracted();
  } else if (body.phase === 'failed') {
    throw new Error(body.error || 'extract failed');
  } else {
    setProgress(extractProgress(body, ''));
  }
}

// ----------------------------------------------------------- job scaffold
// The three OCR jobs — Extract, Redo (History) and Retry missing — share one
// shape: start POST, poll to a terminal phase (the meter shows the way), show
// the outcome, and keep the busy flag honest on success and failure alike.
export function postJob(sessionId, action, payload) {
  const opts = { method: 'POST', timeoutMs: 15000 };
  if (payload) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(payload);
  }
  return jsonApi('/api/sessions/' + encodeURIComponent(sessionId) + '/' + action, opts);
}

// The terminal body of a job whose start answer was `started`. `prefix` words
// the meter per surface ('' | 'Redo' | 'Retry').
export function followJob(sessionId, started, prefix) {
  if (started.phase === 'succeeded') return Promise.resolve(started);
  return pollUntilDone(sessionId, function (b) {
    setProgress(extractProgress(b, prefix));
  });
}

// Run `run()` inside the busy cycle. `begin` / `end` toggle the caller's own
// button; `startLabel` is the meter's first line; `failLabel` heads the
// failure toast ('Extract failed'), and `failStatus` heads the card's error
// line when it differs ('Failed: ' by default).
export async function runTakeJob(opts) {
  if (state.busy) return;
  state.busy = true;
  if (opts.begin) opts.begin();
  setError('');
  setProgress({ label: opts.startLabel, fraction: 0 });
  renderThumbnails();
  try {
    await opts.run();
  } catch (exc) {
    setError((opts.failStatus || 'Failed: ') + (exc.message || exc));
    toast(opts.failLabel + ': ' + (exc.message || exc), 'error');
  } finally {
    state.busy = false;
    if (opts.end) opts.end();
    clearProgress();
    renderThumbnails();
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
    startLabel: 'Reading · starting',
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
    startLabel: 'Retry · starting',
    begin: function () { els.retryMissing.disabled = true; },
    end: function () { els.retryMissing.disabled = false; },
    run: async function () {
      const started = await postJob(sessionId, 'retry-missing');
      const before = (started.missing_photos || []).length;
      const body = await followJob(sessionId, started, 'Retry');
      adoptTake(sessionId, body);
      loadHistory(0);
      const left = state.missingPhotos.length;
      if (body.retry_error) {
        setError('Retry failed: ' + body.retry_error);
        toast('Retry failed: ' + body.retry_error, 'error');
      } else if (!left) {
        toast('All photos read.');
      } else {
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
    const original = els.copyExtracted.innerHTML;
    els.copyExtracted.innerHTML = icon('check') + ' Copied';
    setTimeout(function () {
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

// Show a job's result in the Capture card (Redo from History, Retry missing).
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
  state.result = takeResult(body, 0);
  state.showPhotos = false;
  renderThumbnails();
  renderExtracted();
}

// Start over (New take, or the take's ⋯): an empty take on the Settings
// default model and style.
export function resetTake() {
  dropPhotos();
  state.takeAdopted = false;
  state.sessionId = null;
  state.sessionIdPromise = null;
  state.extracted = '';
  state.missingPhotos = [];
  state.result = null;
  state.showPhotos = false;
  if (state.config) {
    state.model = defaultModel();
    state.promptId = defaultPromptId();
  }
  setError('');
  renderTakeOptions();
  renderThumbnails();
  renderExtracted();
}
