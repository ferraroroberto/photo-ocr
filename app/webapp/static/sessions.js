/* Photo OCR — the History panel: list render, paging, tap-to-copy rows with
 * a Redo / Delete overflow menu, and the clean-all action. */

'use strict';

import { state, els, toast, HISTORY_PAGE_SIZE, modelLabel, copyText } from './state.js';
import { jsonApi } from './api.js';
import { adoptTake, followJob, postJob, retryMissing, runTakeJob } from './extract.js';
import { setStatus } from './capture.js';
import { icon } from './_vendored/icons/icons.js';
import { emptyStateEl } from './_vendored/empty-state/empty-state.js';

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

// Shared row component for both the chronological history list and the
// search-result list — one DOM shape, two data sources. One take is one
// action-row (design.md): tap the row to copy it, one trailing overflow
// control for everything else (Redo, Delete). The snippet leads as the
// title; date, photo count and model are the one muted meta line.
function makeSessionRow(sessionId, metaText, previewText, isSnippet, source, missing) {
  const li = document.createElement('li');
  li.className = 'action-row history-item';
  // `missing` = count of unread photos; undefined when the list can't say
  // (search hits), in which case the menu asks the server when it opens.
  const ref = { session_id: sessionId, missing: missing };

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'action-row-main';
  main.title = 'Copy this take';
  main.addEventListener('click', function () { copyHistoryEntry(ref); });

  const title = document.createElement('span');
  title.className = 'action-row-title preview';
  if (isSnippet) {
    title.innerHTML = highlightSnippet(previewText);
  } else {
    title.textContent = previewText;
  }

  const meta = document.createElement('span');
  meta.className = 'action-row-meta';
  meta.textContent = metaText;
  // Flag externally-sourced takes (app-launcher, api, …) so History is
  // an attributable cross-fleet audit trail. Manual PWA takes ("webapp")
  // are the unmarked default — no badge keeps the common case clean.
  if (source && source !== 'webapp') {
    const badge = document.createElement('span');
    badge.className = 'source-badge';
    badge.textContent = source;
    meta.appendChild(document.createTextNode(' '));
    meta.appendChild(badge);
  }
  main.append(title, meta);

  const more = document.createElement('button');
  more.type = 'button';
  more.className = 'action-row-kebab';
  more.setAttribute('aria-label', 'More actions');
  more.setAttribute('aria-haspopup', 'dialog');
  more.innerHTML = icon('ellipsis-vertical');
  more.addEventListener('click', function () { openTakeMenu(ref, metaText); });

  li.append(main, more);
  return li;
}

function renderHistory() {
  // Search mode and browse mode share the same list element.
  if (state.searchQuery) {
    renderSearchResults();
    return;
  }
  els.historyCount.textContent =
    state.historyItems.length + '/' + state.historyTotal;
  els.historyList.innerHTML = '';
  if (!state.historyItems.length) {
    // Canonical fleet empty-state block — never a silent empty container.
    const li = document.createElement('li');
    li.appendChild(emptyStateEl('history', 'No saved takes yet'));
    els.historyList.appendChild(li);
  }
  state.historyItems.forEach(function (s) {
    const unread = ((s.extract_progress || {}).missing_photos || []).length;
    const parts = [
      formatDate(s.created_at),
      s.photo_count + (s.photo_count === 1 ? ' photo' : ' photos'),
      (s.model ? modelLabel(s.model) : '—') +
        (s.extract_duration_s
          ? ' · ' + s.extract_duration_s.toFixed(1) + 's'
          : ''),
    ];
    if (unread) parts.push(unread + ' unread');
    els.historyList.appendChild(
      makeSessionRow(
        s.session_id,
        parts.join(' · '),
        s.extracted_preview || (s.error ? 'Error — ' + s.error : '(no text)'),
        false,
        s.source,
        unread
      )
    );
  });
  els.loadMoreHistory.hidden =
    state.historyItems.length >= state.historyTotal;
}

function renderSearchResults() {
  const results = state.searchResults || [];
  els.historyCount.textContent =
    results.length + (results.length === 1 ? ' match' : ' matches');
  els.historyList.innerHTML = '';
  if (!results.length) {
    const empty = document.createElement('li');
    empty.className = 'history-empty';
    empty.textContent = 'No matches for “' + state.searchQuery + '”';
    els.historyList.appendChild(empty);
  } else {
    results.forEach(function (r) {
      els.historyList.appendChild(
        makeSessionRow(
          r.session_id,
          formatDate(r.created_at) + ' · ' + (r.model ? modelLabel(r.model) : '—'),
          r.snippet || '(match)',
          true,
          r.source,
          undefined
        )
      );
    });
  }
  // Search returns a single ranked page — no incremental loading.
  els.loadMoreHistory.hidden = true;
}

// Re-render the history panel after a mutation, staying in whichever
// mode (search vs. browse) the user is currently in.
function refreshHistoryView() {
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

// Drop out of search mode — used by the Refresh button.
export function clearSearch() {
  if (els.historySearch) els.historySearch.value = '';
  if (searchTimer) clearTimeout(searchTimer);
  state.searchQuery = '';
  state.searchResults = [];
}

function formatDate(iso) {
  if (!iso) return '?';
  try {
    const d = new Date(iso);
    const pad = function (n) { return String(n).padStart(2, '0'); };
    return (
      d.getFullYear() +
      '-' +
      pad(d.getMonth() + 1) +
      '-' +
      pad(d.getDate()) +
      ' ' +
      pad(d.getHours()) +
      ':' +
      pad(d.getMinutes())
    );
  } catch (_) {
    return iso;
  }
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

async function redoHistoryEntry(s) {
  await runTakeJob({
    failLabel: 'Redo failed',
    startStatus: 'Queued redo…',
    run: async function () {
      const started = await postJob(s.session_id, 'redo', {
        model: state.model,
        prompt_id: state.promptId,
      });
      const body = await followJob(s.session_id, started, 'Redo ');
      adoptTake(s.session_id, body);
      refreshHistoryView();
      setStatus('Redo done — tap Copy');
      toast('Redo done.');
    },
  });
}

// The overflow menu is one shared <dialog>; it remembers which take opened it.
let menuTake = null;

function openTakeMenu(ref, metaText) {
  menuTake = ref;
  els.takeMenuWhen.textContent = metaText;
  els.takeRetry.hidden = !ref.missing;
  els.takeMenu.showModal();
  if (ref.missing === undefined) revealRetryIfMissing(ref);
}

// A search hit doesn't carry its unread photos; ask the server once the menu
// is up and show the action only if that take still has some.
async function revealRetryIfMissing(ref) {
  try {
    const body = await jsonApi(
      '/api/sessions/' + encodeURIComponent(ref.session_id) + '/extract/status'
    );
    ref.missing = (body.missing_photos || []).length;
    if (menuTake === ref) els.takeRetry.hidden = !ref.missing;
  } catch (_) {
    // The menu still works without it; Redo remains.
  }
}

export function initTakeMenu() {
  const dlg = els.takeMenu;
  dlg.querySelector('.detail-close').addEventListener('click', function () { dlg.close(); });
  // A tap on the backdrop (the dialog element itself, outside its card) dismisses.
  dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
  els.takeRetry.addEventListener('click', function () {
    const ref = menuTake;
    dlg.close();
    if (ref) retryMissing(ref.session_id);
  });
  els.takeRedo.addEventListener('click', function () {
    const ref = menuTake;
    dlg.close();
    if (ref) redoHistoryEntry(ref);
  });
  els.takeDelete.addEventListener('click', async function () {
    const ref = menuTake;
    if (!ref || !confirm('Delete this take?')) return;
    dlg.close();
    await deleteHistoryEntry(ref);
  });
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

export async function cleanAllHistory() {
  if (!confirm('Delete all saved takes?')) return;
  try {
    await jsonApi('/api/sessions', { method: 'DELETE' });
    refreshHistoryView();
    toast('History cleared.');
  } catch (exc) {
    toast('Clean failed: ' + (exc.message || exc), 'error');
  }
}
