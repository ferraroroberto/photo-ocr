/* Photo OCR — the History panel: search first, then the takes grouped under
 * date overlines, paged with "Load more (N left)". A row tap copies the take;
 * its ⋯ opens the take sheet (the text, Copy, Retry, Redo with…, Delete).
 * Also the delete-all action Settings › Storage calls. */

'use strict';

import { state, els, toast, HISTORY_PAGE_SIZE, modelLabel, copyText } from './state.js';
import { jsonApi } from './api.js';
import { adoptTake, followJob, postJob, retryMissing, runTakeJob } from './extract.js';
import { defaultPromptId } from './settings.js';
import { icon } from './_vendored/icons/icons.js';
import { emptyStateEl } from './_vendored/empty-state/empty-state.js';
import { confirmSheet, openSheet } from './sheet.js';

export async function loadHistory(offset) {
  state.historyOffset = offset || 0;
  try {
    const body = await jsonApi(
      '/api/sessions?limit=' +
        HISTORY_PAGE_SIZE +
        '&offset=' +
        state.historyOffset
    );
    if (state.historyOffset === 0) {
      state.historyItems = body.sessions || [];
    } else {
      state.historyItems = state.historyItems.concat(body.sessions || []);
    }
    state.historyTotal = body.total || 0;
    renderHistory();
  } catch (exc) {
    toast('History load failed: ' + (exc.message || exc), 'error');
  }
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}

// FTS5 snippet() wraps each match in [ … ]; turn those into <mark>.
// Stray brackets in the OCR text can mis-highlight — purely cosmetic.
function highlightSnippet(snip) {
  return escapeHtml(snip).replace(/\[([^[\]]*)\]/g, '<mark>$1</mark>');
}

// ----------------------------------------------------------- dates
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function pad(n) { return String(n).padStart(2, '0'); }

// Whole days between the take's day and today (0 = today), or null when the
// date can't be read.
function daysAgo(iso) {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return null;
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((today - day) / 86400000);
}

// The overline a take sits under: Today / Yesterday / This week / its month.
// Pure in the take's own date, so the groups stay stable across Load more.
function dateGroup(iso) {
  const days = daysAgo(iso);
  if (days === null) return 'Earlier';
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return 'This week';
  const d = new Date(iso);
  return MONTHS_LONG[d.getMonth()] + ' ' + d.getFullYear();
}

// The row's "when", short because its overline already says the day or month.
function rowWhen(iso) {
  const days = daysAgo(iso);
  if (days === null) return '?';
  const d = new Date(iso);
  const time = pad(d.getHours()) + ':' + pad(d.getMinutes());
  if (days <= 1) return time;
  if (days < 7) return WEEKDAYS[d.getDay()] + ' ' + time;
  return d.getDate() + ' ' + MONTHS[d.getMonth()];
}

// The take sheet's title: the full "when".
function fullWhen(iso) {
  const days = daysAgo(iso);
  if (days === null) return 'Take';
  const d = new Date(iso);
  const time = pad(d.getHours()) + ':' + pad(d.getMinutes());
  if (days <= 0) return 'Today, ' + time;
  if (days === 1) return 'Yesterday, ' + time;
  return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear() + ', ' + time;
}

// ----------------------------------------------------------- rows
function chipEl(text, tone) {
  const chip = document.createElement('span');
  chip.className = 'chip';
  chip.dataset.tone = tone;
  chip.textContent = text;
  return chip;
}

// What a take's row and sheet know about it. Browse rows carry everything;
// a search hit carries no photo count, duration or unread count (they are
// left undefined, never invented, and the sheet asks the server).
function browseTake(s) {
  return {
    session_id: s.session_id,
    created_at: s.created_at,
    photos: s.photo_count,
    model: s.model,
    prompt_id: s.prompt_id,
    duration: s.extract_duration_s,
    missing: ((s.extract_progress || {}).missing_photos || []).length,
    failed: !!s.error,
    source: s.source,
  };
}

function searchTake(r) {
  return {
    session_id: r.session_id,
    created_at: r.created_at,
    photos: undefined,
    model: r.model,
    prompt_id: undefined,
    duration: undefined,
    missing: undefined,
    failed: false,
    source: r.source,
  };
}

// The exception chips, in one place for the row and the sheet: unread
// (attention), failed (danger), the calling app (neutral). Normal takes none.
function takeChips(take) {
  const chips = [];
  if (take.missing) chips.push(chipEl(take.missing + ' unread', 'attention'));
  if (take.failed) chips.push(chipEl('Failed', 'danger'));
  // Externally sourced takes (app-launcher, api, …) keep History an
  // attributable cross-fleet audit trail; the PWA's own ("webapp") is plain.
  if (take.source && take.source !== 'webapp') chips.push(chipEl(take.source, 'neutral'));
  return chips;
}

function photosText(n) {
  return n + (n === 1 ? ' photo' : ' photos');
}

// Shared row for the browse list and the search results — one DOM shape, two
// data sources. One take is one action-row (design.md): tap the row to copy
// it, one trailing ⋯ for the take sheet. The text (or the failure) leads; the
// meta line is when, the exception chips, then photos and model.
function makeSessionRow(take, titleText, isSnippet) {
  const li = document.createElement('li');
  li.className = 'action-row history-item';

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'action-row-main';
  main.title = 'Copy this take';
  main.addEventListener('click', function () { copyHistoryEntry(take); });

  const title = document.createElement('span');
  title.className = 'action-row-title preview';
  if (isSnippet) {
    title.innerHTML = highlightSnippet(titleText);
  } else {
    title.textContent = titleText;
  }

  const meta = document.createElement('span');
  meta.className = 'action-row-meta';
  meta.append(rowWhen(take.created_at));
  takeChips(take).forEach(function (chip) { meta.append(' ', chip); });
  const rest = [];
  if (take.photos !== undefined) rest.push(photosText(take.photos));
  rest.push(take.model ? modelLabel(take.model) : '—');
  meta.append(' · ' + rest.join(' · '));
  main.append(title, meta);

  const more = document.createElement('button');
  more.type = 'button';
  more.className = 'icon-button action-row-kebab';
  more.setAttribute('aria-label', 'More actions');
  more.setAttribute('aria-haspopup', 'dialog');
  more.innerHTML = icon('ellipsis-vertical');
  more.addEventListener('click', function () { openTakeSheet(take); });

  li.append(main, more);
  return li;
}

function groupHeader(label) {
  const li = document.createElement('li');
  li.className = 'history-group';
  li.setAttribute('role', 'presentation');
  li.textContent = label;
  return li;
}

// The History header names exceptions only: how many of the loaded takes are
// partly read (it counts what is loaded, so it never claims more). Empty
// otherwise, and in search mode (hits don't know).
function renderHistoryHeadline() {
  els.historyHeadline.innerHTML = '';
  if (state.searchQuery) return;
  const partly = state.historyItems.filter(function (s) {
    return ((s.extract_progress || {}).missing_photos || []).length > 0;
  }).length;
  if (!partly) return;
  const part = document.createElement('span');
  part.className = 'headline-part';
  part.dataset.tone = 'attention';
  part.textContent = partly + ' partly read';
  els.historyHeadline.appendChild(part);
}

function renderHistory() {
  // Search mode and browse mode share the same list element.
  if (state.searchQuery) {
    renderSearchResults();
    return;
  }
  renderHistoryHeadline();
  els.historyList.innerHTML = '';
  if (els.historySearch) {
    els.historySearch.placeholder = state.historyTotal
      ? 'Search ' + state.historyTotal + (state.historyTotal === 1 ? ' saved take…' : ' saved takes…')
      : 'Search extracted text…';
  }
  if (!state.historyItems.length) {
    // Canonical fleet empty-state block, naming the way forward.
    const li = document.createElement('li');
    const empty = emptyStateEl('history', 'No saved takes', {
      actionLabel: 'Capture a take',
      onAction: function () { document.getElementById('tabCapture').click(); },
    });
    const days = state.config && state.config.history_retention_days;
    if (days) {
      const hint = document.createElement('p');
      hint.className = 'empty-state-hint';
      hint.textContent = 'With Save to history on, takes are kept here for ' + days + ' days.';
      empty.insertBefore(hint, empty.querySelector('.empty-state-action'));
    }
    li.appendChild(empty);
    els.historyList.appendChild(li);
  }
  let group = null;
  state.historyItems.forEach(function (s) {
    const label = dateGroup(s.created_at);
    if (label !== group) {
      els.historyList.appendChild(groupHeader(label));
      group = label;
    }
    els.historyList.appendChild(
      makeSessionRow(browseTake(s), s.error || s.extracted_preview || '(no text)', false)
    );
  });
  const left = state.historyTotal - state.historyItems.length;
  els.loadMoreHistory.hidden = left <= 0;
  els.loadMoreLabel.textContent = 'Load more (' + left + ' left)';
}

function renderSearchResults() {
  const results = state.searchResults || [];
  renderHistoryHeadline();
  els.historyList.innerHTML = '';
  if (!results.length) {
    const li = document.createElement('li');
    li.appendChild(
      emptyStateEl('history', 'No matches for “' + state.searchQuery + '”', {
        actionLabel: 'Clear search',
        onAction: function () {
          clearSearch();
          renderHistory();
          if (els.historySearch) els.historySearch.focus();
        },
      })
    );
    els.historyList.appendChild(li);
  } else {
    // Ranked by relevance, so no date overlines.
    results.forEach(function (r) {
      els.historyList.appendChild(makeSessionRow(searchTake(r), r.snippet || '(match)', true));
    });
  }
  // Search returns a single ranked page — no incremental loading.
  els.loadMoreHistory.hidden = true;
}

// Re-render the history panel after a mutation or on opening the tab,
// staying in whichever mode (search vs. browse) the user is in.
export function refreshHistoryView() {
  if (state.searchQuery) {
    runSearch(state.searchQuery);
  } else {
    loadHistory(0);
  }
}

let searchTimer = null;

async function runSearch(q) {
  try {
    const body = await jsonApi(
      '/api/search?q=' + encodeURIComponent(q) + '&limit=25'
    );
    state.searchQuery = q;
    state.searchResults = body.results || [];
    renderHistory();
  } catch (exc) {
    toast('Search failed: ' + (exc.message || exc), 'error');
  }
}

// Debounced handler for the search input. Empty query restores browse mode.
export function onSearchInput() {
  const q = (els.historySearch.value || '').trim();
  if (searchTimer) clearTimeout(searchTimer);
  if (!q) {
    state.searchQuery = '';
    state.searchResults = [];
    renderHistory();
    return;
  }
  searchTimer = setTimeout(function () { runSearch(q); }, 250);
}

// Drop out of search mode (the no-matches state's Clear search).
function clearSearch() {
  if (els.historySearch) els.historySearch.value = '';
  if (searchTimer) clearTimeout(searchTimer);
  state.searchQuery = '';
  state.searchResults = [];
}

async function copyHistoryEntry(s) {
  try {
    const body = await jsonApi(
      '/api/sessions/' + encodeURIComponent(s.session_id) + '/text'
    );
    const txt = body.extracted || '';
    if (!txt) {
      toast('Nothing to copy.', 'error');
      return;
    }
    await copyText(txt);
    toast('Copied ' + txt.length + ' chars.');
  } catch (exc) {
    toast('Copy failed: ' + (exc.message || exc), 'error');
  }
}

// Re-run a take with the model picked in "Redo with…" and the take's own
// style (the Settings default when the take doesn't say).
async function redoHistoryEntry(take, model) {
  await runTakeJob({
    failLabel: 'Redo failed',
    startLabel: 'Redo · starting',
    run: async function () {
      const started = await postJob(take.session_id, 'redo', {
        model: model,
        prompt_id: take.prompt_id || defaultPromptId(),
      });
      const body = await followJob(take.session_id, started, 'Redo');
      adoptTake(take.session_id, body);
      refreshHistoryView();
      toast('Redo done.');
    },
  });
}

// ----------------------------------------------------------- take sheet
// One sheet per take, from a browse row or a search hit: the when as title,
// the meta (photos, model, duration, exception chips), the text read-only in
// a box reserved before it arrives, then Copy text (the primary), Retry N
// unread (only when there are), Redo with…, and Delete take last.
function sheetMeta(el, take) {
  el.innerHTML = '';
  const parts = [];
  if (take.photos !== undefined) parts.push(photosText(take.photos));
  if (take.model) parts.push(modelLabel(take.model));
  if (take.duration) parts.push(Number(take.duration).toFixed(1) + ' s');
  el.append(parts.join(' · '));
  takeChips(take).forEach(function (chip) { el.append(' ', chip); });
}

function retryLabel(n) {
  return 'Retry ' + n + ' unread';
}

function openTakeSheet(take) {
  const body = document.createElement('div');
  body.className = 'take-sheet';
  const meta = document.createElement('p');
  meta.className = 'take-sheet-meta';
  sheetMeta(meta, take);
  const text = document.createElement('div');
  text.className = 'take-sheet-text';
  text.tabIndex = 0;
  text.setAttribute('role', 'region');
  text.setAttribute('aria-label', 'Text of this take');
  text.textContent = 'Loading the text…';
  text.classList.add('loading');
  const error = document.createElement('p');
  error.className = 'take-sheet-error';
  error.hidden = true;
  body.append(meta, text, error);

  const sheet = openSheet({
    title: fullWhen(take.created_at),
    body: body,
    className: 'take-sheet-dialog',
    actions: [
      { label: 'Copy text', icon: 'copy', tone: 'primary', onSelect: function () { copyHistoryEntry(take); } },
      {
        label: retryLabel(take.missing || 0),
        icon: 'refresh-cw',
        hidden: !take.missing,
        onSelect: function () { retryMissing(take.session_id); },
      },
      { label: 'Redo with…', icon: 'redo-2', onSelect: function () { openRedoPicker(take); } },
      { label: 'Delete take', icon: 'trash-2', tone: 'danger', onSelect: function () { confirmDelete(take); } },
    ],
  });
  const retryBtn = sheet.actions[1];

  jsonApi('/api/sessions/' + encodeURIComponent(take.session_id) + '/text')
    .then(function (res) {
      text.classList.remove('loading');
      text.textContent = res.extracted || '(no text)';
    })
    .catch(function (exc) {
      text.classList.remove('loading');
      text.textContent = '';
      error.textContent = 'Could not load the text: ' + (exc.message || exc);
      error.hidden = false;
    });

  // A search hit doesn't carry its unread photos, duration or style: ask the
  // server once the sheet is up, and reveal Retry only if it has some.
  if (take.missing === undefined) {
    jsonApi('/api/sessions/' + encodeURIComponent(take.session_id) + '/extract/status')
      .then(function (status) {
        take.missing = (status.missing_photos || []).length;
        take.duration = status.duration_s;
        take.model = status.model || take.model;
        take.prompt_id = status.prompt_id;
        sheetMeta(meta, take);
        retryBtn.querySelector('.sheet-action-label').textContent = retryLabel(take.missing);
        retryBtn.hidden = !take.missing;
      })
      .catch(function () { /* the sheet works without it; Redo remains */ });
  }
}

// "Redo with…": the models, the take's own one marked.
function openRedoPicker(take) {
  const models = (state.config && state.config.ocr_models_available) || [];
  openSheet({
    title: 'Redo with',
    actions: models.map(function (m) {
      return {
        label: modelLabel(m),
        current: m === take.model,
        onSelect: function () { redoHistoryEntry(take, m); },
      };
    }),
  });
}

async function confirmDelete(take) {
  const yes = await confirmSheet({
    title: 'Delete this take?',
    message: 'Its photos and text are deleted for good.',
    confirmLabel: 'Delete',
    danger: true,
  });
  if (yes) await deleteHistoryEntry(take);
}

async function deleteHistoryEntry(s) {
  try {
    await jsonApi(
      '/api/sessions/' + encodeURIComponent(s.session_id),
      { method: 'DELETE' }
    );
    refreshHistoryView();
  } catch (exc) {
    toast('Delete failed: ' + (exc.message || exc), 'error');
  }
}

// Settings › Storage › Delete all saved takes.
export async function cleanAllHistory() {
  const yes = await confirmSheet({
    title: 'Delete all saved takes?',
    message: 'Every saved take, with its photos and text, is deleted for good.',
    confirmLabel: 'Delete all',
    danger: true,
  });
  if (!yes) return;
  try {
    await jsonApi('/api/sessions', { method: 'DELETE' });
    refreshHistoryView();
    toast('History cleared.');
  } catch (exc) {
    toast('Clean failed: ' + (exc.message || exc), 'error');
  }
}
