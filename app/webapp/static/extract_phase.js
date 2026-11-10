/* Photo OCR — the extraction phase of a take as the client reads it. The
 * server owns the machine (src/extract_state.py, docs/extract-phase-state.md);
 * this module is the one place the client names its phases, maps a status body
 * to one, and checks that successive polls only move forward. The strings are
 * the wire values. Framework-free and DOM-free so a Node test can drive it. */

'use strict';

export const ExtractPhase = Object.freeze({
  IDLE: 'idle', // never read; nothing queued
  QUEUED: 'queued', // accepted, the job has not started
  RUNNING: 'running', // reading photos
  MERGING: 'merging', // chunks read, writing the text
  SUCCEEDED: 'succeeded', // the text on disk is current
  FAILED: 'failed', // the last run failed; `error` says why
});

// Position of the live phases along one job; a poll never sees one go back.
const ACTIVE_ORDER = Object.freeze({
  [ExtractPhase.QUEUED]: 0,
  [ExtractPhase.RUNNING]: 1,
  [ExtractPhase.MERGING]: 2,
});

export class IllegalExtractPhaseMove extends Error {}

// The phase of a status body. A body with no phase, or one this build does not
// know, is idle: the poll loop keeps waiting and shows no progress text.
export function deriveExtractPhase(body) {
  const stored = body ? body.phase : undefined;
  const known = Object.keys(ExtractPhase).map(function (k) { return ExtractPhase[k]; });
  return known.indexOf(stored) >= 0 ? stored : ExtractPhase.IDLE;
}

export function isExtractDone(body) {
  return deriveExtractPhase(body) === ExtractPhase.SUCCEEDED;
}

export function isExtractFailed(body) {
  return deriveExtractPhase(body) === ExtractPhase.FAILED;
}

// Poll after poll of one job: a live phase never moves to an earlier live
// phase. Returns `next`; throws before the caller acts on an impossible answer.
export function requireForwardPhase(previous, next) {
  const from = ACTIVE_ORDER[previous];
  const to = ACTIVE_ORDER[next];
  if (from !== undefined && to !== undefined && to < from) {
    throw new IllegalExtractPhaseMove('extraction moved back from ' + previous + ' to ' + next);
  }
  return next;
}
