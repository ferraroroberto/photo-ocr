# 📷 Photo OCR

Mobile-first photo OCR. Snap or upload N photos of a document, screen, email, or
page; a vision-capable model on your local LLM hub returns one clean,
deduplicated, copy-ready text. No preamble, no commentary, just the text.

Sister project to [`voice-transcriber`](../voice-transcriber) — same conventions,
same archive shape, same auth model, but for pixels instead of audio.

---

## What it does

- **Multi-photo capture / upload.** iOS Safari and Android Chrome both support
  `<input type="file" accept="image/*" capture="environment" multiple>`. Tap the
  shutter, capture overlapping shots of a long email, tap **Extract**.
- **Parallel multi-photo extraction.** Each photo goes to the local LLM hub
  as its own request (`extract_chunk_size`), a few at a time
  (`extract_concurrency`; backends the hub serializes, such as Gemini, go one
  by one). Python joins the outputs in sequence order and de-duplicates the
  lines repeated at each seam. There is no second LLM "stitch" call.
- **No all-or-nothing.** Each request has its own timeout and the run has an
  overall budget. A failed photo is retried once (optionally on
  `extract_fallback_model`). If it still fails, you get the text from the
  rest with a `[missing: photo N (NN.jpg) could not be read]` line where it
  belongs, and the status payload lists `missing_photos`. "Hub down" and
  "hub still working past the timeout" get distinct messages. A partly read
  take shows one **Retry missing** action (beside the result, and in its
  History row's menu) that re-reads only the unread photos, splices their
  text into place and re-collates — **Redo** still re-reads everything.
- **Live extract progress.** The webapp starts extraction as a background job
  and polls session status, so long takes show `Chunk i of N`, `Merging`, and
  the final result instead of freezing behind one long HTTP request.
- **Clean output discipline.** The system prompt forbids preamble, commentary,
  `"Photo 1:"` labels, and translation. The result is drop-in-clipboard-ready.
- **Fleet design canon.** The UI follows the fleet design system (tokens from
  `design.md`/`design.dark.md`): two tabs — **Capture / History** — behind the
  vendored floating bottom-tab nav, Lucide icons throughout (no emoji glyphs).
  **Settings is never a tab:** every pane opens with the vendored `home-head`
  row, which carries the sun-moon **theme toggle** (it follows the system
  preference until overridden — same feature as home-automation and
  app-launcher) and the **Settings gear** that opens the Settings pane. The
  Capture card's toolbar carries the take-scoped controls: add photo /
  gallery / reset, then a labelled **Save to history** switch that keeps a
  take out of History when off. Messages use the fleet's neutral frosted
  **toast** (the nav bar's glass; only a real error tints). A build-identity footer is visible from every
  tab. Settings carries the persisted **Text size**
  (Small / Default / Large) control that makes the viewport zoom lock
  acceptable. Shared components are vendored verbatim under
  `app/webapp/static/_vendored/` from `project-scaffolding`.
- **Searchable archive.** A search box on the History tab runs full-text search
  (SQLite FTS5) over every past extract — "find the bakery receipt". The index
  lives in `archive/index.sqlite` and rebuilds itself from `extracted.txt` on
  boot, so it can be deleted at any time. Toggle with `search_enabled` in
  `webapp_config.json`.
- **History + redo.** Every take lands in `archive/YYYY/MM/DD/HH-MM-SS-<id>/`.
  Re-run with a different model from the History tab without re-capturing.
  A History row is one tap-to-copy row (snippet first, date · photos · model
  as its meta line); **Retry missing** (partly read takes only), **Redo** and
  **Delete** live in the row's overflow menu,
  Delete last and behind a confirm.
- **Pre-flight quality gate.** Each photo is scored on-device for blur,
  glare, and exposure the moment it's added — a bad shot gets an advisory
  label on its thumbnail and a one-tap Retake, before any hub round-trip is
  spent on it. Advisory only; never blocks Extract. Toggle with
  `quality_gate_enabled`.
- **One toolbar for the take's photos.** Tap a thumbnail to select it (a ring,
  `aria-pressed`; tap it again, tap elsewhere, or press Escape to clear), then
  act from the toolbar below the strip — move left/right, View, Remove, and
  Retake / Keep on a quality-warned photo — all real 44px touch targets.
  Disabled until a photo is selected.
- **No telemetry.** Images and text never leave your home PC except via the
  authenticated hub call, which itself goes to your own Google AI Pro / Claude
  subscription.

---

## Requirements

- **Windows 10/11** (tested) — POSIX should also work; the launcher batch
  files are Windows-only.
- **Python 3.11+** on `PATH`.
- **[local-llm-hub](../local-llm-hub)** running on `http://127.0.0.1:8000`
  with at least one vision-capable alias (`gemini_flash`, `gemini_pro`,
  `gemini_lite`, `claude_haiku`, `claude_sonnet`, `claude_opus`). The hub is
  the sole inference plane — this app does not call any vendor API directly.
- For Cloudflare tunnel: `cloudflared` on `PATH`
  (`winget install Cloudflare.cloudflared`).

---

## One-time setup

```powershell
# In the repo root:
setup.bat
```

This creates `.venv\`, installs `requirements.txt`, and generates the PWA
icons under `app/webapp/static/`.

---

## Quick start: access from another device over Tailscale

The webapp binds `0.0.0.0:8444`, so any device on your tailnet can reach it —
no Cloudflare tunnel needed for tailnet-only access. Four steps on the home PC,
once:

HTTPS uses a **real Let's Encrypt certificate** issued for the tailnet MagicDNS
name via `tailscale cert` — every device already on the tailnet trusts Let's
Encrypt, so there are **no per-device trust steps**: no CA to install, no iOS
profile, no Certificate Trust toggle.

```powershell
# 1. One-time per tailnet: enable HTTPS in the Tailscale admin console
#    (DNS → HTTPS Certificates), then provision the cert — it auto-detects
#    this machine's MagicDNS name and writes cert.pem / key.pem into
#    webapp/certificates/.
.\.venv\Scripts\python.exe scripts\gen_tailscale_cert.py

# 2. Open Windows Firewall on :8444 for the Private profile (Tailscale's
#    interface is Private by default). Needs admin.
New-NetFirewallRule -DisplayName "photo-ocr 8444" -Direction Inbound `
    -Protocol TCP -LocalPort 8444 -Action Allow -Profile Private

# 3. Start the webapp (foreground) — or use tray.bat for resident mode.
.\.venv\Scripts\python.exe launcher.py webapp
```

**Open on the phone or laptop** (any device on the same tailnet):
`https://<pc>.<tailnet>.ts.net:8444` — find your machine's MagicDNS name with
`tailscale status` on the home PC. The lock is green immediately; **Add to Home
Screen** installs the PWA with no certificate fuss. (`https://localhost:8444`
warns — the cert is for the `.ts.net` name, not loopback; plain desktop access
on the PC itself is `http://localhost:8444`.)

> **Auto-renew (no calendar reminder needed).** A Let's Encrypt leaf is valid
> ~90 days, so renewal is automated rather than manual: both boot paths
> (`webapp.bat` and the tray's `WebappManager.start()`) run
> `gen_tailscale_cert.py --check` on startup, which re-issues the cert only
> when it is a `.ts.net` cert expiring within 30 days (a no-op otherwise).

**Auth gate (optional)** — by default the gate is **off** and any tailnet
device can use the app. To require a token + password:
```powershell
.\.venv\Scripts\python.exe scripts\gen_token.py
.\.venv\Scripts\python.exe scripts\set_password.py "your-password"
```
Tailscale IPs are *not* in the loopback bypass; the phone will see a login
overlay on first visit and swap the password for a bearer token that's stashed
in `localStorage`.

---

## Day-to-day usage

### Tray (recommended)

```powershell
tray.bat
```

A system-tray icon appears. It spawns the webapp on `:8444` and stays resident.
If `webapp/cloudflared.yml` exists, the tray also launches a named Cloudflare
tunnel in the background so the public URL is up alongside the webapp.

- **Left-click** the tray icon → opens the webapp in your default browser.
- **Right-click** for the menu:
  - **📷 Open photo OCR** — opens the webapp.
  - **📋 Copy local URL** — clipboard the loopback URL (with `?token=…` if
    `auth_token` is set).
  - **📋 Copy Tailscale URL** — clipboard `https://<tailscale-host>:8444`
    (with `?token=…` if set). Resolves the tailnet hostname via
    `tailscale status --json`; greyed-out feedback if Tailscale isn't
    installed or logged in.
  - **📋 Copy Cloudflare URL** — clipboard the public URL from
    `webapp/last_tunnel_url.txt` (written by the tray when the named
    tunnel comes up).
  - **🔄 Restart webapp** — stop + start uvicorn so a fresh pull is
    picked up without quitting the tray.
  - **ℹ️ Status** — popup with hub + webapp state.
  - **🚪 Quit** — stops cloudflared (if running) and the webapp.

**Self-heal (issue #110):** the initial webapp spawn at tray boot retries
with backoff (5s / 15s / 30s) instead of giving up after one attempt, and a
background health watchdog polls `/healthz` every 60s — a webapp that's
crashed and stopped listening is auto-respawned; one that's listening but not
answering (wedged) only alerts, since auto-killing a stuck process could mask
what's actually wrong (use **🔄 Restart webapp** for that case). Every
start/retry/wedge/respawn/recovery event is logged to `webapp/watchdog.log`
(gitignored) — the tray's own `logging` output goes nowhere useful under
`pythonw` (no console), so this file is the actual diagnostic trail.

### Webapp without the tray

```powershell
webapp.bat
```

Uvicorn boots in the foreground on `:8444`. HTTPS if `webapp/certificates/`
exists, HTTP otherwise. Ctrl+C to stop.

### CLI (scripting / smoke tests)

```powershell
.\.venv\Scripts\python.exe launcher.py extract photo1.jpg photo2.jpg --model gemini_flash
```

Prints the extracted text to stdout. No archive, no session — quick one-off.

---

## Repo layout

```
photo-ocr/
├── launcher.py                  entry point — `python launcher.py <command>`
├── setup.bat                    one-shot installer (creates .venv, deps, icons)
├── tray.bat                     start the system-tray launcher
├── webapp.bat                   standalone FastAPI on :8444
├── webapp_tunnel_named.bat      webapp + named Cloudflare tunnel
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── package.json                 (optional Vitest harness for the JS modules)
├── CLAUDE.md                    project instructions for coding agents
├── AGENTS.md                    one-line pointer to CLAUDE.md
├── README.md                    this file
├── src/                         logic layer — no UI imports
│   ├── app_config.py
│   ├── webapp_config.py
│   ├── image_utils.py           validate, EXIF-rotate, downscale, persist
│   ├── ocr_client.py            local-llm-hub /v1/messages client
│   ├── ocr_prompts.py           prompt library loader
│   ├── archive.py               dated session folders + retention
│   ├── archive_index.py         SQLite FTS5 full-text search index
│   ├── extract_job.py           chunked extraction job engine: locking,
│   │                            progress state machine, archive writes, search index
│   ├── cloudflared_runner.py    spawn/monitor the named tunnel
│   ├── static_versioning.py     cache-busting query param
│   └── runtime_data.py          resolves the SQLite data-root path (machine-level, not hardcoded)
├── app/
│   ├── cli/                     argparse dispatcher
│   ├── webapp/                  FastAPI app + manager
│   │   ├── server.py            app factory, static mount, router includes
│   │   ├── middleware.py        bearer-token / loopback auth gate
│   │   ├── routers/             APIRouter per concern (misc, config, auth, sessions, search)
│   │   ├── manager.py
│   │   └── static/              PWA: index.html, ES-module JS, styles.css, icons
│   │       └── _vendored/       fleet components (nav · home-head · icons · card ·
│   │                            button · toast · empty-state · base · range-tab · text-size ·
│   │                            action-row · modal · switch · icon-button · select-native),
│   │                            copied verbatim from project-scaffolding
│   └── tray/                    system-tray launcher
├── config/
│   ├── config.json              app-level (log level, language hint)
│   ├── ocr_prompts.json         committed prompt library
│   ├── webapp_config.json       runtime UI prefs (gitignored)
│   └── webapp_config.sample.json
├── scripts/
│   ├── gen_icons.py             thin caller onto project-scaffolding's shared brand_gen.py (camera master)
│   ├── gen_tailscale_cert.py    HTTPS via `tailscale cert` (real LE leaf, `--check` auto-renew)
│   ├── gen_token.py             generate/rotate the bearer token
│   ├── set_password.py          set/clear the login password
│   ├── run_named_tunnel.py      uvicorn + cloudflared
│   ├── verify-before-ship.ps1   pre-ship gate: byte-compile + pytest + e2e
│   ├── run-e2e.ps1              e2e leg runner (used by the pre-ship gate)
│   └── classify_e2e.py          branch-diff → e2e tier (skip/static/full)
├── assets/                      generated by scripts/gen_icons.py, committed
│   ├── tray/photo-ocr.ico            Windows tray icon (16/32/48/64/256)
│   └── stream-deck/photo-ocr-144.png Elgato Stream Deck button
├── webapp/
│   ├── cloudflared.sample.yml   committed tunnel config template
│   ├── cloudflared.yml          (gitignored — UUID + hostname)
│   └── certificates/            (gitignored)
├── archive/                     (gitignored — sessions on disk)
└── tests/                       pytest suite
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  iPhone / Android (PWA installed to Home Screen)                │
│                                                                 │
│  [📷 Add photo] capture or pick from gallery, N times           │
│  ┌──────┐ ┌──────┐ ┌──────┐  reorder, delete, preview           │
│  │ #1   │ │ #2   │ │ #3   │                                    │
│  └──────┘ └──────┘ └──────┘                                    │
│  [🔍 Extract text]                                              │
│  ┌────────────────────────────────────────────────────┐         │
│  │ <extracted text — editable>                        │         │
│  └────────────────────────────────────────────────────┘         │
│  [📋 Copy]                                                       │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTPS, multipart upload
                                │ Bearer token + Cloudflare Access
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Home PC                                                        │
│                                                                 │
│  photo-ocr webapp (FastAPI on :8444)                            │
│   ├── /api/extract                   single-shot: images→text   │
│   ├── /api/sessions                  create session             │
│   ├── /api/sessions/{id}/photos      append 1..N photos         │
│   ├── /api/sessions/{id}/extract     start OCR job              │
│   ├── /api/sessions/{id}/extract/status  poll chunk progress    │
│   ├── /api/sessions/{id}/redo        start re-run with new model│
│   ├── /api/sessions/{id}/retry-missing  re-read unread photos   │
│   ├── /api/sessions                  list (newest first)        │
│   ├── /api/search                    full-text search (FTS5)     │
│   ├── /api/config, /api/login, /api/version, …                 │
│   └── archive/YYYY/MM/DD/<id>/  01.jpg…NN.jpg + extracted.txt   │
│                                                                 │
│  local-llm-hub  ──── /v1/messages (vision)  on :8000            │
│                                                                 │
│  cloudflared  ────  ocr.<your-domain>  (named tunnel)           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Configuration

### `config/config.json` (committed default)

```json
{
  "log_level": "INFO",
  "default_language_hint": null,
  "webapp": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8444
  }
}
```

The `webapp` section is optional (all three keys default as shown). **This is the bind config for the recommended tray/manager path** (`tray.bat` → `app/webapp/manager.py`). To change the port the tray listens on, set `webapp.port` here.

| Key | Default | Notes |
|---|---|---|
| `log_level` | `INFO` | Python logging level. |
| `default_language_hint` | `null` | Language hint passed to the OCR prompt (e.g. `"Spanish"`). `null` lets the model auto-detect. |
| `webapp.enabled` | `true` | Set to `false` to run the tray without starting the webapp. |
| `webapp.host` | `0.0.0.0` | Bind address used by the tray/manager. Change this to restrict access (e.g. `127.0.0.1`). |
| `webapp.port` | `8444` | Port used by the tray/manager. |

### `config/webapp_config.json` (gitignored)

Created on first **💾 Save defaults** tap. Schema lives in
`config/webapp_config.sample.json`:

| Key | Default | Notes |
|---|---|---|
| `ocr_model_default` | `claude_opus` | Alias on the local-llm-hub. Fastest complete read of the #166 8-photo replay (29 s, vs 37 s on `claude_sonnet`; `gemini_flash`, which the hub runs one call at a time, read only 5 of 8 within the 900 s budget at 9–382 s per photo). |
| `ocr_models_available` | gemini × 3, claude × 3 | Drives the picker. |
| `ocr_prompt_default` | `verbatim-merge` | One of the entries in `config/ocr_prompts.json`. |
| `llm_hub_url` | `http://127.0.0.1:8000` | Local-llm-hub address. |
| `host` | `0.0.0.0` | Bind address — **`launcher.py webapp` CLI only**. Ignored by the tray; use `config/config.json`'s `webapp.host` for that. |
| `port` | `8444` | Port — **`launcher.py webapp` CLI only**. Ignored by the tray; use `config/config.json`'s `webapp.port` for that. |
| `history_retention_days` | `30` | Sessions older than this are pruned on startup. |
| `max_photos_per_session` | `50` | Hard cap on photos per take. |
| `max_photo_dimension_px` | `2048` | Long-edge resize before sending to the hub. |
| `extract_chunk_size` | `1` | Photos per hub request. Requests of more than one photo overlap by one photo. |
| `extract_request_timeout_s` | `420` | Seconds one hub request may take before it is abandoned and retried. |
| `extract_run_budget_s` | `900` | Wall-clock budget for a whole extraction, retries included; photos still unread when it runs out are reported missing. |
| `extract_concurrency` | `4` | Hub requests in flight at once (1–16). Serialized hub backends (Gemini) always run one at a time. |
| `extract_fallback_model` | `""` | Model a failed photo retries on once. Empty = retry on the same model. |
| `single_shot_max_photos` | `8` | Max images the synchronous `POST /api/extract` consumable endpoint accepts (see [Consumable API](#consumable-api-vendor-the-ocr-into-other-apps)). Bigger takes use the async session flow. |
| `search_enabled` | `true` | Full-text search over the session archive (SQLite FTS5). `false` hides the search box and writes no index. |
| `quality_gate_enabled` | `true` | On-device pre-flight image quality gate. `false` skips client-side blur/glare/exposure scoring entirely. |
| `auth_token` | `""` | Empty = auth gate **off**. Set via `scripts/gen_token.py`. |
| `auth_password` | `""` | Optional companion password — see auth section. |

### `config/ocr_prompts.json` (committed)

Four entries ship by default: `verbatim-merge` (default), `structured-markdown`,
`plain-stripped`, `code-fenced`. Add more by appending entries — no code change.

---

## Optional: bearer-token auth + password gate

Run once on the home PC:

```powershell
.\.venv\Scripts\python.exe scripts\gen_token.py
.\.venv\Scripts\python.exe scripts\set_password.py "your-password"
```

- Loopback requests still bypass the gate.
- Remote requests (Cloudflare tunnel) need either the `?token=…` query param
  or the password.
- Failed password attempts are logged with client IP to `webapp/auth.log`.

---

## Persistent URL via named Cloudflare tunnel

One-time setup:

```powershell
cloudflared tunnel login
cloudflared tunnel create ocr
cloudflared tunnel route dns ocr ocr.<your-domain>
copy webapp\cloudflared.sample.yml webapp\cloudflared.yml
# edit webapp\cloudflared.yml — fill in UUID + hostname
```

Once `webapp/cloudflared.yml` is in place, `tray.bat` spawns cloudflared
automatically alongside the webapp — the persistent URL (with `?token=…`
if a token is configured) is written to `webapp/last_tunnel_url.txt` so
the tray's **📋 Copy Cloudflare URL** reads from this file. Quitting the
tray stops the tunnel.

For headless / no-tray use, `webapp_tunnel_named.bat` does the same thing
in the foreground (uvicorn + cloudflared, Ctrl+C to stop).

Behind Cloudflare, set up an **Access policy** for the hostname so the
public URL is gated by your Google sign-in.

---

## Storage budget

50 photos × 2048 px long-edge JPEG q=85 ≈ 25 MB per take. At 5 takes/day for
30 days that's ~3.75 GB. The retention cleanup runs on every boot.

---

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not smoke"
.\.venv\Scripts\python.exe -m py_compile launcher.py
```

The `smoke` marker is the slow live-tray bucket — see the Playwright section below. The rest of the suite uses `TestClient` + mocked hub responses.

### Playwright browser smoke tests

A `pytest-playwright` suite under `tests/e2e/` catches SPA boot regressions (JS errors, empty `<select>`s, broken settings toggle, missing login overlay) plus regression nets for past iPhone-only bugs (cache-busting, cert lifetime, photo upload) and the Capture thumbnail toolbar (selection, 44px targets, move/remove/retake). Instance selection is guarded by the vendor-verbatim `tests/e2e/_e2e_live_guard.py` (project-scaffolding issue #191/#194, adopted fleet-wide byte-identical — issue #108): a bare `pytest tests/e2e` with the tray up **refuses to run** with a guard message rather than silently adopting it; with the tray down it boots its own disposable instance instead. Set `PHOTO_OCR_E2E_LIVE=1` (`scripts/run-e2e.ps1` does) to explicitly *adopt* the live tray on `https://127.0.0.1:8444` for read-only smoke checks — this repo never kills it to satisfy the opt-in (`tray.bat --restart` owns that).

By default the suite runs in **two projections**: Chromium desktop and WebKit projected onto an iPhone 14 (viewport, user-agent, touch). WebKit is iOS Mobile Safari's engine family, so the second projection catches most "Safari is unhappy" regressions on Windows. Pin one engine with `--browser chromium` for a faster dev loop; a test tagged `@pytest.mark.desktop_only` skips the WebKit projection.

One-time setup:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m playwright install chromium webkit
```

Then with the tray running (`tray.bat`):

```powershell
.\scripts\run-e2e.ps1
# or directly:
$env:PHOTO_OCR_E2E_LIVE = "1"
& .\.venv\Scripts\python.exe -m pytest -m smoke -v tests/e2e
```

Without a tray, a bare run already boots its own disposable webapp on a free port (the live-tray port being free means nothing to adopt). `--e2e-autoboot` (or `PHOTO_OCR_E2E_AUTOBOOT=1`) forces that same disposable path unconditionally, regardless of whether a tray is running — this is what the pre-ship gate uses, so it never touches a live tray.

### Verifying changes before ship

`scripts/verify-before-ship.ps1` is the single pre-ship gate. It byte-compiles `app`/`src`/`tests`, runs the non-e2e pytest suite, then runs the Playwright e2e suite (Chromium + WebKit/iPhone) against a **disposable webapp it boots itself on a free port** — so a forgotten tray can't let a regression slip through as a skipped suite.

The e2e leg is proportionate to the diff: `scripts/classify_e2e.py` classifies the branch's changed files against `origin/main` (rules in `.fleet.toml` `[e2e]`). A docs-only branch **skips** the leg and the gate prints the tier and reason; a static-image change runs only the smoke slice; anything under `app/webapp/`, `src/`, or any path no rule names runs the **full** suite. A classifier that errors, prints no tier, or returns an unknown one also runs the full suite, and says so — the gate never skips on doubt.

```powershell
powershell.exe -File scripts/verify-before-ship.ps1
```

Exits non-zero on the first failure with the output left visible; prints total wall time and `Ready to ship` when green. Re-runnable with no manual cleanup. Any change under `app/webapp/` must pass it before being declared done. Use Windows PowerShell 5.1 (`powershell.exe`), not `pwsh` — the default `pwsh` on PATH is a 0-byte WindowsApps reparse stub that fails non-interactively.

---

## Consumable API: vendor the OCR into other apps

photo-ocr is the canonical local **image→text** service in the fleet — the pixel counterpart to `voice-transcriber` (audio→text) and `local-llm-hub` (the LLM plane). Any app on the same PC can hand it images and get clean text back over loopback, no auth, instead of re-implementing the capture → chunk → vision-hub → merge stack.

The simplest path is one call:

```bash
curl -sk -X POST "https://127.0.0.1:8444/api/extract?model=claude_opus" \
  -F files=@screenshot.png | jq -r .text
```

Every externally-triggered extraction lands in History attributed to its caller (pass `?source=app-launcher`), so History stays the fleet's single source of truth for all OCR — searchable and recoverable, not just PWA takes.

Full contract — single-shot **and** the async progress-polling flow, base-URL/TLS/auth, source attribution, error table, examples, and the stability changelog — is in **[`docs/consuming-the-session-api.md`](docs/consuming-the-session-api.md)**. First consumer: app-launcher's Coding-terminal "paste screenshot" button (`ferraroroberto/app-launcher#171`).

---

## Sister projects

- [`voice-transcriber`](../voice-transcriber) — voice → text via whisper.cpp +
  optional LLM polish. Shares conventions, archive shape, auth model.
- [`local-llm-hub`](../local-llm-hub) — the inference plane on `:8000` that
  both apps call.
