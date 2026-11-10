/* Photo OCR — the upload lifecycle of one photo in the Capture strip,
 * declared once. A photo is `pending` when added, `uploading` while its POST
 * is in flight, then `ready` (the server has it, `seq` says where) or `failed`
 * (`error` says why). The strings are also the CSS classes on the thumbnail.
 * Every reader asks this module; none spells a status or tests a pair of them.
 * Framework-free and DOM-free so a Node test can drive it. */

'use strict';

export const PhotoStatus = Object.freeze({
  PENDING: 'pending', // added, upload not started
  UPLOADING: 'uploading', // POST in flight
  READY: 'ready', // the server holds it
  FAILED: 'failed', // the upload failed; `photo.error` says why
});

// Legal moves. A photo that is ready or failed is final: retaking makes a new
// photo, it does not rewind this one.
const LEGAL_MOVES = Object.freeze({
  [PhotoStatus.PENDING]: [PhotoStatus.UPLOADING],
  [PhotoStatus.UPLOADING]: [PhotoStatus.READY, PhotoStatus.FAILED],
  [PhotoStatus.READY]: [],
  [PhotoStatus.FAILED]: [],
});

export class IllegalPhotoTransition extends Error {}

function isKnown(status) {
  return Object.prototype.hasOwnProperty.call(LEGAL_MOVES, status);
}

// The status of a photo. One never given a status reads as pending; a string
// this build does not know is refused rather than rendered as something else.
export function derivePhotoStatus(photo) {
  const stored = photo.status;
  if (stored === undefined || stored === null || stored === '') return PhotoStatus.PENDING;
  if (!isKnown(stored)) throw new IllegalPhotoTransition('unknown photo status: ' + stored);
  return stored;
}

// The only writer of `photo.status`. A move the table does not list throws
// before anything is stored.
export function setPhotoStatus(photo, next) {
  const current = derivePhotoStatus(photo);
  if (!isKnown(next)) throw new IllegalPhotoTransition('unknown photo status: ' + next);
  if (current !== next && LEGAL_MOVES[current].indexOf(next) < 0) {
    throw new IllegalPhotoTransition('a photo cannot move from ' + current + ' to ' + next);
  }
  photo.status = next;
}

// The status a photo is created with.
export function newPhotoStatus() {
  return PhotoStatus.PENDING;
}

// The server holds the photo.
export function isPhotoReady(photo) {
  return derivePhotoStatus(photo) === PhotoStatus.READY;
}

export function isPhotoFailed(photo) {
  return derivePhotoStatus(photo) === PhotoStatus.FAILED;
}

// Still on its way up: Extract must wait for it.
export function isPhotoUploading(photo) {
  const status = derivePhotoStatus(photo);
  return status === PhotoStatus.PENDING || status === PhotoStatus.UPLOADING;
}
