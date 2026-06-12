/* Photo OCR — the History panel: list render, paging, copy/redo/delete
 * of saved takes, and the clean-all action. */

'use strict';

import { state, els, toast, HISTORY_PAGE_SIZE } from './state.js';
import { jsonApi } from './api.js';
import { renderExtracted } from './extract.js';
import { renderThumbnails, setStatus } from './capture.js';

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

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
// search-result list — one DOM shape, two data sources. The
// Copy/Redo/Delete actions only need a { session_id } reference.
function makeSessionRow(sessionId, leftText, rightText, previewText, isSnippet, source) {
  const li = document.createElement('li');
  li.className = 'history-item';

  const meta = document.createElement('div');
  meta.className = 'meta';
  const left = document.createElement('span');
  left.textContent = leftText;
  // Flag externally-sourced takes (app-launcher, api, …) so History is
  // an attributable cross-fleet audit trail. Manual PWA takes ("webapp")
  // are the unmarked default — no badge keeps the common case clean.
  if (source && source !== 'webapp') {
    const badge = document.createElement('span');
    badge.className = 'source-badge';
    badge.textContent = source;
    left.appendChild(document.createTextNode(' '));
    left.appendChild(badge);
  }
  const right = document.createElement('span');
  right.textContent = rightText;
  meta.appendChild(left);
  meta.appendChild(right);
  li.appendChild(meta);

  const preview = document.createElement('div');
  preview.className = 'preview';
  if (isSnippet) {
    preview.innerHTML = highlightSnippet(previewText);
  } else {
    preview.textContent = previewText;
  }
  li.appendChild(preview);

  const ref = { session_id: sessionId };
  const actions = document.createElement('div');
  actions.className = 'actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'copy-btn';
  copyBtn.type = 'button';
  copyBtn.textContent = '📋 Copy';
  copyBtn.addEventListener('click', function () { copyHistoryEntry(ref); });
  actions.appendChild(copyBtn);

  const redoBtn = document.createElement('button');
  redoBtn.className = 'ghost-btn';
  redoBtn.type = 'button';
  redoBtn.textContent = '🔁 Redo';
  redoBtn.addEventListener('click', function () { redoHistoryEntry(ref); });
  actions.appendChild(redoBtn);

  const delBtn = document.createElement('button');
  delBtn.className = 'ghost-btn';
  delBtn.type = 'button';
  delBtn.textContent = '🗑️ Delete';
  delBtn.addEventListener('click', function () { deleteHistoryEntry(ref); });
  actions.appendChild(delBtn);

  li.appendChild(actions);
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
  state.historyItems.forEach(function (s) {
    els.historyList.appendChild(
      makeSessionRow(
        s.session_id,
        formatDate(s.created_at) + ' · ' + s.photo_count + ' photo(s)',
        (s.model || '—') +
          (s.extract_duration_s
            ? ' · ' + s.extract_duration_s.toFixed(1) + 's'
            : ''),
        s.extracted_preview || (s.error ? '⚠️ ' + s.error : '(no text)'),
        false,
        s.source
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
          formatDate(r.created_at),
          r.model || '—',
          r.snippet || '(match)',
          true,
          r.source
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

// Debounced handler for the 🔎 input. Empty query restores browse mode.
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
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(txt);
    }
    toast('Copied ' + txt.length + ' chars.', 'good');
  } catch (exc) {
    toast('Copy failed: ' + (exc.message || exc), 'error');
  }
}

async function redoHistoryEntry(s) {
  if (state.busy) return;
  state.busy = true;
  renderThumbnails();
  setStatus('Queued redo…');
  let finalStatusText = null;
  try {
    let body = await jsonApi(
      '/api/sessions/' + encodeURIComponent(s.session_id) + '/redo',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: state.model, prompt_id: state.promptId }),
        timeoutMs: 15000,
      }
    );
    while (body.phase !== 'succeeded') {
      if (body.phase === 'failed') throw new Error(body.error || 'redo failed');
      if (body.phase === 'running') {
        const total = body.chunks_total || 0;
        const current = total ? Math.min((body.chunks_done || 0) + 1, total) : 1;
        setStatus('Redo chunk ' + current + ' of ' + total + '…');
      } else if (body.phase === 'merging') {
        setStatus('Merging redo output…');
      }
      await sleep(1000);
      body = await jsonApi(
        '/api/sessions/' + encodeURIComponent(s.session_id) + '/extract/status',
        { timeoutMs: 15000 }
      );
    }
    state.sessionId = s.session_id;
    state.extracted = body.extracted || '';
    renderExtracted();
    refreshHistoryView();
    setStatus('Redo done — tap Copy');
    finalStatusText = els.captureStatus.textContent;
    toast('Redo done.', 'good');
  } catch (exc) {
    setStatus('❌ ' + (exc.message || exc));
    finalStatusText = els.captureStatus.textContent;
    toast('Redo failed: ' + (exc.message || exc), 'error');
  } finally {
    state.busy = false;
    renderThumbnails();
    if (finalStatusText) setStatus(finalStatusText);
  }
}

async function deleteHistoryEntry(s) {
  if (!confirm('Delete session ' + s.session_id + '?')) return;
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
    toast('History cleared.', 'good');
  } catch (exc) {
    toast('Clean failed: ' + (exc.message || exc), 'error');
  }
}
