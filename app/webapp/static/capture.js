/* Photo OCR — the take card: the empty take's two starts, photo capture and
 * upload, the thumbnail strip (tap a tile for its photo sheet; the last tile
 * adds more), per-photo delete/reorder, this take's model / style / Save to
 * history line, which of the card's three views shows (empty, photos,
 * result), the header's exception line, the progress meter and the error
 * line, and drag & drop. */

'use strict';

import { state, els, toast, modelLabel } from './state.js';
import { jsonApi } from './api.js';
import { assessImage } from './quality.js';
import { icon } from './_vendored/icons/icons.js';
import { resetTake } from './extract.js';
import { openSheet } from './sheet.js';

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

// A failed job's line in the card (with its toast). The server's own message
// reaches it unchanged, so "hub unreachable" and "hub still working past the
// timeout" stay distinct. Empty text hides it.
export function setError(text) {
  els.takeError.textContent = text || '';
  els.takeError.hidden = !text;
}

// The meter while a job reads: `{ label, fraction }` from poll.js
// extractProgress (or a starting label at 0). clearProgress hides it.
export function setProgress(p) {
  const pct = Math.round(Math.max(0, Math.min(1, p.fraction || 0)) * 100);
  els.extractMeter.hidden = false;
  els.extractMeterFill.style.width = pct + '%';
  els.extractMeterBar.setAttribute('aria-valuenow', String(pct));
  els.extractMeterLabel.textContent = p.label || '';
}
export function clearProgress() {
  els.extractMeter.hidden = true;
  els.extractMeterLabel.textContent = '';
}

function maxPhotos() {
  return state.config && state.config.max_photos_per_session
    ? state.config.max_photos_per_session
    : 50;
}

function isWarned(photo) {
  return !!(photo.warnings && photo.warnings.length && !photo.warningDismissed);
}

function plural(n, word) {
  return n + ' ' + word + (n === 1 ? '' : 's');
}

// The Capture header's context line: exceptions only (design.md page-header),
// each part in its tone, at most two parts, empty when the take is normal.
// Computed from the same state the card draws, so the two never disagree.
export function renderHeadline() {
  const photos = state.photos;
  const count = function (status) {
    return photos.filter(function (p) { return p.status === status; }).length;
  };
  const parts = [];
  if (state.busy) parts.push({ text: 'Reading', tone: 'accent' });
  const failed = count('failed');
  if (failed) parts.push({ text: failed + ' failed', tone: 'danger', photo: true });
  const uploading = count('pending') + count('uploading');
  if (uploading) parts.push({ text: 'Uploading ' + uploading, tone: 'neutral' });
  if (state.result && state.missingPhotos.length) {
    parts.push({ text: plural(state.missingPhotos.length, 'photo') + ' unread', tone: 'attention' });
  }
  // A quality warning is for deciding before Extract; a result has read them.
  const warned = state.result ? [] : photos.filter(isWarned);
  if (warned.length) {
    const kinds = new Set(warned.map(function (p) { return warningLabel(p.warnings); }));
    const word = kinds.size === 1 ? Array.from(kinds)[0].toLowerCase() : 'unclear';
    parts.push({ text: warned.length + ' ' + word, tone: 'attention', photo: true });
  }
  if (state.incognito) parts.push({ text: 'Not saved to History', tone: 'neutral' });

  const shown = parts.slice(0, 2);
  els.captureHeadline.innerHTML = '';
  // "3 photos · 1 blurry": a lone photo exception names the take's size.
  if (shown.length === 1 && shown[0].photo) {
    els.captureHeadline.append(plural(photos.length, 'photo') + ' · ');
  }
  shown.forEach(function (part, i) {
    if (i) els.captureHeadline.append(' · ');
    const span = document.createElement('span');
    span.className = 'headline-part';
    span.dataset.tone = part.tone;
    span.textContent = part.text;
    els.captureHeadline.appendChild(span);
  });
}

// Which of the card's three views shows: the empty take's starts, the photos
// (strip, options, Extract) or the result. A result takes the card until
// "N photos ›" asks for the photos; an adopted History take has none to show.
export function renderView() {
  const haveAny = state.photos.length > 0;
  const hasResult = !!state.result;
  const showResult = hasResult && (!state.showPhotos || !haveAny);
  els.takeEmpty.hidden = haveAny || hasResult;
  els.takeBuild.hidden = !haveAny || showResult;
  els.takeResult.hidden = !showResult;
  els.showText.hidden = !hasResult;
  els.showPhotos.hidden = !haveAny;
  els.showPhotosLabel.textContent = plural(state.photos.length, 'photo');
  els.extractLabel.textContent = hasResult ? 'Extract again' : 'Extract text';
}

export function showPhotosView(show) {
  state.showPhotos = show;
  renderView();
}

// This take's model and style chips: the per-take choice. Settings holds the
// default a new take starts from.
export function styleLabel(id) {
  const prompt = (state.prompts || []).find(function (p) { return p.id === id; });
  return prompt ? prompt.label || prompt.id : String(id || '');
}

function fillChip(chip, text, name) {
  chip.innerHTML = '';
  const label = document.createElement('span');
  label.className = 'take-chip-text';
  label.textContent = text;
  chip.appendChild(label);
  chip.insertAdjacentHTML('beforeend', icon('chevron-down'));
  chip.setAttribute('aria-label', name + ' for this take: ' + text);
}

export function renderTakeOptions() {
  fillChip(els.takeModel, modelLabel(state.model || ''), 'Model');
  fillChip(els.takeStyle, styleLabel(state.promptId), 'Style');
}

export function openModelPicker() {
  const models = (state.config && state.config.ocr_models_available) || [];
  openSheet({
    title: 'Model for this take',
    actions: models.map(function (m) {
      return {
        label: modelLabel(m),
        current: m === state.model,
        onSelect: function () { state.model = m; renderTakeOptions(); },
      };
    }),
  });
}

export function openStylePicker() {
  openSheet({
    title: 'Style for this take',
    actions: (state.prompts || []).map(function (p) {
      return {
        label: p.label || p.id,
        current: p.id === state.promptId,
        onSelect: function () { state.promptId = p.id; renderTakeOptions(); },
      };
    }),
  });
}

// The strip's + tile and the take's ⋯ each open one sheet.
function openAddSheet() {
  openSheet({
    title: 'Add photos',
    actions: [
      { label: 'Choose photos', icon: 'image', onSelect: function () { els.galleryInput.click(); } },
      { label: 'Camera', icon: 'camera', onSelect: function () { els.cameraInput.click(); } },
    ],
  });
}

export function openTakeMoreSheet() {
  openSheet({
    title: 'This take',
    actions: [
      // Drops the photos and the text, no confirm (the result's New take).
      { label: 'Start over', icon: 'eraser', onSelect: resetTake },
    ],
  });
}

// ----------------------------------------------------------- photo sheet
// One sheet per photo: the photo whole (its box reserved so nothing shifts
// while it loads), Move left / right (the sheet stays open on the same photo),
// Retake and Keep for a quality-warned photo, Remove last. Closing hands
// focus back to the photo's tile, or its neighbour once it is gone.
function focusTile(photo, fallbackIdx) {
  const tiles = els.thumbStrip.querySelectorAll('button');
  const own = els.thumbStrip.querySelector('.thumb-select[data-client-id="' + photo.clientId + '"]');
  const target = own || tiles[Math.min(fallbackIdx, tiles.length - 1)];
  if (target) target.focus();
}

function openPhotoSheet(photo) {
  const startIdx = state.photos.indexOf(photo);
  const body = document.createElement('div');
  body.className = 'photo-sheet';

  const frame = document.createElement('div');
  frame.className = 'photo-sheet-frame';
  if (photo.previewUrl) {
    const img = document.createElement('img');
    img.src = photo.previewUrl;
    img.alt = 'Photo preview';
    frame.appendChild(img);
  }

  const warning = document.createElement('p');
  warning.className = 'photo-sheet-warning';

  const moves = document.createElement('div');
  moves.className = 'photo-sheet-moves';
  const moveButton = function (glyph, label, step) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'button-surface';
    btn.innerHTML = icon(glyph);
    btn.append(label);
    btn.addEventListener('click', function () {
      const idx = state.photos.indexOf(photo);
      if (idx >= 0) movePhoto(idx, idx + step);
      sync();
    });
    return btn;
  };
  const left = moveButton('chevron-left', 'Move left', -1);
  const right = moveButton('chevron-right', 'Move right', 1);
  moves.append(left, right);
  body.append(frame, warning, moves);

  const actions = [];
  if (isWarned(photo)) {
    actions.push({ label: 'Retake', icon: 'camera', onSelect: function () { retakePhoto(photo); } });
    actions.push({
      label: 'Keep it',
      icon: 'check',
      onSelect: function () { photo.warningDismissed = true; renderThumbnails(); },
    });
  }
  actions.push({
    label: 'Remove photo',
    icon: 'trash-2',
    tone: 'danger',
    onSelect: function () { removePhoto(photo); },
  });

  const sheet = openSheet({
    title: '',
    body: body,
    actions: actions,
    className: 'photo-sheet-dialog',
    onClose: function () { focusTile(photo, Math.max(0, startIdx)); },
  });
  const title = sheet.dialog.querySelector('h2');

  function sync() {
    const idx = state.photos.indexOf(photo);
    title.textContent = 'Photo ' + (idx + 1) + ' of ' + state.photos.length;
    left.disabled = idx <= 0;
    right.disabled = idx < 0 || idx >= state.photos.length - 1;
    warning.hidden = !isWarned(photo);
    warning.textContent = isWarned(photo)
      ? warningLabel(photo.warnings) + ': retake it, or keep it if it reads fine.'
      : '';
  }
  sync();
}

// ----------------------------------------------------------- rendering
export function renderThumbnails() {
  const active = document.activeElement;
  const refocusId =
    active && active.classList && active.classList.contains('thumb-select')
      ? active.dataset.clientId
      : null;
  const scrollLeft = els.thumbStrip.scrollLeft;
  els.thumbStrip.innerHTML = '';
  state.photos.forEach(function (photo, idx) {
    const li = document.createElement('li');
    li.className = 'thumb ' + (photo.status || 'pending');
    if (photo.status === 'uploading') li.classList.add('uploading');
    if (photo.status === 'failed') li.classList.add('failed');
    const warned = isWarned(photo);
    if (warned) li.classList.add('warned');

    // The tile is one button (focusable, Enter / Space) opening the photo's
    // sheet; its photo and the chips below are decoration for it.
    const pick = document.createElement('button');
    pick.className = 'thumb-select';
    pick.dataset.track = 'capture.photo';
    pick.type = 'button';
    pick.dataset.clientId = photo.clientId;
    pick.setAttribute('aria-haspopup', 'dialog');
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
    pick.addEventListener('click', function () { openPhotoSheet(photo); });
    li.appendChild(pick);

    const seq = document.createElement('span');
    seq.className = 'seq';
    seq.textContent = String(idx + 1).padStart(2, '0');
    li.appendChild(seq);

    if (warned) {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.dataset.tone = 'attention';
      chip.textContent = warningLabel(photo.warnings);
      li.appendChild(chip);
    }

    els.thumbStrip.appendChild(li);
    if (photo.clientId === refocusId) pick.focus();
  });

  // The last tile adds more photos (both starts, in a sheet); it disables at
  // the take's max.
  const full = state.photos.length >= maxPhotos();
  const addLi = document.createElement('li');
  addLi.className = 'thumb-add';
  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'thumb-add-btn';
  add.dataset.track = 'capture.add';
  add.disabled = full;
  add.setAttribute('aria-label', full ? 'Add photos (max ' + maxPhotos() + ' reached)' : 'Add photos');
  add.innerHTML = icon('plus');
  add.addEventListener('click', openAddSheet);
  addLi.appendChild(add);
  els.thumbStrip.appendChild(addLi);

  els.thumbStrip.scrollLeft = scrollLeft;

  // Extract appears once a photo is ready to read.
  const haveReady = state.photos.some(function (p) { return p.status === 'ready'; });
  els.extractBtn.hidden = !haveReady;
  els.extractBtn.disabled = state.busy;
  // A running job rewrites the take's meta: the switch waits for it.
  const saveSwitch = els.saveToHistoryHost.querySelector('[role="switch"]');
  if (saveSwitch) saveSwitch.disabled = state.busy;
  renderView();
  renderHeadline();
}

// ----------------------------------------------------------- capture
export function handleFilePick(files) {
  if (!files || !files.length) return;
  // The Capture card is showing a History take's result: its photos aren't in
  // the strip, so a new photo belongs to a new take, not appended to that one.
  if (state.takeAdopted) resetTake();
  const maxAllowed = maxPhotos();
  const spaceLeft = maxAllowed - state.photos.length;
  if (spaceLeft <= 0) {
    toast('Reached the max (' + maxAllowed + ') for this take. Extract or start over.', 'error');
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
  // Adding to a take with a result shows its photos (Extract again is there).
  state.showPhotos = true;
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
    // Removed (or retaken) while the session was still being created.
    if (state.photos.indexOf(photo) < 0) return;
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
    // Removed while the upload was in flight: removePhoto had no server copy
    // to delete yet, so drop it now or the next order sync is one photo short.
    if (state.photos.indexOf(photo) < 0) {
      if (added) await deleteServerPhoto(sid, added.sequence_index);
      return;
    }
    photo.status = 'ready';
  } catch (exc) {
    photo.status = 'failed';
    photo.error = String(exc.message || exc);
    toast('Upload failed: ' + photo.error, 'error');
  } finally {
    renderThumbnails();
  }
}

async function deleteServerPhoto(sid, seq) {
  try {
    const body = await jsonApi(
      '/api/sessions/' + encodeURIComponent(sid) + '/photos/' + seq,
      { method: 'DELETE' }
    );
    if (sid === state.sessionId) applyServerPhotoOrder(body.photos);
  } catch (exc) {
    toast('Server delete failed: ' + (exc.message || exc), 'error');
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
    await deleteServerPhoto(state.sessionId, photo.seq);
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
