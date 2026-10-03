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
 *   state.model           — selected model alias
 *   state.promptId        — selected prompt id
 *   state.prompts         — [{ id, label, description, system }]
 *   state.config          — full /api/config response
 *   state.busy            — boolean: an extract is in flight
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
export const PROMPT_KEY = 'photo-ocr.promptId';
export const MODEL_KEY = 'photo-ocr.model';
export const THEME_KEY = 'photo-ocr.theme';
export const TAB_KEY = 'photo-ocr.tab';
export const HISTORY_PAGE_SIZE = 10;

export const state = {
  sessionId: null,
  sessionIdPromise: null,
  photos: [],
  extracted: '',
  model: null,
  promptId: null,
  prompts: [],
  config: null,
  busy: false,
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
  thumbStrip: document.getElementById('thumbStrip'),
  cameraInput: document.getElementById('cameraInput'),
  galleryInput: document.getElementById('galleryInput'),
  extractBtn: document.getElementById('extractBtn'),
  captureStatus: document.getElementById('captureStatus'),
  extracted: document.getElementById('extracted'),
  copyExtracted: document.getElementById('copyExtracted'),
  resetBtn: document.getElementById('resetBtn'),
  saveToHistoryHost: document.getElementById('saveToHistoryHost'),
  saveToHistoryLabel: document.getElementById('saveToHistoryLabel'),
  ocrModel: document.getElementById('ocrModel'),
  ocrStyle: document.getElementById('ocrStyle'),
  ocrPromptPreview: document.getElementById('ocrPromptPreview'),
  retentionDays: document.getElementById('retentionDays'),
  maxPhotos: document.getElementById('maxPhotos'),
  saveSettings: document.getElementById('saveSettings'),
  buildReadout: document.getElementById('buildReadout'),
  historyCount: document.getElementById('historyCount'),
  historyList: document.getElementById('historyList'),
  historySearch: document.getElementById('historySearch'),
  refreshHistory: document.getElementById('refreshHistory'),
  cleanAll: document.getElementById('cleanAll'),
  loadMoreHistory: document.getElementById('loadMoreHistory'),
  takeMenu: document.getElementById('takeMenu'),
  takeMenuWhen: document.getElementById('takeMenuWhen'),
  takeRedo: document.getElementById('takeRedo'),
  takeDelete: document.getElementById('takeDelete'),
  previewDialog: document.getElementById('previewDialog'),
  previewImg: document.getElementById('previewImg'),
  previewClose: document.getElementById('previewClose'),
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
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
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
