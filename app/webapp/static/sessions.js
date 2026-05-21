/* Photo OCR — the History panel: list render, paging, copy/redo/delete
 * of saved takes, and the clean-all action. */

'use strict';

import { state, els, toast, HISTORY_PAGE_SIZE } from './state.js';
import { jsonApi } from './api.js';
import { renderExtracted } from './extract.js';

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

function renderHistory() {
  els.historyCount.textContent =
    state.historyItems.length + '/' + state.historyTotal;
  els.historyList.innerHTML = '';
  state.historyItems.forEach(function (s) {
    const li = document.createElement('li');
    li.className = 'history-item';

    const meta = document.createElement('div');
    meta.className = 'meta';
    const left = document.createElement('span');
    left.textContent =
      formatDate(s.created_at) + ' · ' + s.photo_count + ' photo(s)';
    const right = document.createElement('span');
    right.textContent =
      (s.model || '—') +
      (s.extract_duration_s ? ' · ' + s.extract_duration_s.toFixed(1) + 's' : '');
    meta.appendChild(left);
    meta.appendChild(right);
    li.appendChild(meta);

    const preview = document.createElement('div');
    preview.className = 'preview';
    preview.textContent = s.extracted_preview || (s.error ? '⚠️ ' + s.error : '(no text)');
    li.appendChild(preview);

    const actions = document.createElement('div');
    actions.className = 'actions';

    const copyBtn = document.createElement('button');
    copyBtn.className = 'copy-btn';
    copyBtn.type = 'button';
    copyBtn.textContent = '📋 Copy';
    copyBtn.addEventListener('click', function () { copyHistoryEntry(s); });
    actions.appendChild(copyBtn);

    const redoBtn = document.createElement('button');
    redoBtn.className = 'ghost-btn';
    redoBtn.type = 'button';
    redoBtn.textContent = '🔁 Redo';
    redoBtn.addEventListener('click', function () { redoHistoryEntry(s); });
    actions.appendChild(redoBtn);

    const delBtn = document.createElement('button');
    delBtn.className = 'ghost-btn';
    delBtn.type = 'button';
    delBtn.textContent = '🗑️ Delete';
    delBtn.addEventListener('click', function () { deleteHistoryEntry(s); });
    actions.appendChild(delBtn);

    li.appendChild(actions);
    els.historyList.appendChild(li);
  });

  if (state.historyItems.length < state.historyTotal) {
    els.loadMoreHistory.hidden = false;
  } else {
    els.loadMoreHistory.hidden = true;
  }
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
  try {
    const body = await jsonApi(
      '/api/sessions/' + encodeURIComponent(s.session_id) + '/redo',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: state.model, prompt_id: state.promptId }),
      }
    );
    state.sessionId = s.session_id;
    state.extracted = body.extracted || '';
    renderExtracted();
    loadHistory(0);
    toast('Redo done.', 'good');
  } catch (exc) {
    toast('Redo failed: ' + (exc.message || exc), 'error');
  }
}

async function deleteHistoryEntry(s) {
  if (!confirm('Delete session ' + s.session_id + '?')) return;
  try {
    await jsonApi(
      '/api/sessions/' + encodeURIComponent(s.session_id),
      { method: 'DELETE' }
    );
    loadHistory(0);
  } catch (exc) {
    toast('Delete failed: ' + (exc.message || exc), 'error');
  }
}

export async function cleanAllHistory() {
  if (!confirm('Delete all saved takes?')) return;
  try {
    await jsonApi('/api/sessions', { method: 'DELETE' });
    loadHistory(0);
    toast('History cleared.', 'good');
  } catch (exc) {
    toast('Clean failed: ' + (exc.message || exc), 'error');
  }
}
