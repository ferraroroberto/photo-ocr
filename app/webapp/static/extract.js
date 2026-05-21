/* Photo OCR — the extract action, the result textarea, copy-to-
 * clipboard, and resetting the current take. */

'use strict';

import { state, els, toast } from './state.js';
import { jsonApi } from './api.js';
import { renderThumbnails, setStatus } from './capture.js';
import { loadHistory } from './sessions.js';

export function renderExtracted() {
  els.extracted.value = state.extracted || '';
  els.copyExtracted.disabled = !state.extracted;
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
  state.busy = true;
  els.extractBtn.classList.add('busy');
  els.extractBtn.disabled = true;
  setStatus(
    'LLM hub → ' + state.model + ' · extracting from ' + readyPhotos.length + ' photo(s)…'
  );

  const t0 = Date.now();
  try {
    const body = await jsonApi(
      '/api/sessions/' + encodeURIComponent(state.sessionId) + '/extract',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: state.model, prompt_id: state.promptId }),
      }
    );
    state.extracted = body.extracted || '';
    renderExtracted();
    const seconds = ((Date.now() - t0) / 1000).toFixed(1);
    if (body.reused) {
      setStatus('Already extracted — showing cached result · ' + seconds + ' s');
    } else if (!state.extracted) {
      setStatus('No readable text detected · ' + seconds + ' s');
    } else {
      setStatus('Done in ' + seconds + ' s — tap Copy');
    }
    loadHistory(0);
  } catch (exc) {
    setStatus('❌ ' + (exc.message || exc));
    toast('Extract failed: ' + (exc.message || exc), 'error');
  } finally {
    state.busy = false;
    els.extractBtn.classList.remove('busy');
    renderThumbnails();
  }
}

// ----------------------------------------------------------- copy
export async function copyExtracted() {
  const txt = state.extracted || '';
  if (!txt) return;
  try {
    // Prefer the ClipboardItem path with text/plain only — voice-transcriber's
    // troubleshooting table flags styled-DOM leakage with the writeText
    // path on some Safari versions; force the MIME explicitly.
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      const blob = new Blob([txt], { type: 'text/plain' });
      const item = new ClipboardItem({ 'text/plain': blob });
      await navigator.clipboard.write([item]);
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(txt);
    } else {
      // Fallback for very old browsers.
      const ta = document.createElement('textarea');
      ta.value = txt;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    els.copyExtracted.classList.add('copied');
    const original = els.copyExtracted.textContent;
    els.copyExtracted.textContent = '✓ Copied';
    setTimeout(function () {
      els.copyExtracted.classList.remove('copied');
      els.copyExtracted.textContent = original;
    }, 1200);
  } catch (exc) {
    toast('Copy failed: ' + (exc.message || exc), 'error');
  }
}

// ----------------------------------------------------------- reset
export function resetTake() {
  state.photos.forEach(function (p) {
    if (p.previewUrl) {
      try { URL.revokeObjectURL(p.previewUrl); } catch (_) {}
    }
  });
  state.photos = [];
  state.sessionId = null;
  state.extracted = '';
  renderThumbnails();
  renderExtracted();
  setStatus('Add a photo to begin');
}
