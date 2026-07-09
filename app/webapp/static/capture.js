/* Photo OCR — photo capture, upload, the thumbnail strip, per-photo
 * delete/reorder, the preview dialog, and drag & drop. */

'use strict';

import { state, els, toast } from './state.js';
import { jsonApi } from './api.js';
import { assessImage } from './quality.js';
import { icon } from './_vendored/icons/icons.js';

function clientId() {
  return 'c' + Math.random().toString(36).slice(2, 10);
}

function readyPhotos() {
  return state.photos.filter(function (p) {
    return p.status === 'ready' && p.seq != null;
  });
}

function applyServerPhotoOrder(serverPhotos) {
  const ready = readyPhotos();
  (serverPhotos || []).forEach(function (pm, idx) {
    if (ready[idx]) ready[idx].seq = pm.sequence_index;
  });
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
    removeBtn.setAttribute('aria-label', 'Remove photo');
    removeBtn.innerHTML = icon('x');
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
      left.setAttribute('aria-label', 'Move photo left');
      left.innerHTML = icon('chevron-left');
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
      right.setAttribute('aria-label', 'Move photo right');
      right.innerHTML = icon('chevron-right');
      right.addEventListener('click', function (ev) {
        ev.stopPropagation();
        movePhoto(idx, idx + 1);
      });
      li.appendChild(right);
    }

    if (photo.warnings && photo.warnings.length && !photo.warningDismissed) {
      li.classList.add('warned');
      const warn = document.createElement('div');
      warn.className = 'photo-warning';

      const warnText = document.createElement('div');
      warnText.className = 'warn-text';
      warnText.textContent = warningLabel(photo.warnings);
      warn.appendChild(warnText);

      const retakeBtn = document.createElement('button');
      retakeBtn.className = 'retake-btn';
      retakeBtn.type = 'button';
      retakeBtn.textContent = 'Retake';
      retakeBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        retakePhoto(photo);
      });
      warn.appendChild(retakeBtn);

      const dismissBtn = document.createElement('button');
      dismissBtn.className = 'dismiss-btn';
      dismissBtn.type = 'button';
      dismissBtn.textContent = 'Keep';
      dismissBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        photo.warningDismissed = true;
        renderThumbnails();
      });
      warn.appendChild(dismissBtn);

      li.appendChild(warn);
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
      warnings: [],
      warningDismissed: false,
    };
    state.photos.push(photo);
    uploadPhoto(photo);
    assessPhoto(photo);
  });
  renderThumbnails();
}

async function ensureSession() {
  if (state.sessionId) return state.sessionId;
  // Multi-select capture calls this once per photo without awaiting between
  // them — memoize the in-flight request so concurrent callers share the
  // same POST instead of each racing a fresh session into existence.
  if (!state.sessionIdPromise) {
    state.sessionIdPromise = (async function () {
      const body = await jsonApi('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ incognito: state.incognito }),
      });
      state.sessionId = body.session_id;
      return state.sessionId;
    })().finally(function () {
      state.sessionIdPromise = null;
    });
  }
  return state.sessionIdPromise;
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
      const body = await jsonApi(
        '/api/sessions/' +
          encodeURIComponent(state.sessionId) +
          '/photos/' +
          photo.seq,
        { method: 'DELETE' }
      );
      applyServerPhotoOrder(body.photos);
    } catch (exc) {
      toast('Server delete failed: ' + (exc.message || exc), 'error');
    }
  }
}

export async function syncPhotoOrder() {
  if (!state.sessionId) return;
  const ready = readyPhotos();
  if (!ready.length) return;
  const order = ready.map(function (p) { return p.seq; });
  const body = await jsonApi(
    '/api/sessions/' + encodeURIComponent(state.sessionId) + '/photos/reorder',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order: order }),
    }
  );
  applyServerPhotoOrder(body.photos);
}

function movePhoto(fromIdx, toIdx) {
  if (toIdx < 0 || toIdx >= state.photos.length) return;
  const [moved] = state.photos.splice(fromIdx, 1);
  state.photos.splice(toIdx, 0, moved);
  renderThumbnails();
  if (
    !state.sessionId ||
    readyPhotos().length < 2 ||
    state.photos.some(function (p) { return p.status === 'pending' || p.status === 'uploading'; })
  ) {
    toast('Order will sync before Extract.', 'good');
    return;
  }
  syncPhotoOrder()
    .then(function () {
      toast('Order saved for Extract.', 'good');
    })
    .catch(function (exc) {
      toast('Order sync failed: ' + (exc.message || exc), 'error');
    });
}

// --------------------------------------------------- quality gate
const WARNING_LABELS = { blurry: 'Blurry', 'too dark': 'Dark', glare: 'Glare' };

function warningLabel(warnings) {
  return warnings
    .map(function (w) { return WARNING_LABELS[w] || w; })
    .join(' · ');
}

// Score a freshly-added photo on-device. Advisory only — any failure is
// swallowed, and the feature flag can switch it off entirely.
function assessPhoto(photo) {
  if (!state.config || !state.config.quality_gate_enabled) return;
  if (!photo.file) return;
  assessImage(photo.file)
    .then(function (result) {
      // The photo may have been removed while we were decoding.
      if (state.photos.indexOf(photo) < 0) return;
      photo.warnings = (result && result.warnings) || [];
      renderThumbnails();
    })
    .catch(function () { /* advisory — never surface a decode error */ });
}

function retakePhoto(photo) {
  removePhoto(photo);
  // Re-open the camera so "retake" is genuinely one tap.
  if (els.cameraInput) els.cameraInput.click();
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
