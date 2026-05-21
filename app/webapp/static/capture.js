/* Photo OCR — photo capture, upload, the thumbnail strip, per-photo
 * delete/reorder, the preview dialog, and drag & drop. */

'use strict';

import { state, els, toast } from './state.js';
import { jsonApi } from './api.js';

function clientId() {
  return 'c' + Math.random().toString(36).slice(2, 10);
}

export function setStatus(text) {
  els.captureStatus.textContent = text || '';
}

// ----------------------------------------------------------- rendering
export function renderThumbnails() {
  els.thumbStrip.innerHTML = '';
  state.photos.forEach(function (photo, idx) {
    const li = document.createElement('li');
    li.className = 'thumb ' + (photo.status || 'pending');
    if (photo.status === 'uploading') li.classList.add('uploading');
    if (photo.status === 'failed') li.classList.add('failed');

    if (photo.previewUrl) {
      const img = document.createElement('img');
      img.src = photo.previewUrl;
      img.alt = 'Photo ' + (idx + 1);
      li.appendChild(img);
    }

    const seq = document.createElement('span');
    seq.className = 'seq';
    seq.textContent = String(idx + 1).padStart(2, '0');
    li.appendChild(seq);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove';
    removeBtn.type = 'button';
    removeBtn.title = 'Remove';
    removeBtn.textContent = '✕';
    removeBtn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      removePhoto(photo);
    });
    li.appendChild(removeBtn);

    if (idx > 0) {
      const left = document.createElement('button');
      left.className = 'move left';
      left.type = 'button';
      left.title = 'Move left';
      left.textContent = '◀';
      left.addEventListener('click', function (ev) {
        ev.stopPropagation();
        movePhoto(idx, idx - 1);
      });
      li.appendChild(left);
    }
    if (idx < state.photos.length - 1) {
      const right = document.createElement('button');
      right.className = 'move right';
      right.type = 'button';
      right.title = 'Move right';
      right.textContent = '▶';
      right.addEventListener('click', function (ev) {
        ev.stopPropagation();
        movePhoto(idx, idx + 1);
      });
      li.appendChild(right);
    }

    li.addEventListener('click', function () {
      if (photo.previewUrl) openPreview(photo.previewUrl);
    });
    els.thumbStrip.appendChild(li);
  });

  const haveAny = state.photos.length > 0;
  const haveReady = state.photos.some(function (p) { return p.status === 'ready'; });
  els.extractBtn.disabled = !haveReady || state.busy;
  if (state.busy) {
    // captureStatus owned by extract flow
  } else if (!haveAny) {
    setStatus('Add a photo to begin');
  } else if (!haveReady) {
    setStatus('Uploading…');
  } else {
    setStatus(state.photos.length + ' photo(s) ready · tap Extract');
  }
}

// ----------------------------------------------------------- capture
export function handleFilePick(files) {
  if (!files || !files.length) return;
  const maxAllowed = state.config && state.config.max_photos_per_session
    ? state.config.max_photos_per_session
    : 50;
  const spaceLeft = maxAllowed - state.photos.length;
  if (spaceLeft <= 0) {
    toast('Reached the max (' + maxAllowed + ') for this take. Extract or reset.', 'error');
    return;
  }
  const list = Array.from(files).slice(0, spaceLeft);
  list.forEach(function (file) {
    if (!file || !file.type || !file.type.startsWith('image/')) {
      toast('Skipped non-image: ' + (file ? file.name : '?'), 'error');
      return;
    }
    const photo = {
      clientId: clientId(),
      file: file,
      previewUrl: URL.createObjectURL(file),
      status: 'pending',
      seq: state.photos.length + 1,
      error: null,
    };
    state.photos.push(photo);
    uploadPhoto(photo);
  });
  renderThumbnails();
}

async function ensureSession() {
  if (state.sessionId) return state.sessionId;
  const body = await jsonApi('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ incognito: state.incognito }),
  });
  state.sessionId = body.session_id;
  return state.sessionId;
}

async function uploadPhoto(photo) {
  photo.status = 'uploading';
  renderThumbnails();
  try {
    const sid = await ensureSession();
    const form = new FormData();
    form.append('files', photo.file, photo.file.name || 'photo.jpg');
    const body = await jsonApi('/api/sessions/' + encodeURIComponent(sid) + '/photos', {
      method: 'POST',
      body: form,
    });
    // Server has authoritative photo list — the most recently appended
    // entry corresponds to this upload, but use sequence_index from the
    // server response for correctness if the user added several at once.
    const added = (body.added || []).slice(-1)[0];
    if (added) photo.seq = added.sequence_index;
    photo.status = 'ready';
  } catch (exc) {
    photo.status = 'failed';
    photo.error = String(exc.message || exc);
    toast('Upload failed: ' + photo.error, 'error');
  } finally {
    renderThumbnails();
  }
}

async function removePhoto(photo) {
  const idx = state.photos.indexOf(photo);
  if (idx < 0) return;
  if (photo.previewUrl) {
    try { URL.revokeObjectURL(photo.previewUrl); } catch (_) {}
  }
  state.photos.splice(idx, 1);
  renderThumbnails();
  if (photo.status === 'ready' && state.sessionId && photo.seq != null) {
    try {
      await jsonApi(
        '/api/sessions/' +
          encodeURIComponent(state.sessionId) +
          '/photos/' +
          photo.seq,
        { method: 'DELETE' }
      );
      // Server renumbers; refresh local seqs from server's truth.
      // We rely on the next upload's response to re-sync.
    } catch (exc) {
      toast('Server delete failed: ' + (exc.message || exc), 'error');
    }
  }
}

function movePhoto(fromIdx, toIdx) {
  if (toIdx < 0 || toIdx >= state.photos.length) return;
  const [moved] = state.photos.splice(fromIdx, 1);
  state.photos.splice(toIdx, 0, moved);
  renderThumbnails();
  // Server-side reorder is intentionally not implemented in v1; the
  // client picks the new order, then on extract the new order takes
  // effect since the server reads photos by sequence_index which is
  // re-stamped on every delete and on re-upload. For pure reorder
  // without delete, the v1 server still extracts in stored order, so
  // a re-upload of the affected photos is the workaround until v2.
  toast('Reorder is visual only in v1 — Extract will use stored upload order.', 'good');
}

// ----------------------------------------------------------- preview dialog
export function openPreview(url) {
  if (!els.previewDialog) return;
  els.previewImg.src = url;
  if (els.previewDialog.showModal) {
    els.previewDialog.showModal();
  } else {
    els.previewDialog.hidden = false;
  }
}
export function closePreview() {
  if (els.previewDialog.close) els.previewDialog.close();
  els.previewDialog.hidden = true;
  els.previewImg.src = '';
}

// ----------------------------------------------------------- drag & drop
export function setupDragDrop() {
  const target = document.body;
  ['dragenter', 'dragover'].forEach(function (evt) {
    target.addEventListener(evt, function (ev) {
      ev.preventDefault();
    });
  });
  target.addEventListener('drop', function (ev) {
    ev.preventDefault();
    if (ev.dataTransfer && ev.dataTransfer.files) {
      handleFilePick(ev.dataTransfer.files);
    }
  });
}
