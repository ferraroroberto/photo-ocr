/* Photo OCR — shared state, DOM handles, and cross-cutting primitives.
 *
 * State machine (single source of truth):
 *
 *   state.sessionId       — server-assigned on first photo upload (lazy)
 *   state.sessionIdPromise — in-flight ensureSession() POST, memoized so
 *                            concurrent multi-select uploads share one session
 *   state.photos          — [{ clientId, seq, file, previewUrl, status,
 *                              error, warnings, warningDismissed }]
 *   state.extracted       — current extracted text
 *   state.result          — the take's last outcome, or null before one:
 *                           { model, seconds, cached, saved } (its meta line)
 *   state.showPhotos      — boolean: a take with a result shows its photos
 *                           ("N photos ›") instead of the text
 *   state.missingPhotos   — filenames the current text could not read
 *                           (drives the Retry missing action)
 *   state.photosChanged   — boolean: the strip was reordered, trimmed or added
 *                           to since the text was read, so the unread names no
 *                           longer point at the same photos (hides Retry
 *                           missing; Extract again / Redo re-read the take)
 *   state.model           — this take's model alias (the Capture chip;
 *                           a reset take starts from the Settings default)
 *   state.promptId        — this take's prompt id (same)
 *   state.prompts         — [{ id, label, description, system }]
 *   state.config          — full /api/config response
 *   state.busy            — boolean: an extract, redo or retry is in flight
 *   state.takeAdopted     — boolean: sessionId is a History take whose result
 *                           was pulled into the Capture card (Redo / Retry
 *                           missing); the strip does not hold its photos, so
 *                           the next added photo starts a fresh take
 *   state.incognito       — boolean: don't save this take to history (the
 *                           Save to history switch shows its inverse)
 *   state.searchQuery     — active archive-search phrase ('' = browse mode)
 *   state.searchResults   — [{ session_id, created_at, model, snippet }]
 *
 * Auth: a bearer token is stored in localStorage under TOKEN_KEY. The
 * page extracts it from ?token=… on first load (then strips it from
 * the visible URL). On 401, api.js shows the login overlay; the
 * password is swapped for the token via POST /api/login.
 */

'use strict';

export const TOKEN_KEY = 'photo-ocr.token';
export const THEME_KEY = 'photo-ocr.theme';
export const TAB_KEY = 'photo-ocr.tab';
export const HISTORY_PAGE_SIZE = 10;

export const state = {
  sessionId: null,
  sessionIdPromise: null,
  photos: [],
  extracted: '',
  result: null,
  showPhotos: false,
  missingPhotos: [],
  photosChanged: false,
  model: null,
  promptId: null,
  prompts: [],
  config: null,
  busy: false,
  takeAdopted: false,
  incognito: false,
  historyOffset: 0,
  historyItems: [],
  historyTotal: 0,
  searchQuery: '',
  searchResults: [],
};

// ----------------------------------------------------------------- DOM
export const els = {
  themeToggles: document.querySelectorAll('.theme-toggle'),
  settingsGears: document.querySelectorAll('.home-settings'),
  settingsPane: document.getElementById('paneSettings'),
  captureHeadline: document.getElementById('captureHeadline'),
  takeEmpty: document.getElementById('takeEmpty'),
  takeBuild: document.getElementById('takeBuild'),
  takeStarts: document.querySelectorAll('.take-start'),
  thumbStrip: document.getElementById('thumbStrip'),
  cameraInput: document.getElementById('cameraInput'),
  galleryInput: document.getElementById('galleryInput'),
  extractBtn: document.getElementById('extractBtn'),
  takeMore: document.getElementById('takeMenuBtn'),
  takeModel: document.getElementById('takeModel'),
  takeStyle: document.getElementById('takeStyle'),
  extractMeter: document.getElementById('extractMeter'),
  extractMeterBar: document.querySelector('#extractMeter .meter'),
  extractMeterFill: document.querySelector('#extractMeter .meter-fill'),
  extractMeterLabel: document.querySelector('#extractMeter .take-meter-label'),
  extractLabel: document.querySelector('#extractBtn .extract-label'),
  showText: document.getElementById('showText'),
  takeResult: document.getElementById('takeResult'),
  showPhotos: document.getElementById('showPhotos'),
  showPhotosLabel: document.getElementById('showPhotosLabel'),
  resultMeta: document.getElementById('resultMeta'),
  resultEmpty: document.getElementById('resultEmpty'),
  extracted: document.getElementById('extracted'),
  copyExtracted: document.getElementById('copyExtracted'),
  newTake: document.getElementById('newTake'),
  retryMissing: document.getElementById('retryMissing'),
  retryMissingLabel: document.getElementById('retryMissingLabel'),
  takeError: document.getElementById('takeError'),
  saveToHistoryHost: document.getElementById('saveToHistoryHost'),
  saveToHistoryLabel: document.getElementById('saveToHistoryLabel'),
  setModel: document.getElementById('setModel'),
  setModelValue: document.getElementById('setModelValue'),
  setStyle: document.getElementById('setStyle'),
  setStyleValue: document.getElementById('setStyleValue'),
  setPrompt: document.getElementById('setPrompt'),
  setRetention: document.getElementById('setRetention'),
  setRetentionValue: document.getElementById('setRetentionValue'),
  setMaxPhotos: document.getElementById('setMaxPhotos'),
  setMaxPhotosValue: document.getElementById('setMaxPhotosValue'),
  buildReadout: document.getElementById('buildReadout'),
  historyHeadline: document.getElementById('historyHeadline'),
  historyList: document.getElementById('historyList'),
  historySearch: document.getElementById('historySearch'),
  cleanAll: document.getElementById('cleanAll'),
  loadMoreHistory: document.getElementById('loadMoreHistory'),
  loadMoreLabel: document.getElementById('loadMoreLabel'),
  loginOverlay: document.getElementById('loginOverlay'),
  loginForm: document.getElementById('loginForm'),
  loginPassword: document.getElementById('loginPassword'),
  loginError: document.getElementById('loginError'),
};

// ----------------------------------------------------------- auth utils
export function tokenFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const t = (params.get('token') || '').trim();
  if (!t) return null;
  params.delete('token');
  const newQuery = params.toString();
  const newUrl =
    window.location.pathname +
    (newQuery ? '?' + newQuery : '') +
    window.location.hash;
  window.history.replaceState({}, '', newUrl);
  return t;
}
export function readToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}
export function writeToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
}

// ----------------------------------------------------------- toasts
// The one fleet toast (neutral frosted; only an error tints). Re-exported so
// every module keeps importing `toast` from here.
export { showToast as toast } from './_vendored/toast/toast.js';

// ----------------------------------------------------------- model labels
// Model aliases are config ids (`gemini_flash`); people read "Gemini Flash".
// Display only — the id stays the value sent to the server.
export function modelLabel(id) {
  return String(id)
    .split('_')
    .map(function (w) { return w ? w.charAt(0).toUpperCase() + w.slice(1) : w; })
    .join(' ');
}

// ----------------------------------------------------------- clipboard
// The one copy path (Capture result and History row). Rejects when nothing
// could be copied, so a caller's success toast is always honest.
export async function copyText(txt) {
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
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    if (!ok) throw new Error('clipboard unavailable');
  }
}
