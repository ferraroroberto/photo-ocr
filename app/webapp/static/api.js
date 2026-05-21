/* Photo OCR — fetch helpers and the login overlay.
 *
 * `api()` attaches the bearer token and routes a 401 to the login
 * overlay; `jsonApi()` adds JSON parsing + error shaping on top.
 */

'use strict';

import { els, readToken } from './state.js';

// ----------------------------------------------------------- login UI
export function showLogin() {
  if (!els.loginOverlay) return;
  els.loginOverlay.hidden = false;
  els.loginPassword.value = '';
  els.loginPassword.focus();
}
export function hideLogin() {
  if (els.loginOverlay) els.loginOverlay.hidden = true;
}

// ----------------------------------------------------------- fetch
export async function api(path, opts) {
  opts = opts || {};
  const headers = new Headers(opts.headers || {});
  const token = readToken();
  if (token) headers.set('Authorization', 'Bearer ' + token);
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    showLogin();
    throw new Error('auth required');
  }
  return res;
}

export async function jsonApi(path, opts) {
  const res = await api(path, opts);
  let body = null;
  try {
    body = await res.json();
  } catch (_) {
    body = null;
  }
  if (!res.ok) {
    const detail = (body && body.detail) || ('HTTP ' + res.status);
    const err = new Error(detail);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}
