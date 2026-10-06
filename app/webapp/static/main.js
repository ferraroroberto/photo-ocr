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
  closePreview,
  setupDragDrop,
  setupThumbToolbar,
} from './capture.js';
import { extract, copyExtracted, resetTake, renderExtracted, retryMissing } from './extract.js';
import {
  loadHistory,
  cleanAllHistory,
  onSearchInput,
  clearSearch,
  initTakeMenu,
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
initNavTabs({
  storageKey: TAB_KEY,
  onChange: function () { els.settingsPane.hidden = true; },
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
els.retryMissing.addEventListener('click', function () { retryMissing(state.sessionId); });
els.resetBtn.addEventListener('click', resetTake);
// "Save to history" is the vendored switch (the fleet ships no native
// checkboxes for on/off). On = saved, the default; its inverse is the
// session's `incognito` flag, so the API contract is unchanged.
const saveSwitch = switchEl(!state.incognito, {
  label: 'Save to history',
  onToggle: function (saved, btn) {
    state.incognito = !saved;
    setSwitch(btn, saved);
  },
});
els.saveToHistoryHost.appendChild(saveSwitch);
// The row's text is a tap target too. Not a wrapping <label>: a label around
// a button can double-fire its click on WebKit, flipping the switch back.
els.saveToHistoryLabel.addEventListener('click', function () { saveSwitch.click(); });

els.ocrModel.addEventListener('change', function () {
  state.model = els.ocrModel.value;
  localStorage.setItem(MODEL_KEY, state.model);
  updateSaveDirty();
});
els.ocrStyle.addEventListener('change', function () {
  state.promptId = els.ocrStyle.value;
  localStorage.setItem(PROMPT_KEY, state.promptId);
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
    updateSaveDirty();
    toast('Defaults saved.');
  } catch (exc) {
    toast('Save failed: ' + (exc.message || exc), 'error');
  }
});

els.refreshHistory.addEventListener('click', function () {
  // Refresh always drops back to the chronological browse list.
  clearSearch();
  loadHistory(0);
});
els.cleanAll.addEventListener('click', cleanAllHistory);
initTakeMenu();
els.loadMoreHistory.addEventListener('click', function () {
  loadHistory(state.historyOffset + HISTORY_PAGE_SIZE);
});
if (els.historySearch) {
  els.historySearch.addEventListener('input', onSearchInput);
}

els.previewClose.addEventListener('click', closePreview);
els.previewDialog.addEventListener('click', function (ev) {
  if (ev.target === els.previewDialog) closePreview();
});

setupDragDrop();
setupThumbToolbar();
boot();
