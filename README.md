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
- **One model call, one shot.** All photos go to the local LLM hub in one
  request. The model concatenates pages in reading order and merges overlapping
  lines — each unique line once.
- **Clean output discipline.** The system prompt forbids preamble, commentary,
  `"Photo 1:"` labels, and translation. The result is drop-in-clipboard-ready.
- **Searchable archive.** A 🔎 box on the history panel runs full-text search
  (SQLite FTS5) over every past extract — "find the bakery receipt". The index
  lives in `archive/index.sqlite` and rebuilds itself from `extracted.txt` on
  boot, so it can be deleted at any time. Toggle with `search_enabled` in
  `webapp_config.json`.
- **History + redo.** Every take lands in `archive/YYYY/MM/DD/HH-MM-SS-<id>/`.
  Re-run with a different model from the history panel without re-capturing.
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

```powershell
# 1. Generate the self-signed cert. gen_ssl_cert.py auto-discovers your
#    Tailscale hostname + IP via `tailscale status --json` / `tailscale ip -4`
#    and adds them to the cert's SAN list — so https://<tailscale-hostname>:8444 is valid
#    out of the box once the CA is trusted on the client device.
.\.venv\Scripts\python.exe scripts\gen_ssl_cert.py

# 2. Open Windows Firewall on :8444 for the Private profile (Tailscale's
#    interface is Private by default). Needs admin.
New-NetFirewallRule -DisplayName "photo-ocr 8444" -Direction Inbound `
    -Protocol TCP -LocalPort 8444 -Action Allow -Profile Private

# 3. Start the webapp (foreground) — or use tray.bat for resident mode.
.\.venv\Scripts\python.exe launcher.py webapp
```

**Open on the phone or laptop** (any device on the same tailnet):
- `https://<tailscale-hostname>:8444` — find your machine's name with
  `tailscale status` on the home PC (or in the Tailscale admin console)
- or `https://<100.x.y.z>:8444` — the home PC's tailnet IPv4 from
  `tailscale ip -4`

**iOS PWA install** — open `https://<tailscale-hostname>:8444/install-ca` once,
install the profile, then go to **Settings → General → About → Certificate
Trust Settings** and enable full trust on "Photo OCR Local CA". After that
Safari trusts the cert and you can **Add to Home Screen**.

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
│   └── diagnostics.py
├── app/
│   ├── cli/                     argparse dispatcher
│   ├── webapp/                  FastAPI app + manager
│   │   ├── server.py            app factory, static mount, router includes
│   │   ├── middleware.py        bearer-token / loopback auth gate
│   │   ├── routers/             APIRouter per concern (misc, config, auth, sessions, search)
│   │   ├── manager.py
│   │   └── static/              PWA: index.html, ES-module JS, styles.css, icons
│   └── tray/                    system-tray launcher
├── config/
│   ├── config.json              app-level (log level, language hint)
│   ├── ocr_prompts.json         committed prompt library
│   ├── webapp_config.json       runtime UI prefs (gitignored)
│   └── webapp_config.sample.json
├── scripts/
│   ├── gen_icons.py             PNG icon generator (RGB — iOS-friendly)
│   ├── gen_ssl_cert.py          self-signed loopback HTTPS cert
│   ├── gen_token.py             generate/rotate the bearer token
│   ├── set_password.py          set/clear the login password
│   └── run_named_tunnel.py      uvicorn + cloudflared
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
│   ├── /api/sessions                  create session             │
│   ├── /api/sessions/{id}/photos      append 1..N photos         │
│   ├── /api/sessions/{id}/extract     run OCR on all photos      │
│   ├── /api/sessions/{id}/redo        re-run with new model      │
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
  "default_language_hint": null
}
```

### `config/webapp_config.json` (gitignored)

Created on first **💾 Save defaults** tap. Schema lives in
`config/webapp_config.sample.json`:

| Key | Default | Notes |
|---|---|---|
| `ocr_model_default` | `gemini_flash` | Alias on the local-llm-hub. |
| `ocr_models_available` | gemini × 3, claude × 3 | Drives the picker. |
| `ocr_prompt_default` | `verbatim-merge` | One of the entries in `config/ocr_prompts.json`. |
| `llm_hub_url` | `http://127.0.0.1:8000` | Local-llm-hub address. |
| `port` | `8444` | Webapp HTTPS/HTTP port. |
| `history_retention_days` | `30` | Sessions older than this are pruned on startup. |
| `max_photos_per_session` | `50` | Hard cap on photos per take. |
| `max_photo_dimension_px` | `2048` | Long-edge resize before sending to the hub. |
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

A `pytest-playwright` suite under `tests/e2e/` catches SPA boot regressions (JS errors, empty `<select>`s, broken settings toggle, missing login overlay) plus regression nets for past iPhone-only bugs (cache-busting, cert lifetime, photo upload). Runs against the **live tray on `https://127.0.0.1:8444`** — does not boot anything itself; if the tray isn't up, every test is skipped with a clear message.

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
& .\.venv\Scripts\python.exe -m pytest -m smoke -v tests/e2e
```

### Verifying changes before ship

`scripts/verify-before-ship.ps1` is the single pre-ship gate. It byte-compiles `app`/`src`/`tests`, runs the non-e2e pytest suite, then runs the Playwright e2e suite (Chromium + WebKit/iPhone) against a **disposable webapp it boots itself on a free port** — so a forgotten tray can't let a regression slip through as a skipped suite.

```powershell
pwsh -File scripts/verify-before-ship.ps1
```

Exits non-zero on the first failure with the output left visible; prints total wall time and `Ready to ship` when green. Re-runnable with no manual cleanup. Any change under `app/webapp/` must pass it before being declared done.

---

## Sister projects

- [`voice-transcriber`](../voice-transcriber) — voice → text via whisper.cpp +
  optional LLM polish. Shares conventions, archive shape, auth model.
- [`local-llm-hub`](../local-llm-hub) — the inference plane on `:8000` that
  both apps call.
