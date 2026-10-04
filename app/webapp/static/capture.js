/* Photo OCR — photo capture, upload, the thumbnail strip (tap a tile to
 * select it, act from the toolbar), per-photo delete/reorder, the preview
 * dialog, and drag & drop. */

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
function selectedIndex() {
  return state.photos.findIndex(function (p) {
    return p.clientId === state.selectedPhotoId;
  });
}

function selectPhoto(photo) {
  // Tapping the selected thumbnail again deselects it.
  state.selectedPhotoId = state.selectedPhotoId === photo.clientId ? null : photo.clientId;
  renderThumbnails();
}

function clearSelection() {
  if (state.selectedPhotoId == null) return;
  state.selectedPhotoId = null;
  renderThumbnails();
}

// Arm the toolbar for the selected photo: every action is disabled until a
// thumbnail is selected, and Retake / Keep are offered only for a photo that
// carries a quality warning.
function syncToolbar() {
  const idx = selectedIndex();
  const photo = idx >= 0 ? state.photos[idx] : null;
  const warned = !!(photo && photo.warnings && photo.warnings.length && !photo.warningDismissed);
  els.thumbToolbar.hidden = state.photos.length === 0;
  els.thumbMoveLeft.disabled = !photo || idx === 0;
  els.thumbMoveRight.disabled = !photo || idx === state.photos.length - 1;
  els.thumbView.disabled = !photo || !photo.previewUrl;
  els.thumbRemove.disabled = !photo;
  els.thumbRetake.hidden = !warned;
  els.thumbRetake.disabled = !warned;
  els.thumbKeep.hidden = !warned;
  els.thumbKeep.disabled = !warned;
}

// A toolbar action can disable or hide the button that was just pressed
// (moving to the end, Keep, Remove) — hand focus to the selected thumbnail
// instead of dropping it to the page.
function ensureFocus(fallbackIdx) {
  const active = document.activeElement;
  if (active && active !== document.body && !active.disabled && !active.hidden) return;
  const tiles = els.thumbStrip.querySelectorAll('.thumb-select');
  const idx = selectedIndex() >= 0 ? selectedIndex() : fallbackIdx;
  const target = tiles[Math.min(idx, tiles.length - 1)];
  if (target) target.focus();
}

export function renderThumbnails() {
  const active = document.activeElement;
  const refocusId =
    active && active.classList && active.classList.contains('thumb-select')
      ? active.dataset.clientId
      : null;
  const scrollLeft = els.thumbStrip.scrollLeft;
  if (selectedIndex() < 0) state.selectedPhotoId = null;
  els.thumbStrip.innerHTML = '';
  state.photos.forEach(function (photo, idx) {
    const li = document.createElement('li');
    li.className = 'thumb ' + (photo.status || 'pending');
    if (photo.status === 'uploading') li.classList.add('uploading');
    if (photo.status === 'failed') li.classList.add('failed');
    const selected = photo.clientId === state.selectedPhotoId;
    if (selected) li.classList.add('selected');
    const warned = !!(photo.warnings && photo.warnings.length && !photo.warningDismissed);
    if (warned) li.classList.add('warned');

    // The tile itself is the select toggle (a real button: focusable, Enter /
    // Space); its photo and the chips below are decoration for it.
    const pick = document.createElement('button');
    pick.className = 'thumb-select';
    pick.type = 'button';
    pick.dataset.clientId = photo.clientId;
    pick.setAttribute('aria-pressed', selected ? 'true' : 'false');
    pick.setAttribute(
      'aria-label',
      'Photo ' + (idx + 1) + (warned ? ', ' + warningLabel(photo.warnings) : '')
    );
    if (photo.previewUrl) {
      const img = document.createElement('img');
      img.src = photo.previewUrl;
      img.alt = '';
      pick.appendChild(img);
    }
    pick.addEventListener('click', function () { selectPhoto(photo); });
    li.appendChild(pick);

    const seq = document.createElement('span');
    seq.className = 'seq';
    seq.textContent = String(idx + 1).padStart(2, '0');
    li.appendChild(seq);

    if (warned) {
      const chip = document.createElement('span');
      chip.className = 'warn-chip';
      chip.textContent = warningLabel(photo.warnings);
      li.appendChild(chip);
    }

    els.thumbStrip.appendChild(li);
    if (photo.clientId === refocusId) pick.focus();
  });
  els.thumbStrip.scrollLeft = scrollLeft;
  syncToolbar();

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
    toast('Order will sync before Extract.');
    return;
  }
  syncPhotoOrder()
    .then(function () {
      toast('Order saved for Extract.');
    })
    .catch(function (exc) {
      toast('Order sync failed: ' + (exc.message || exc), 'error');
    });
}

// ----------------------------------------------------------- toolbar
function selectedPhoto() {
  const idx = selectedIndex();
  return idx >= 0 ? state.photos[idx] : null;
}

export function setupThumbToolbar() {
  els.thumbMoveLeft.addEventListener('click', function () {
    const idx = selectedIndex();
    if (idx > 0) movePhoto(idx, idx - 1);
    ensureFocus(idx);
  });
  els.thumbMoveRight.addEventListener('click', function () {
    const idx = selectedIndex();
    if (idx >= 0) movePhoto(idx, idx + 1);
    ensureFocus(idx);
  });
  els.thumbView.addEventListener('click', function () {
    const photo = selectedPhoto();
    if (photo && photo.previewUrl) openPreview(photo.previewUrl);
  });
  els.thumbRetake.addEventListener('click', function () {
    const photo = selectedPhoto();
    if (photo) retakePhoto(photo);
  });
  els.thumbKeep.addEventListener('click', function () {
    const idx = selectedIndex();
    if (idx < 0) return;
    state.photos[idx].warningDismissed = true;
    renderThumbnails();
    ensureFocus(idx);
  });
  els.thumbRemove.addEventListener('click', function () {
    const idx = selectedIndex();
    if (idx < 0) return;
    removePhoto(state.photos[idx]);
    ensureFocus(idx);
  });

  // Tapping anywhere that is not a thumbnail or the toolbar (or the preview it
  // opens) clears the selection; so does Escape, unless it is closing the
  // preview dialog. pointerup, not click: iOS Safari does not dispatch a
  // click to a document listener for a tap on a non-interactive element, and
  // pointerup (unlike pointerdown) is not fired by the start of a page scroll.
  document.addEventListener('pointerup', function (ev) {
    if (state.selectedPhotoId == null) return;
    const target = ev.target;
    if (target && target.closest && target.closest('.thumb, #thumbToolbar, #previewDialog')) return;
    clearSelection();
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape' || state.selectedPhotoId == null) return;
    if (els.previewDialog && els.previewDialog.open) return;
    clearSelection();
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
