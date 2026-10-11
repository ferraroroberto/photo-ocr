/* Photo OCR — the Settings panel: config fetch, the default model / style
 * selectors (a take starts from them; the Capture chips change one take),
 * the prompt preview, the dirty-aware Save defaults button, and the
 * build-identity footer line. */

'use strict';

import { state, els, MODEL_KEY, PROMPT_KEY, modelLabel } from './state.js';
import { jsonApi } from './api.js';
import { buildReadoutText } from './_vendored/page-foot/page-foot.js';

// The default a new take starts from: this device's Settings choice, else the
// server's default, else the first available.
export function defaultModel() {
  const cfg = state.config || {};
  return (
    localStorage.getItem(MODEL_KEY) ||
    cfg.ocr_model_default ||
    (cfg.ocr_models_available || [])[0] ||
    'gemini_flash'
  );
}
export function defaultPromptId() {
  const cfg = state.config || {};
  return (
    localStorage.getItem(PROMPT_KEY) ||
    cfg.ocr_prompt_default ||
    (state.prompts[0] && state.prompts[0].id) ||
    'verbatim-merge'
  );
}

// The Settings selects hold the defaults (the take's own choice is the
// Capture chips'), so the preview and Save defaults read the selects.
export function refreshPromptPreview() {
  const selected = els.ocrStyle.value || state.promptId;
  const prompt = (state.prompts || []).find(function (p) { return p.id === selected; });
  els.ocrPromptPreview.value = prompt ? prompt.system : '';
}

export function renderSettings() {
  if (!state.config) return;

  // Models
  els.ocrModel.innerHTML = '';
  (state.config.ocr_models_available || []).forEach(function (m) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = modelLabel(m);
    if (m === state.model) opt.selected = true;
    els.ocrModel.appendChild(opt);
  });

  // Styles
  els.ocrStyle.innerHTML = '';
  (state.prompts || []).forEach(function (p) {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.label || p.id;
    if (p.id === state.promptId) opt.selected = true;
    els.ocrStyle.appendChild(opt);
  });

  refreshPromptPreview();

  els.retentionDays.value = state.config.history_retention_days || 30;
  els.maxPhotos.value = state.config.max_photos_per_session || 50;

  updateSaveDirty();
}

// Save defaults only arms when the current selection actually differs from
// the persisted server defaults — disabled + quiet when clean, enabled +
// accent-tinted when there is something to save (#76).
export function updateSaveDirty() {
  if (!state.config) return;
  const cfg = state.config;
  const dirty =
    els.ocrModel.value !== (cfg.ocr_model_default || '') ||
    els.ocrStyle.value !== (cfg.ocr_prompt_default || '') ||
    (parseInt(els.retentionDays.value, 10) || 0) !==
      (cfg.history_retention_days || 30) ||
    (parseInt(els.maxPhotos.value, 10) || 0) !==
      (cfg.max_photos_per_session || 50);
  els.saveSettings.disabled = !dirty;
  els.saveSettings.classList.toggle('shaded-btn--accent', dirty);
}

export async function fetchConfig() {
  const body = await jsonApi('/api/config');
  state.config = body;
  state.prompts = body.ocr_prompts || [];
  state.model = defaultModel();
  state.promptId = defaultPromptId();
  renderSettings();

  // The search box only appears when the server has the feature on.
  if (els.historySearch) {
    els.historySearch.hidden = !body.search_enabled;
  }
}

// Surface the loaded build in the page footer so "is the phone running
// the current code?" is answerable at a glance from any tab — see issue
// #5. /api/version is auth-exempt, so a plain fetch avoids the login
// overlay even before the user has signed in.
export async function loadVersion() {
  if (!els.buildReadout) return;
  try {
    const res = await fetch('/api/version');
    if (!res.ok) throw new Error(String(res.status));
    const v = await res.json();
    els.buildReadout.textContent = buildReadoutText(v.git_sha || '?', v.built_at || '');
  } catch (_) {
    // Non-critical — leave the footer quiet rather than alarming.
    els.buildReadout.textContent = '';
  }
}
