/* Photo OCR — the shared extract/redo poll-until-done loop. Both the
 * fresh-extract path (extract.js) and the History redo path (sessions.js)
 * drive the same async OCR job: POST to start, then GET /extract/status
 * once a second until the job reaches a terminal phase. Keeping the loop
 * — and the progress-line wording — here is the single source of truth, so
 * the two surfaces can never drift on either the state machine or the
 * status strings the user sees. */

'use strict';

import { jsonApi } from './api.js';

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

// Progress for a non-terminal extract phase, as the Capture meter shows it:
// `{ label, fraction }`. The job counts parts (chunks of photos), not photos,
// so the wording never claims a photo count. `prefix` is '' for a fresh
// extract, 'Redo' or 'Retry', so every surface words progress identically
// instead of maintaining parallel strings.
export function extractProgress(body, prefix) {
  const lead = prefix ? prefix + ' · ' : 'Reading · ';
  const total = body.chunks_total || 0;
  const done = body.chunks_done || 0;
  if (body.phase === 'queued') {
    return { label: lead + 'queued', fraction: 0 };
  }
  if (body.phase === 'running') {
    const current = total ? Math.min(done + 1, total) : 1;
    return {
      label: lead + 'part ' + current + ' of ' + (total || 1),
      fraction: total ? done / total : 0,
    };
  }
  if (body.phase === 'merging') {
    return { label: lead + 'merging the parts', fraction: 1 };
  }
  return { label: '', fraction: 0 };
}

// Poll /extract/status once a second until the OCR job finishes. Calls
// `onStatus(body)` for every non-terminal poll so the caller can update
// the UI, returns the succeeded body, and throws on a failed phase.
export async function pollUntilDone(sessionId, onStatus) {
  while (true) {
    const body = await jsonApi(
      '/api/sessions/' + encodeURIComponent(sessionId) + '/extract/status',
      { timeoutMs: 15000 }
    );
    if (body.phase === 'failed') {
      throw new Error(body.error || 'extract failed');
    }
    if (body.phase === 'succeeded') return body;
    if (onStatus) onStatus(body);
    await sleep(1000);
  }
}
