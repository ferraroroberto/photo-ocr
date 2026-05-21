/* Photo OCR — entry module: boots the SPA and wires every DOM event.
 *
 * Loaded by index.html as <script type="module">. The other modules
 * expose pure-ish functions; this file is the only place that attaches
 * event listeners and owns the boot sequence. */

'use strict';

import {
  state,
  els,
  toast,
  tokenFromUrl,
  writeToken,
  MODEL_KEY,
  PROMPT_KEY,
  HISTORY_PAGE_SIZE,
} from './state.js';
import { jsonApi, hideLogin } from './api.js';
import {
  handleFilePick,
  renderThumbnails,
  closePreview,
  setupDragDrop,
} from './capture.js';
import { extract, copyExtracted, resetTake, renderExtracted } from './extract.js';
import { loadHistory, cleanAllHistory } from './sessions.js';
import {
  fetchConfig,
  fetchStatus,
  refreshPromptPreview,
  loadVersion,
} from './settings.js';

// ----------------------------------------------------------- boot
async function boot() {
  // Pick up ?token=…
  const fromUrl = tokenFromUrl();
  if (fromUrl) writeToken(fromUrl);

  loadVersion();

  try {
    await fetchConfig();
  } catch (exc) {
    // Auth gate triggers showLogin from the api wrapper; everything
    // else surfaces here.
    if (String(exc.message) !== 'auth required') {
      toast('Boot failed: ' + (exc.message || exc), 'error');
    }
    return;
  }
  await fetchStatus();
  await loadHistory(0);
  renderThumbnails();
  renderExtracted();
}

// ----------------------------------------------------------- wire up
els.loginForm.addEventListener('submit', async function (ev) {
  ev.preventDefault();
  els.loginError.hidden = true;
  const password = els.loginPassword.value;
  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    const body = await res.json().catch(function () { return null; });
    if (!res.ok || !body || !body.token) {
      const msg = (body && body.detail) || 'Login failed';
      els.loginError.textContent = msg;
      els.loginError.hidden = false;
      return;
    }
    writeToken(body.token);
    hideLogin();
    boot();
  } catch (exc) {
    els.loginError.textContent = String(exc.message || exc);
    els.loginError.hidden = false;
  }
});

els.cameraInput.addEventListener('change', function () {
  handleFilePick(els.cameraInput.files);
  els.cameraInput.value = '';
});
els.galleryInput.addEventListener('change', function () {
  handleFilePick(els.galleryInput.files);
  els.galleryInput.value = '';
});
els.extractBtn.addEventListener('click', extract);
els.copyExtracted.addEventListener('click', copyExtracted);
els.resetBtn.addEventListener('click', resetTake);
els.incognitoToggle.addEventListener('change', function () {
  state.incognito = !!els.incognitoToggle.checked;
});

els.ocrModel.addEventListener('change', function () {
  state.model = els.ocrModel.value;
  localStorage.setItem(MODEL_KEY, state.model);
});
els.ocrStyle.addEventListener('change', function () {
  state.promptId = els.ocrStyle.value;
  localStorage.setItem(PROMPT_KEY, state.promptId);
  refreshPromptPreview();
});
els.extracted.addEventListener('input', function () {
  state.extracted = els.extracted.value;
  els.copyExtracted.disabled = !state.extracted;
});

els.saveSettings.addEventListener('click', async function () {
  const patch = {
    ocr_model_default: state.model,
    ocr_prompt_default: state.promptId,
    history_retention_days: Math.max(1, parseInt(els.retentionDays.value, 10) || 30),
    max_photos_per_session: Math.max(1, parseInt(els.maxPhotos.value, 10) || 50),
  };
  try {
    const body = await jsonApi('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    state.config = Object.assign({}, state.config, body.config || {});
    toast('Defaults saved.', 'good');
  } catch (exc) {
    toast('Save failed: ' + (exc.message || exc), 'error');
  }
});

els.refreshHistory.addEventListener('click', function () { loadHistory(0); });
els.cleanAll.addEventListener('click', cleanAllHistory);
els.loadMoreHistory.addEventListener('click', function () {
  loadHistory(state.historyOffset + HISTORY_PAGE_SIZE);
});

els.previewClose.addEventListener('click', closePreview);
els.previewDialog.addEventListener('click', function (ev) {
  if (ev.target === els.previewDialog) closePreview();
});

setupDragDrop();
boot();
