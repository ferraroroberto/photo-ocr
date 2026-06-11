# Consuming the OCR session API

A guide for **downstream apps** that want clean, deduplicated text out of one or more photos/screenshots without re-implementing the `capture → validate → chunk → vision-hub → overlap-merge` plumbing. Call this app's API instead.

This is a **supported, consumable integration surface**. photo-ocr is the canonical local **image→text** service in the fleet — the pixel counterpart to [`voice-transcriber`](https://github.com/ferraroroberto/voice-transcriber) (the canonical local **audio→text** service) and [`claude-local-calls`](https://github.com/ferraroroberto/claude-local-calls) (the canonical LLM hub). Downstream apps proxy to it over loopback rather than duplicating the OCR stack.

First consumer: **app-launcher**'s Coding-terminal "paste screenshot" button (companion issue `ferraroroberto/app-launcher#171`), which proxies a screenshot here and drops the extracted text into the compose bar — exactly how its 🎤 dictation button consumes the voice-transcriber.

> **Contract status.** The `/api/extract`, `/api/sessions*`, `/api/version`, and `/healthz` routes documented here are a **stable contract**. Breaking changes are recorded in the [Changelog](#changelog) at the bottom of this file. Pin the build you tested against via `GET /api/version` (`git_sha`) if you need certainty across upgrades.

---

## TL;DR

Two ways to get text out of images:

1. **Single-shot** — you have a small set of images (a screenshot or two) and want text back in **one call**: `POST /api/extract` (multipart, 1..N files) → `{ "text": ... }`. Simplest; this is what most consumers want.
2. **Async session (large takes)** — many photos, or you want live `Chunk i of N` progress: `POST /api/sessions` → `POST .../photos` → `POST .../extract` (starts a background job) → poll `GET .../extract/status` until `phase: "succeeded"`. This is the flow the PWA itself uses.

Same-host callers need no auth and `verify=False` on the loopback cert.

---

## Base URL, transport, auth

| Concern | Same-host (loopback) consumer | Remote consumer |
|---|---|---|
| Base URL | `https://127.0.0.1:8444` (HTTP if no cert) | your Cloudflare URL, e.g. `https://ocr.<domain>` |
| TLS | self-signed loopback cert → `verify=False` | Cloudflare terminates TLS; normal verification |
| Auth | **none** — loopback IPs bypass the gate | bearer token required when enabled |

**Loopback bypass.** `app/webapp/middleware.py` lets any caller from `127.0.0.1`, `::1`, or `localhost` through without a token. A downstream app running on the same PC needs no credentials at all.

**Self-signed cert.** The webapp serves HTTPS on loopback (so the PWA keeps a secure context on the phone). A same-host HTTP client must skip verification (`httpx.Client(verify=False)` / `requests(..., verify=False)` / `curl -k`) — the cert is not in your client's trust store. If `webapp/certificates/` is absent the server falls back to plain HTTP.

**Remote auth (only if the token gate is on).** When `auth_token` is set in `config/webapp_config.json`, non-loopback callers must present it as either:

- `Authorization: Bearer <token>` header (preferred for API clients), or
- `?token=<token>` query string.

Exempt paths that never need the token: `/`, `/static/*`, `/healthz`, `/install-ca`, `/api/login`, `/api/version`.

---

## Lifecycle overview

```
single-shot ───────────────────────────────────────────────────────┐
  POST /api/extract            multipart 1..N files → { text, ... }  │
                                                                     │
async session ──────────────────────────────────────────────────────┤
  POST /api/sessions                       create  → { session_id }  │
  POST /api/sessions/{id}/photos           append 1..N photos        │
  POST /api/sessions/{id}/extract          start background OCR job   │
  GET  /api/sessions/{id}/extract/status   poll: chunks_done/total    │
  GET  /api/sessions/{id}/text             final text (any time)     │
                                                                     │
  DELETE /api/sessions/{id}                drop (e.g. incognito)     ┘
```

Photos are persisted to disk (`archive/YYYY/MM/DD/HH-MM-SS-<id>/NN.jpg`) the moment they're ingested, **before** extraction runs, so a dropped connection never loses the captured images — re-run extraction later with `POST .../extract` or `.../redo`.

---

## Endpoints

### `POST /api/extract` — single-shot (the consumable one)

One call: create a session, ingest the images, run extraction to completion server-side, return the text. **Multipart** form upload — field name `files` (repeatable for multiple images):

```
POST /api/extract?model=gemini_flash&prompt_id=verbatim-merge&incognito=false
Content-Type: multipart/form-data
  files=<screenshot.jpg>
  files=<screenshot2.jpg>   # optional, 1..N
```

Query params, all optional:

- `model` — a vision alias on the hub (`gemini_flash` default, `gemini_pro`, `gemini_lite`, `claude_haiku`, `claude_sonnet`, `claude_opus`). Unknown model → `400`.
- `prompt_id` — one of the entries in `config/ocr_prompts.json` (`verbatim-merge` default, `structured-markdown`, `plain-stripped`, `code-fenced`).
- `incognito` — `true` keeps the take out of History; pair with `DELETE /api/sessions/{id}` if you also want it off disk. Default `false` (the take stays in History, recoverable, like a PWA session).

Response:

```json
{
  "session_id": "14-32-07-a1b2c3d4",
  "text": "the clean extracted text",
  "model": "gemini_flash",
  "prompt_id": "verbatim-merge",
  "chars": 1234,
  "duration_s": 3.7,
  "incognito": false
}
```

Empty `text` is a **valid 200** — the prompt asks the model to emit nothing when there's no readable text. Errors: `400` empty upload / unknown model, `413` more than `single_shot_max_photos` images (default 8 — use the async flow for big takes), `502` on a hub/extraction failure (the detail carries the hub error).

> The single-shot cap (`single_shot_max_photos` in `config/webapp_config.json`) keeps a synchronous call bounded — a screenshot is 1–2 images. For a 50-photo document, use the async session flow so you can show progress instead of holding one long request.

### `POST /api/sessions` — create (async flow)

Optional JSON body `{ "incognito": false }`. Response: `{ session_id, folder, created_at, incognito }`. `session_id` is the handle for every subsequent call.

### `POST /api/sessions/{id}/photos` — append photos

Multipart, field name `files` (1..N). Validates, EXIF-rotates, downscales (`max_photo_dimension_px`), persists each. Response lists all photos on the session plus the ones `added`. `413` if it would exceed `max_photos_per_session`.

### `POST /api/sessions/{id}/extract` — start the OCR job

Optional JSON body `{ "model": "...", "prompt_id": "..." }`. Starts a **background** job and returns immediately with the initial status payload. Idempotent: calling it again after success returns the existing text (`reused: true`) instead of re-billing the hub — use `/redo` to force a re-run with a different model.

### `GET /api/sessions/{id}/extract/status` — poll progress

```json
{
  "session_id": "…", "phase": "running",
  "chunks_total": 3, "chunks_done": 1,
  "model": "gemini_flash", "prompt_id": "verbatim-merge",
  "duration_s": null, "extract_succeeded": null,
  "extracted_chars": 0, "error": null, "reused": false
}
```

`phase` walks `idle → queued → running → merging → succeeded` (or `failed`, with `error` set). On `succeeded` the payload also carries `extracted` (the full text). Poll this until `phase` is `succeeded` or `failed`.

### `GET /api/sessions/{id}/text` — read the text

`{ session_id, extracted }` — the full extracted text at any time (the list endpoint only returns 200-char previews).

> **Field-name note.** Single-shot returns the text as `text`; the async status/text payloads call it `extracted`. Same string, two historical names — read whichever the endpoint you called returns.

### Reading back: list, delete

- `GET /api/sessions?limit=10&offset=0` → `{ sessions: [...], total, offset, limit }` (per-session metadata + previews; incognito sessions are excluded).
- `DELETE /api/sessions/{id}` → `{ removed: "<id>" }` (`404` if unknown).
- `DELETE /api/sessions` → `{ removed: <count> }`.

### `GET /api/version` — build identity (pin point)

```json
{ "git_sha": "6768630", "built_at": "2026-06-11T...", "asset_hash": "ab12cd34" }
```

Exempt from auth. Use `git_sha` to pin the build you integration-tested against. `GET /healthz` → `{ "ok": true, "service": "photo-ocr-webapp" }` for a plain liveness probe.

---

## Image format

Anything `src/image_utils.py` validates: JPEG / PNG / WebP / HEIC (iOS) etc. Images are EXIF-rotated and downscaled to `max_photo_dimension_px` (long edge) before they hit the hub, and stored as JPEG. Send a truthful `Content-Type` on each part; an unreadable/oversized file fails that one part with `400` and a clear message.

---

## End-to-end examples

### curl — single-shot

```bash
# loopback, no auth, -k for the self-signed cert
curl -sk -X POST "https://127.0.0.1:8444/api/extract?model=gemini_flash" \
  -F files=@screenshot.png | jq -r .text
```

### Python `requests` — single-shot (the app-launcher pattern)

```python
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
BASE = "https://127.0.0.1:8444"  # same-host: loopback bypasses auth

with open("screenshot.png", "rb") as fh:
    resp = requests.post(
        f"{BASE}/api/extract",
        params={"model": "gemini_flash"},
        files={"files": ("screenshot.png", fh.read(), "image/png")},
        timeout=120.0,
        verify=False,           # self-signed loopback cert
    )
resp.raise_for_status()
print(resp.json()["text"])
```

### Python `httpx` — async flow with progress

```python
import time
import httpx

BASE = "https://127.0.0.1:8444"
c = httpx.Client(base_url=BASE, verify=False, timeout=120.0)

sid = c.post("/api/sessions", json={}).json()["session_id"]
c.post(f"/api/sessions/{sid}/photos",
       files=[("files", ("01.jpg", open("01.jpg", "rb").read(), "image/jpeg")),
              ("files", ("02.jpg", open("02.jpg", "rb").read(), "image/jpeg"))])
c.post(f"/api/sessions/{sid}/extract", json={"model": "gemini_flash"})

while True:
    s = c.get(f"/api/sessions/{sid}/extract/status").json()
    print(s["phase"], s["chunks_done"], "/", s["chunks_total"])
    if s["phase"] in ("succeeded", "failed"):
        break
    time.sleep(0.5)

print(c.get(f"/api/sessions/{sid}/text").json()["extracted"])
```

---

## Error reference

| Status | When | Notes |
|---|---|---|
| `400` | empty upload, unknown model, or an unreadable/invalid image | bad request shape |
| `401` | remote caller, token gate on, token missing/wrong | loopback never sees this |
| `404` | unknown `session_id` | |
| `413` | single-shot over `single_shot_max_photos`, or a session over `max_photos_per_session` | use / split via the async flow |
| `502` | hub unreachable, or the model exhausted its token budget while reasoning | single-shot only; the async path reports the same failure via `phase: "failed"` + `error` |
| `500` | session vanished mid-extract (should not happen) | |

The **async** `/extract` path never returns a hub error as a non-200 — it accepts the job (200) and surfaces the failure through `extract/status` (`phase: "failed"`, `error`). Only the **single-shot** path maps a hub failure to `502`, because a one-call consumer needs a status code to branch on.

---

## Changelog

Breaking changes to the contract are recorded here. Pin a build via `GET /api/version` (`git_sha`) if you need certainty.

- **2026-06-11** — Initial publication of the OCR API as a supported consumable surface, plus the new single-shot `POST /api/extract` endpoint (issue #37). The `/api/sessions*` routes already worked over loopback; this documents them as a contract and adds the one-call path for downstream consumers (first consumer: app-launcher#171).
