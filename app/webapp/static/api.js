/* Photo OCR — fetch helpers and the sign-in hook.
 *
 * `api()` attaches the bearer token and routes a 401 to the vendored
 * sign-in sheet; `jsonApi()` adds JSON parsing + error shaping on top.
 */

'use strict';

import { readToken, writeToken } from './state.js';
import { requestSignIn } from './_vendored/sign-in/sign-in.js';

const DEFAULT_TIMEOUT_MS = 45000;

// ----------------------------------------------------------- sign-in
// Every 401 lands here. The sheet hands all callers one promise while it is
// open, so the follow-up runs once per sign-in, not once per failing call.
let onSignedIn = null;
let signInOpen = false;

// main.js passes its boot sequence so this module stays independent of it.
export function setSignInHandler(handler) {
  onSignedIn = handler;
}

function showLogin() {
  if (signInOpen) return;
  signInOpen = true;
  requestSignIn({
    endpoint: '/api/login',
    hint: 'Enter the password to use this photo OCR app.',
    onToken: writeToken,
  }).then(function () {
    signInOpen = false;
    if (onSignedIn) onSignedIn();
  }, function (exc) {
    signInOpen = false;
    console.warn('sign-in failed to open', exc);
  });
}

// ----------------------------------------------------------- fetch
export async function api(path, opts) {
  opts = opts || {};
  const headers = new Headers(opts.headers || {});
  const token = readToken();
  if (token) headers.set('Authorization', 'Bearer ' + token);
  const timeoutMs = opts.timeoutMs == null ? DEFAULT_TIMEOUT_MS : opts.timeoutMs;
  const controller = timeoutMs > 0 ? new AbortController() : null;
  let timeoutId = null;
  if (controller) {
    timeoutId = setTimeout(function () { controller.abort(); }, timeoutMs);
  }
  let res;
  try {
    res = await fetch(
      path,
      Object.assign({}, opts, {
        headers,
        signal: controller ? controller.signal : opts.signal,
      })
    );
  } catch (exc) {
    if (exc && exc.name === 'AbortError') {
      throw new Error('request timed out after ' + Math.round(timeoutMs / 1000) + ' s');
    }
    throw exc;
  } finally {
    if (timeoutId != null) clearTimeout(timeoutId);
  }
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
