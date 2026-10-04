# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## This repository
Mobile-first photo OCR — capture/upload N photos of a document, screen, or page; a vision-capable model on the local LLM hub returns one clean, deduplicated, copy-ready text. Sister project to `voice-transcriber` (same conventions, same archive shape, same auth model), but for pixels instead of audio. See `README.md` for setup, layout, and usage.

**Project specifics:**

- **Stack:** FastAPI + vanilla JS — **not** Streamlit; do not introduce Streamlit.
- **Config & secrets:** there is no `.env`. Project config lives in `config/config.json` (committed) and runtime UI prefs + secrets (`auth_token`, `auth_password`) in `config/webapp_config.json` (gitignored).
- **Verification — webapp boot check:** `& .\.venv\Scripts\python.exe -m uvicorn app.webapp.server:app --host 127.0.0.1 --port 8444` then `curl http://127.0.0.1:8444/healthz`.
- **Pre-ship gate:** any change under `app/webapp/` (or the webapp-facing `src/` modules) must pass `powershell.exe -File scripts/verify-before-ship.ps1` before it is declared done. Use Windows PowerShell 5.1 (`powershell.exe`, or the absolute `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`), not `pwsh` — the default `pwsh` on PATH is a 0-byte WindowsApps reparse stub that fails non-interactively.
- **e2e runtime contract:** the e2e leg of the pre-ship gate collects 26 nodes (Chromium + WebKit/iPhone, auto-booted): 19 executed, 7 skipped by design (the server-side cache-busting and cert checks run once on Chromium, the iPhone-projection check on WebKit only). Measured 2026-10-04 as the median of 5 warm runs on a loaded machine: 14.5 s of session time, of which the thumbnail-toolbar node is ~3.4 s across both projections (the whole gate, byte-compile + 199 unit tests + e2e, ~24 s; the 2026-10-03 figure before that node was 12.3 s); a cold first run in a fresh checkout adds several seconds of imports. Investigate a gate that takes over ~30 s; a test that sleeps or polls in real time belongs on the page clock (`page.clock`), not a timer. The gate writes JUnit to `webapp/e2e-junit.xml` (gitignored), declared as `[e2e] junit_xml` in `.fleet.toml` so `/e2e-audit` can read per-test seconds. The gate routes the e2e leg on the branch diff vs `origin/main` through the vendored `scripts/classify_e2e.py` (rules in `.fleet.toml` `[e2e]`): `skip` (docs / markdown / desktop assets only) skips the leg and prints the tier and reason, `static` runs the declared smoke slice (`tests/e2e/test_smoke.py`, Chromium), `full` runs the whole suite; a classifier error, a missing tier or an unknown tier falls back to `full` with its own message, and CI always runs `full`. The node counts and timings above describe a `full` run; a skipped leg writes no JUnit, so `webapp/e2e-junit.xml` is then the last run that did.
- **Restart and verify before hand-off:** the running webapp has no hot-reload — code edits do nothing until the `:8444` process is restarted. The canonical restart is **`tray.bat --restart`** — the orphan-proof reclaim-then-start that kills the tray subtree, then reclaims `:8444` by PID scoped to this repo's `.venv` (CommandLine-matched), then starts fresh. Run that, don't hand-roll the kill (a by-hand kill misses an orphaned port holder). As a by-hand fallback only, kill the process listening on `:8444` (`Get-NetTCPConnection -LocalPort 8444`) — never a blanket `pythonw`/`python` kill, sister apps must survive — then relaunch via `tray.bat`. **Confirm the new build is live** via `curl -k https://127.0.0.1:8444/healthz` (200) before handing off; don't leave a stale process serving. (The tray-launched webapp serves **HTTPS** with a cert issued for the `.ts.net` name — `tailscale cert`, real Let's Encrypt — hence `-k` and `https` — a plain `http://` probe fails at the TLS layer and reads as a false "not live". The manual boot-check above is plain `http` on purpose: it starts uvicorn without the cert flags.)

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). This is a live, parseable block — the product is the FastAPI + static PWA under `app/webapp/`.*

- design spec applies: yes        # `no` would make the gate a permanent no-op; this repo serves a real PWA
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/`
  - /          (Capture / History tabs behind the vendored bottom-tab nav; Settings opens from the header gear)

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is a hand-authored Mermaid diagram of this repo's own internal structure (`app/cli`, `app/tray`, `app/webapp`, `src/`, `config/`, `scripts/`, `archive/`, `tests/`, and the external `local-llm-hub`/`cloudflared`/Tailscale dependencies) — the per-repo counterpart to the fleet-wide diagram `/system-map` generates centrally. Update it in the same PR as any material structural change (a router added/moved, a `src/` module relocated, a new external dependency) — same anti-staleness contract as this repo's own `.fleet.toml` description field (`ferraroroberto/fleet-config#256`). It is not auto-generated and not covered by `scripts/verify-before-ship.ps1`.
