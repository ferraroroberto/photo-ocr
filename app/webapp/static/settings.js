/* Photo OCR — the Settings pane: config fetch, the Display / Reading /
 * Storage inset groups whose rows save to the server as they change (the
 * default model and style a new take starts from, the prompt that style
 * sends, retention, max photos), and the build-identity footer line. */

'use strict';

import { state, els, toast, modelLabel } from './state.js';
import { jsonApi } from './api.js';
import { renderTakeOptions, styleLabel } from './capture.js';
import { openSheet } from './sheet.js';
import { buildReadoutText } from './_vendored/page-foot/page-foot.js';

// The server holds the defaults. These device-only copies, written before the server held the
// defaults, would shadow it, so boot clears any that are left.
const LEGACY_DEVICE_KEYS = ['photo-ocr.model', 'photo-ocr.promptId'];

// Retention presets for "Keep takes for"; any other stored value is shown
// as-is until a preset replaces it.
const RETENTION_PRESETS = [
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: '1 year' },
];
const MAX_PHOTOS_MIN = 1;
const MAX_PHOTOS_MAX = 100;

// The default a new take starts from: the server's, else the first available.
export function defaultModel() {
  const cfg = state.config || {};
  return cfg.ocr_model_default || (cfg.ocr_models_available || [])[0] || 'gemini_flash';
}
export function defaultPromptId() {
  const cfg = state.config || {};
  return cfg.ocr_prompt_default || (state.prompts[0] && state.prompts[0].id) || 'verbatim-merge';
}

function retentionLabel(days) {
  const preset = RETENTION_PRESETS.find(function (p) { return p.days === days; });
  return preset ? preset.label : days + ' days';
}

export function renderSettings() {
  if (!state.config) return;
  const cfg = state.config;
  els.setModelValue.textContent = modelLabel(defaultModel());
  els.setStyleValue.textContent = styleLabel(defaultPromptId());
  els.setRetentionValue.textContent = retentionLabel(cfg.history_retention_days || 30);
  els.setMaxPhotosValue.textContent = String(cfg.max_photos_per_session || 50);
}

// Write one setting to the server. On success the row shows the new value and
// a quiet toast says so; on failure the row keeps the old value and the error
// toast says why. Resolves whether it saved.
async function saveSetting(patch, message) {
  try {
    const body = await jsonApi('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    state.config = Object.assign({}, state.config, body.config || {});
    renderSettings();
    toast(message);
    return true;
  } catch (exc) {
    renderSettings();
    toast('Save failed: ' + (exc.message || exc), 'error');
    return false;
  }
}

// A take still on the old default follows the new one; a take whose chip was
// changed keeps its own choice.
function followDefault(key, before, after) {
  if (state[key] !== before) return;
  state[key] = after;
  renderTakeOptions();
}

function openModelSheet() {
  const models = (state.config && state.config.ocr_models_available) || [];
  const current = defaultModel();
  openSheet({
    title: 'Default model',
    actions: models.map(function (m) {
      return {
        label: modelLabel(m),
        current: m === current,
        onSelect: async function () {
          if (m === current) return;
          if (await saveSetting({ ocr_model_default: m }, 'Default model: ' + modelLabel(m))) {
            followDefault('model', current, m);
          }
        },
      };
    }),
  });
}

function openStyleSheet() {
  const current = defaultPromptId();
  openSheet({
    title: 'Style',
    actions: (state.prompts || []).map(function (p) {
      return {
        label: p.label || p.id,
        current: p.id === current,
        onSelect: async function () {
          if (p.id === current) return;
          if (await saveSetting({ ocr_prompt_default: p.id }, 'Style: ' + (p.label || p.id))) {
            followDefault('promptId', current, p.id);
          }
        },
      };
    }),
  });
}

// The prompt the default style sends, read-only.
function openPromptSheet() {
  const id = defaultPromptId();
  const prompt = (state.prompts || []).find(function (p) { return p.id === id; });
  const body = document.createElement('div');
  body.className = 'prompt-sheet';
  const meta = document.createElement('p');
  meta.className = 'prompt-sheet-meta';
  meta.textContent = 'What the ' + styleLabel(id) + ' style sends to the model.';
  const text = document.createElement('div');
  text.className = 'prompt-sheet-text';
  text.tabIndex = 0;
  text.setAttribute('role', 'region');
  text.setAttribute('aria-label', 'Prompt');
  text.textContent = prompt ? prompt.system : '';
  body.append(meta, text);
  openSheet({ title: 'Prompt', body: body, className: 'prompt-sheet-dialog' });
}

// "Keep takes for": four presets as one segmented control. A pick saves; the
// raised segment flips only once the server has it. A stored value outside
// the presets raises no segment and is named until a preset replaces it.
function openRetentionSheet() {
  const body = document.createElement('div');
  body.className = 'settings-sheet';
  const note = document.createElement('p');
  note.className = 'settings-sheet-note';
  const group = document.createElement('div');
  group.className = 'segmented';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', 'Keep takes for');
  const paint = function () {
    const days = state.config.history_retention_days || 30;
    const preset = RETENTION_PRESETS.some(function (p) { return p.days === days; });
    note.textContent = preset
      ? 'Older takes are deleted automatically.'
      : 'Now ' + days + ' days. Pick a preset to change it.';
    group.querySelectorAll('.segmented-item').forEach(function (btn) {
      btn.setAttribute('aria-pressed', String(Number(btn.dataset.days) === days));
    });
  };
  RETENTION_PRESETS.forEach(function (p) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'segmented-item';
    btn.dataset.days = String(p.days);
    btn.textContent = p.label;
    btn.addEventListener('click', async function () {
      if (p.days === state.config.history_retention_days) return;
      await saveSetting({ history_retention_days: p.days }, 'Keep takes for ' + p.label);
      paint();
    });
    group.appendChild(btn);
  });
  body.append(group, note);
  openSheet({ title: 'Keep takes for', body: body });
  paint();
}

// "Max photos per take": a number that saves when the field is left (or on
// Enter), inside the 1-100 range; anything else is refused in place.
function openMaxPhotosSheet() {
  const body = document.createElement('div');
  body.className = 'settings-sheet';
  const label = document.createElement('label');
  label.className = 'settings-sheet-field';
  const caption = document.createElement('span');
  caption.textContent = 'Photos per take (' + MAX_PHOTOS_MIN + '–' + MAX_PHOTOS_MAX + ')';
  const input = document.createElement('input');
  input.type = 'number';
  input.inputMode = 'numeric';
  input.min = String(MAX_PHOTOS_MIN);
  input.max = String(MAX_PHOTOS_MAX);
  input.className = 'input-native';
  input.id = 'maxPhotosInput';
  input.value = String(state.config.max_photos_per_session || 50);
  label.append(caption, input);
  const error = document.createElement('p');
  error.className = 'settings-sheet-error';
  error.hidden = true;
  body.append(label, error);

  input.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') input.blur();
  });
  input.addEventListener('change', async function () {
    const n = Number(input.value);
    const saved = state.config.max_photos_per_session || 50;
    if (!Number.isInteger(n) || n < MAX_PHOTOS_MIN || n > MAX_PHOTOS_MAX) {
      error.textContent = 'Use a whole number from ' + MAX_PHOTOS_MIN + ' to ' + MAX_PHOTOS_MAX + '.';
      error.hidden = false;
      input.value = String(saved);
      return;
    }
    error.hidden = true;
    if (n === saved) return;
    if (!(await saveSetting({ max_photos_per_session: n }, 'Max photos per take: ' + n))) {
      input.value = String(saved);
    }
  });
  openSheet({ title: 'Max photos per take', body: body });
}

// Wire the rows once; they read the config when tapped.
export function initSettings() {
  els.setModel.addEventListener('click', openModelSheet);
  els.setStyle.addEventListener('click', openStyleSheet);
  els.setPrompt.addEventListener('click', openPromptSheet);
  els.setRetention.addEventListener('click', openRetentionSheet);
  els.setMaxPhotos.addEventListener('click', openMaxPhotosSheet);
}

export async function fetchConfig() {
  const body = await jsonApi('/api/config');
  state.config = body;
  state.prompts = body.ocr_prompts || [];
  try {
    LEGACY_DEVICE_KEYS.forEach(function (key) { localStorage.removeItem(key); });
  } catch (_) { /* storage blocked: nothing was stored either */ }
  state.model = defaultModel();
  state.promptId = defaultPromptId();
  renderSettings();

  // The search box only appears when the server has the feature on.
  if (els.historySearch) {
    els.historySearch.hidden = !body.search_enabled;
  }
}

// Surface the loaded build in the page footer so "is the phone running
// the current code?" is answerable at a glance from any tab.
// /api/version is auth-exempt, so a plain fetch avoids the login
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
