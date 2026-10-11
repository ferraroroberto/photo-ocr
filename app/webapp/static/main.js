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
  THEME_KEY,
  TAB_KEY,
  HISTORY_PAGE_SIZE,
} from './state.js';
import { jsonApi, hideLogin } from './api.js';
import {
  handleFilePick,
  renderThumbnails,
  renderTakeOptions,
  renderHeadline,
  openModelPicker,
  openStylePicker,
  openTakeMoreSheet,
  showPhotosView,
  setupDragDrop,
} from './capture.js';
import { extract, copyExtracted, renderExtracted, resetTake, retryMissing } from './extract.js';
import {
  loadHistory,
  cleanAllHistory,
  onSearchInput,
  refreshHistoryView,
} from './sessions.js';
import {
  fetchConfig,
  refreshPromptPreview,
  loadVersion,
  updateSaveDirty,
} from './settings.js';
import { icon } from './_vendored/icons/icons.js';
import { initNavTabs } from './_vendored/nav/nav-tabs.js';
import { bindTextSize } from './_vendored/text-size/text-size.js';
import { switchEl, setSwitch } from './_vendored/switch/switch.js';

// --------------------------------------------------------------- theme toggle
// Same feature as home-automation / app-launcher: the pre-paint script in
// index.html applies the stored theme (or the system preference) before first
// render; this block owns the sun/moon toggle in every pane's header + persistence.
function applyTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  for (const toggle of els.themeToggles) toggle.innerHTML = icon(dark ? 'sun' : 'moon');
  localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light');
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme !== 'dark');
}

(function initTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(stored ? stored === 'dark' : prefersDark);
})();

for (const toggle of els.themeToggles) toggle.addEventListener('click', toggleTheme);

// Text size (A11Y-02): the zoom lock's escape. The pre-paint script in index.html
// stamps html[data-textsize]; this wires the Settings control to it.
bindTextSize(document.getElementById('textSizeControl'), 'photo-ocr');

// --------------------------------------------------------------- bottom tabs
// Vendored fleet nav (see _vendored/nav/README.md): discovers the tabs
// from the markup, persists the active one so the installed PWA reopens where
// you left it, and owns the iOS pinning behaviour. Settings is no tab (fleet
// NAV-03): every pane's header gear opens it, and choosing any tab leaves it,
// so onChange hides the pane.
// Opening History reloads it (there is no Refresh button); before boot has
// the config the boot sequence loads it instead.
initNavTabs({
  storageKey: TAB_KEY,
  onChange: function (tab) {
    els.settingsPane.hidden = true;
    if (tab === 'history' && state.config) refreshHistoryView();
  },
});

// Show the Settings pane over the current tab. The vendored nav only manages
// its own tabs' panes, so hide them here and leave no tab selected; the nav's
// next setTab (any tab tap) shows that tab's pane again. The stored tab is
// untouched, so a reload from Settings reopens the last real tab.
function openSettings() {
  document.querySelectorAll('main.app > section.pane').forEach(function (pane) {
    pane.hidden = pane !== els.settingsPane;
  });
  const nav = document.querySelector('nav.tabs');
  nav.querySelectorAll('.tab').forEach(function (tab) {
    tab.classList.remove('active');
    tab.setAttribute('aria-selected', 'false');
  });
  nav.dataset.activeTab = 'settings';
  const scroller = document.querySelector('.app');
  if (scroller) scroller.scrollTop = 0;
  window.scrollTo(0, 0);
}
for (const gear of els.settingsGears) gear.addEventListener('click', openSettings);

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
  await loadHistory(0);
  renderTakeOptions();
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
// The empty take's two starts are labels for the hidden file inputs (the
// most reliable picker trigger on iOS); Enter / Space make them keyboard-
// operable like the buttons they look like.
els.takeStarts.forEach(function (start) {
  start.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    start.click();
  });
});
els.extractBtn.addEventListener('click', extract);
els.takeMore.addEventListener('click', openTakeMoreSheet);
els.takeModel.addEventListener('click', openModelPicker);
els.takeStyle.addEventListener('click', openStylePicker);
els.copyExtracted.addEventListener('click', copyExtracted);
els.newTake.addEventListener('click', resetTake);
els.showPhotos.addEventListener('click', function () { showPhotosView(true); });
els.showText.addEventListener('click', function () { showPhotosView(false); });
els.retryMissing.addEventListener('click', function () { retryMissing(state.sessionId); });
// "Save to history" is the vendored switch (the fleet ships no native
// checkboxes for on/off). On = saved, the default; its inverse is the
// session's `incognito` flag. The take's session is created on its first
// upload, before this switch is on screen, so a flip on a live take is
// written to it; a failed write puts the switch back.
const saveSwitch = switchEl(!state.incognito, {
  label: 'Save to history',
  onToggle: async function (saved, btn) {
    state.incognito = !saved;
    setSwitch(btn, saved);
    renderHeadline();
    if (!state.sessionId || state.takeAdopted) return;
    try {
      await jsonApi('/api/sessions/' + encodeURIComponent(state.sessionId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ incognito: !saved }),
      });
    } catch (exc) {
      state.incognito = saved;
      setSwitch(btn, !saved);
      renderHeadline();
      toast('Save to history failed: ' + (exc.message || exc), 'error');
    }
  },
});
els.saveToHistoryHost.appendChild(saveSwitch);
// The row's text is a tap target too. Not a wrapping <label>: a label around
// a button can double-fire its click on WebKit, flipping the switch back.
els.saveToHistoryLabel.addEventListener('click', function () { saveSwitch.click(); });

// The Settings selects set this device's default, and the take in progress
// follows it (as before the Capture chips existed).
els.ocrModel.addEventListener('change', function () {
  state.model = els.ocrModel.value;
  localStorage.setItem(MODEL_KEY, state.model);
  renderTakeOptions();
  updateSaveDirty();
});
els.ocrStyle.addEventListener('change', function () {
  state.promptId = els.ocrStyle.value;
  localStorage.setItem(PROMPT_KEY, state.promptId);
  renderTakeOptions();
  refreshPromptPreview();
  updateSaveDirty();
});
els.retentionDays.addEventListener('input', updateSaveDirty);
els.maxPhotos.addEventListener('input', updateSaveDirty);
els.extracted.addEventListener('input', function () {
  state.extracted = els.extracted.value;
  els.copyExtracted.disabled = !state.extracted;
});

els.saveSettings.addEventListener('click', async function () {
  const patch = {
    ocr_model_default: els.ocrModel.value,
    ocr_prompt_default: els.ocrStyle.value,
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
    updateSaveDirty();
    toast('Defaults saved.');
  } catch (exc) {
    toast('Save failed: ' + (exc.message || exc), 'error');
  }
});

els.cleanAll.addEventListener('click', cleanAllHistory);
els.loadMoreHistory.addEventListener('click', function () {
  loadHistory(state.historyOffset + HISTORY_PAGE_SIZE);
});
if (els.historySearch) {
  els.historySearch.addEventListener('input', onSearchInput);
}

setupDragDrop();
boot();
