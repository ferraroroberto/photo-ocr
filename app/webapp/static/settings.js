/* Photo OCR — the Settings panel: config + status fetch, the model /
 * style selectors, the prompt preview, and the build-identity line. */

'use strict';

import { state, els, MODEL_KEY, PROMPT_KEY } from './state.js';
import { jsonApi } from './api.js';

export function refreshPromptPreview() {
  const prompt = (state.prompts || []).find(function (p) { return p.id === state.promptId; });
  els.ocrPromptPreview.value = prompt ? prompt.system : '';
}

export function renderSettings() {
  if (!state.config) return;

  // Models
  els.ocrModel.innerHTML = '';
  (state.config.ocr_models_available || []).forEach(function (m) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
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
}

export async function fetchConfig() {
  const body = await jsonApi('/api/config');
  state.config = body;
  state.prompts = body.ocr_prompts || [];
  state.model =
    localStorage.getItem(MODEL_KEY) ||
    body.ocr_model_default ||
    (body.ocr_models_available || [])[0] ||
    'gemini_flash';
  state.promptId =
    localStorage.getItem(PROMPT_KEY) ||
    body.ocr_prompt_default ||
    (state.prompts[0] && state.prompts[0].id) ||
    'verbatim-merge';
  renderSettings();

  // The 🔎 search box only appears when the server has the feature on.
  if (els.historySearch) {
    els.historySearch.hidden = !body.search_enabled;
  }
}

export async function fetchStatus() {
  try {
    const body = await jsonApi('/api/status');
    const hub = body.llm_hub || {};
    els.statusReadout.textContent =
      'Hub: ' + (hub.reachable ? '✅' : '❌') + ' · ' + (hub.base_url || '?');
  } catch (_) {
    els.statusReadout.textContent = '';
  }
}

// Surface the loaded build in the Settings panel so "is the phone
// running the current code?" is answerable at a glance — see issue #5.
// /api/version is auth-exempt, so a plain fetch avoids the login
// overlay even before the user has signed in.
export async function loadVersion() {
  if (!els.buildInfo) return;
  try {
    const res = await fetch('/api/version');
    if (!res.ok) throw new Error(String(res.status));
    const v = await res.json();
    const when = String(v.built_at || '')
      .replace('T', ' ')
      .replace(/(\+00:00|Z)$/, ' UTC');
    els.buildInfo.textContent =
      ('Build: ' + (v.git_sha || '?') + ' · ' + when).trim();
  } catch (_) {
    // Non-critical — leave a quiet placeholder rather than alarming.
    els.buildInfo.textContent = 'Build: unavailable';
  }
}
