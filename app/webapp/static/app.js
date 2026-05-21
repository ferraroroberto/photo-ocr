/* Photo OCR — mobile-first single-page app.
 *
 * State machine (single source of truth):
 *
 *   state.sessionId       — server-assigned on first photo upload (lazy)
 *   state.photos          — [{ clientId, seq, file, previewUrl, status, error }]
 *   state.extracted       — current extracted text
 *   state.model           — selected model alias
 *   state.promptId        — selected prompt id
 *   state.prompts         — [{ id, label, description, system }]
 *   state.config          — full /api/config response
 *   state.busy            — boolean: an extract is in flight
 *   state.incognito       — boolean: don't save this take to history
 *
 * Auth: a bearer token is stored in localStorage under TOKEN_KEY. The
 * page extracts it from ?token=… on first load (then strips it from
 * the visible URL). On 401, we show the login overlay; the password
 * is swapped for the token via POST /api/login.
 */

(function () {
  'use strict';

  const TOKEN_KEY = 'photo-ocr.token';
  const PROMPT_KEY = 'photo-ocr.promptId';
  const MODEL_KEY = 'photo-ocr.model';
  const HISTORY_PAGE_SIZE = 10;

  const state = {
    sessionId: null,
    photos: [],
    extracted: '',
    model: null,
    promptId: null,
    prompts: [],
    config: null,
    busy: false,
    incognito: false,
    historyOffset: 0,
    historyItems: [],
    historyTotal: 0,
  };

  // ----------------------------------------------------------------- DOM
  const els = {
    thumbStrip: document.getElementById('thumbStrip'),
    cameraInput: document.getElementById('cameraInput'),
    galleryInput: document.getElementById('galleryInput'),
    extractBtn: document.getElementById('extractBtn'),
    captureStatus: document.getElementById('captureStatus'),
    extracted: document.getElementById('extracted'),
    copyExtracted: document.getElementById('copyExtracted'),
    resetBtn: document.getElementById('resetBtn'),
    incognitoToggle: document.getElementById('incognitoToggle'),
    ocrModel: document.getElementById('ocrModel'),
    ocrStyle: document.getElementById('ocrStyle'),
    ocrPromptPreview: document.getElementById('ocrPromptPreview'),
    retentionDays: document.getElementById('retentionDays'),
    maxPhotos: document.getElementById('maxPhotos'),
    saveSettings: document.getElementById('saveSettings'),
    statusReadout: document.getElementById('statusReadout'),
    buildInfo: document.getElementById('buildInfo'),
    historyCount: document.getElementById('historyCount'),
    historyList: document.getElementById('historyList'),
    refreshHistory: document.getElementById('refreshHistory'),
    cleanAll: document.getElementById('cleanAll'),
    loadMoreHistory: document.getElementById('loadMoreHistory'),
    toast: document.getElementById('toast'),
    previewDialog: document.getElementById('previewDialog'),
    previewImg: document.getElementById('previewImg'),
    previewClose: document.getElementById('previewClose'),
    loginOverlay: document.getElementById('loginOverlay'),
    loginForm: document.getElementById('loginForm'),
    loginPassword: document.getElementById('loginPassword'),
    loginError: document.getElementById('loginError'),
  };

  // ----------------------------------------------------------- auth utils
  function tokenFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const t = (params.get('token') || '').trim();
    if (!t) return null;
    params.delete('token');
    const newQuery = params.toString();
    const newUrl =
      window.location.pathname +
      (newQuery ? '?' + newQuery : '') +
      window.location.hash;
    window.history.replaceState({}, '', newUrl);
    return t;
  }
  function readToken() {
    return localStorage.getItem(TOKEN_KEY) || '';
  }
  function writeToken(t) {
    if (t) localStorage.setItem(TOKEN_KEY, t);
  }
  function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = new Headers(opts.headers || {});
    const token = readToken();
    if (token) headers.set('Authorization', 'Bearer ' + token);
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
      showLogin();
      throw new Error('auth required');
    }
    return res;
  }

  async function jsonApi(path, opts) {
    const res = await api(path, opts);
    let body = null;
    try {
      body = await res.json();
    } catch (_) {
      body = null;
    }
    if (!res.ok) {
      const detail = (body && body.detail) || ('HTTP ' + res.status);
      const err = new Error(detail);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  // ----------------------------------------------------------- login UI
  function showLogin() {
    if (!els.loginOverlay) return;
    els.loginOverlay.hidden = false;
    els.loginPassword.value = '';
    els.loginPassword.focus();
  }
  function hideLogin() {
    if (els.loginOverlay) els.loginOverlay.hidden = true;
  }
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

  // ----------------------------------------------------------- toasts
  let toastTimer = null;
  function toast(msg, kind) {
    els.toast.textContent = msg;
    els.toast.className = 'toast ' + (kind || '');
    els.toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      els.toast.hidden = true;
    }, kind === 'error' ? 4500 : 2200);
  }

  // ----------------------------------------------------------- rendering
  function clientId() {
    return 'c' + Math.random().toString(36).slice(2, 10);
  }

  function setStatus(text) {
    els.captureStatus.textContent = text || '';
  }

  function renderThumbnails() {
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

  function renderExtracted() {
    els.extracted.value = state.extracted || '';
    els.copyExtracted.disabled = !state.extracted;
  }

  function renderSettings() {
    if (!state.config) return;

    // Models
    els.ocrModel.innerHTML = '';
    (state.config.ocr_models_available || []).forEach(function (m) {
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      if (m === state.model) opt.selected = true;
      els.ocrModel.appendChild(opt);
    });

    // Styles
    els.ocrStyle.innerHTML = '';
    (state.prompts || []).forEach(function (p) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.label || p.id;
      if (p.id === state.promptId) opt.selected = true;
      els.ocrStyle.appendChild(opt);
    });

    refreshPromptPreview();

    els.retentionDays.value = state.config.history_retention_days || 30;
    els.maxPhotos.value = state.config.max_photos_per_session || 50;
  }

  function refreshPromptPreview() {
    const prompt = (state.prompts || []).find(function (p) { return p.id === state.promptId; });
    els.ocrPromptPreview.value = prompt ? prompt.system : '';
  }

  // ----------------------------------------------------------- capture
  function handleFilePick(files) {
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

  // ----------------------------------------------------------- extract
  async function extract() {
    if (!state.sessionId) {
      toast('No session yet — add a photo first.', 'error');
      return;
    }
    if (state.busy) return;
    const readyPhotos = state.photos.filter(function (p) { return p.status === 'ready'; });
    if (!readyPhotos.length) {
      toast('No photos ready yet.', 'error');
      return;
    }
    state.busy = true;
    els.extractBtn.classList.add('busy');
    els.extractBtn.disabled = true;
    setStatus(
      'LLM hub → ' + state.model + ' · extracting from ' + readyPhotos.length + ' photo(s)…'
    );

    const t0 = Date.now();
    try {
      const body = await jsonApi(
        '/api/sessions/' + encodeURIComponent(state.sessionId) + '/extract',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: state.model, prompt_id: state.promptId }),
        }
      );
      state.extracted = body.extracted || '';
      renderExtracted();
      const seconds = ((Date.now() - t0) / 1000).toFixed(1);
      if (body.reused) {
        setStatus('Already extracted — showing cached result · ' + seconds + ' s');
      } else if (!state.extracted) {
        setStatus('No readable text detected · ' + seconds + ' s');
      } else {
        setStatus('Done in ' + seconds + ' s — tap Copy');
      }
      loadHistory(0);
    } catch (exc) {
      setStatus('❌ ' + (exc.message || exc));
      toast('Extract failed: ' + (exc.message || exc), 'error');
    } finally {
      state.busy = false;
      els.extractBtn.classList.remove('busy');
      renderThumbnails();
    }
  }

  // ----------------------------------------------------------- copy
  async function copyExtracted() {
    const txt = state.extracted || '';
    if (!txt) return;
    try {
      // Prefer the ClipboardItem path with text/plain only — voice-transcriber's
      // troubleshooting table flags styled-DOM leakage with the writeText
      // path on some Safari versions; force the MIME explicitly.
      if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
        const blob = new Blob([txt], { type: 'text/plain' });
        const item = new ClipboardItem({ 'text/plain': blob });
        await navigator.clipboard.write([item]);
      } else if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(txt);
      } else {
        // Fallback for very old browsers.
        const ta = document.createElement('textarea');
        ta.value = txt;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      els.copyExtracted.classList.add('copied');
      const original = els.copyExtracted.textContent;
      els.copyExtracted.textContent = '✓ Copied';
      setTimeout(function () {
        els.copyExtracted.classList.remove('copied');
        els.copyExtracted.textContent = original;
      }, 1200);
    } catch (exc) {
      toast('Copy failed: ' + (exc.message || exc), 'error');
    }
  }

  // ----------------------------------------------------------- reset
  function resetTake() {
    state.photos.forEach(function (p) {
      if (p.previewUrl) {
        try { URL.revokeObjectURL(p.previewUrl); } catch (_) {}
      }
    });
    state.photos = [];
    state.sessionId = null;
    state.extracted = '';
    renderThumbnails();
    renderExtracted();
    setStatus('Add a photo to begin');
  }

  // ----------------------------------------------------------- history
  async function loadHistory(offset) {
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

  async function cleanAllHistory() {
    if (!confirm('Delete all saved takes?')) return;
    try {
      await jsonApi('/api/sessions', { method: 'DELETE' });
      loadHistory(0);
      toast('History cleared.', 'good');
    } catch (exc) {
      toast('Clean failed: ' + (exc.message || exc), 'error');
    }
  }

  // ----------------------------------------------------------- preview dialog
  function openPreview(url) {
    if (!els.previewDialog) return;
    els.previewImg.src = url;
    if (els.previewDialog.showModal) {
      els.previewDialog.showModal();
    } else {
      els.previewDialog.hidden = false;
    }
  }
  function closePreview() {
    if (els.previewDialog.close) els.previewDialog.close();
    els.previewDialog.hidden = true;
    els.previewImg.src = '';
  }
  els.previewClose.addEventListener('click', closePreview);
  els.previewDialog.addEventListener('click', function (ev) {
    if (ev.target === els.previewDialog) closePreview();
  });

  // ----------------------------------------------------------- drag & drop
  function setupDragDrop() {
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

  // ----------------------------------------------------------- boot
  async function fetchConfig() {
    const body = await jsonApi('/api/config');
    state.config = body;
    state.prompts = body.ocr_prompts || [];
    state.model =
      localStorage.getItem(MODEL_KEY) ||
      body.ocr_model_default ||
      (body.ocr_models_available || [])[0] ||
      'gemini_flash';
    state.promptId =
      localStorage.getItem(PROMPT_KEY) ||
      body.ocr_prompt_default ||
      (state.prompts[0] && state.prompts[0].id) ||
      'verbatim-merge';
    renderSettings();
  }

  async function fetchStatus() {
    try {
      const body = await jsonApi('/api/status');
      const hub = body.llm_hub || {};
      els.statusReadout.textContent =
        'Hub: ' + (hub.reachable ? '✅' : '❌') + ' · ' + (hub.base_url || '?');
    } catch (_) {
      els.statusReadout.textContent = '';
    }
  }

  // Surface the loaded build in the Settings panel so "is the phone
  // running the current code?" is answerable at a glance — see issue #5.
  // /api/version is auth-exempt, so a plain fetch avoids the login
  // overlay even before the user has signed in.
  async function loadVersion() {
    if (!els.buildInfo) return;
    try {
      const res = await fetch('/api/version');
      if (!res.ok) throw new Error(String(res.status));
      const v = await res.json();
      const when = String(v.built_at || '')
        .replace('T', ' ')
        .replace(/(\+00:00|Z)$/, ' UTC');
      els.buildInfo.textContent =
        ('Build: ' + (v.git_sha || '?') + ' · ' + when).trim();
    } catch (_) {
      // Non-critical — leave a quiet placeholder rather than alarming.
      els.buildInfo.textContent = 'Build: unavailable';
    }
  }

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
    await fetchStatus();
    await loadHistory(0);
    renderThumbnails();
    renderExtracted();
  }

  // ----------------------------------------------------------- wire up
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
  els.resetBtn.addEventListener('click', resetTake);
  els.incognitoToggle.addEventListener('change', function () {
    state.incognito = !!els.incognitoToggle.checked;
  });

  els.ocrModel.addEventListener('change', function () {
    state.model = els.ocrModel.value;
    localStorage.setItem(MODEL_KEY, state.model);
  });
  els.ocrStyle.addEventListener('change', function () {
    state.promptId = els.ocrStyle.value;
    localStorage.setItem(PROMPT_KEY, state.promptId);
    refreshPromptPreview();
  });
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
      toast('Defaults saved.', 'good');
    } catch (exc) {
      toast('Save failed: ' + (exc.message || exc), 'error');
    }
  });

  els.refreshHistory.addEventListener('click', function () { loadHistory(0); });
  els.cleanAll.addEventListener('click', cleanAllHistory);
  els.loadMoreHistory.addEventListener('click', function () {
    loadHistory(state.historyOffset + HISTORY_PAGE_SIZE);
  });

  setupDragDrop();
  boot();
})();
