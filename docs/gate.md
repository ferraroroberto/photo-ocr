# The pre-ship gate

`scripts/verify-before-ship.ps1` is the single pre-ship gate, run with:

```powershell
powershell.exe -File scripts/verify-before-ship.ps1
```

It byte-compiles `app`/`src`/`tests`, runs the ratchets below, runs the non-e2e pytest
suite, then runs the Playwright e2e suite (Chromium + WebKit/iPhone) against a
**disposable webapp it boots itself on a free port** — so a forgotten tray can't let a
regression slip through as a skipped suite.

The e2e leg is proportionate to the diff: `scripts/classify_e2e.py` classifies the
branch's changed files against `origin/main` (rules in `.fleet.toml` `[e2e]`). A
docs-only branch **skips** the leg and the gate prints the tier and reason; a
static-image change runs only the smoke slice; anything under `app/webapp/`, `src/`, or
any path no rule names runs the **full** suite. A classifier that errors, prints no
tier, or returns an unknown one also runs the full suite, and says so — the gate never
skips on doubt.

## Ratchets

The static ratchet (`scripts/static_ratchet_gate.py`, rules in `.fleet.toml`
`[static_ratchet]`) records the repo's existing duplicate blocks, unused
imports/variables, and ruff bug (`B`) and security (`S`) findings in
`.static-ratchet-baseline.json` and fails the gate on any new one, naming it, so the
count can only go down. It needs **Node** (jscpd runs through `npx`; the first run
needs network) and `ruff` (installed from `requirements-dev.txt`). A detector that
cannot run fails the gate as NOT CONFIRMED rather than passing. `_vendored/` is excluded
because vendor-verbatim copies are duplicates by design.

The docs ratchet (`scripts/docs_ratchet_gate.py`, rules in `.fleet.toml`
`[docs_ratchet]`) holds `README.md` under 40 KiB and keeps history out of the docs: it
counts issue references, dated headings and changelog headings in `README.md` and
`docs/**/*.md` and fails the gate, naming the file and line, when a count rises above
`.docs-ratchet-baseline.json`. A doc it cannot read (not UTF-8, an unclosed fence) fails
as NOT CONFIRMED rather than passing. Lower the baseline with `--update-baseline` in the
PR that removes debt; adding entries needs `--allow-additions`.

The structure ratchet (`scripts/structure_ratchet.py`, rules in `.fleet.toml`
`[structure_ratchet]`) fails the gate when a file passes 1,000 lines, a Python or JS
function 100 lines, a directory 40 modules, or a `src/` module imports `app/`, beyond
`.structure-ratchet-baseline.json`, and when a tracked root folder falls outside the
declared `shape` (`webapp-tray`) and `extra_roots`. The layout is never baselined;
`docs/layout.md` gives each folder's purpose. Exit 3 (a file could not be measured, or
an unknown shape) fails as NOT CONFIRMED, never a pass.

The conventions ratchet (`scripts/conventions_ratchet.py`, rules in `.fleet.toml`
`[conventions_ratchet]`) holds fleet conventions as `ast` rules: `no-window` (every
`subprocess` spawn passes `creationflags`, since the tray and webapp have no console),
`logging-not-print` (no `print` in `app/` or `src/` library code), `no-drive-path` (no
hardcoded drive letters) and `subprocess-timeout` (a blocking `subprocess.run` needs
`timeout=`). It fails the gate, naming the rule, file and line, when a file's count for
a rule rises above `.conventions-ratchet-baseline.json`. (`secret-compare` and
`git-wrapper` are not among the enabled rules above — see CLAUDE.md for why.) A spawn
that must break a rule carries a `# <rule>-exempt: <why>` marker. A file it cannot
parse fails as NOT CONFIRMED, never a pass.

The comment ratchet (`scripts/comment_ratchet.py`, rules in `.fleet.toml`
`[comment_ratchet]`) counts `#NNN` issue references inside comments in `app/`, `src/`
and `scripts/` (`.py`, `.js`, `.css`, `.ps1`), plus `#NNN` references inside Python
docstrings (`docstrings = true`) and a short list of history phrases (`history_phrases
= true`) and fails the gate, naming the file and each reference's line, when a file's
count rises above `.comment-ratchet-baseline.json`. A comment states why the code is
the way it is now; how it got there lives in the issue, the PR and `git log`.
`_vendored/` is excluded. A file it cannot scan fails as NOT CONFIRMED, never a pass.

The instruction ratchet (`scripts/instruction_ratchet.py`, rules in `.fleet.toml`
`[instruction_ratchet]`) runs the lint-tier rules of the fleet's prompt audit over
`CLAUDE.md` and `AGENTS.md` (the repo ships no skills or rules folder): phrase patterns
such as shouted pressure words, contradictory always/never pairs, vendor names in
agent-neutral prose, and the size caps (`CLAUDE.md` 120 lines, `AGENTS.md` 32,768
bytes). It fails the gate, naming the file, rule and line, when a count rises above
`.instruction-ratchet-baseline.json` (empty). Exit 3 (a file could not be read) fails as
NOT CONFIRMED, never a pass.

The doc refs ratchet (`scripts/doc_refs_ratchet.py`, rules in `.fleet.toml`
`[doc_refs_ratchet]`) reads `README.md`, `CLAUDE.md` and `docs/**/*.md` and resolves
every repo path in an inline code span, `python -m` module, relative link and loopback
port against the tracked tree and the app's own `port`. It fails the gate, naming the
doc and line, on a reference that does not exist and is not in
`.doc-refs-ratchet-baseline.json` (empty). The ports of the services the docs name
(`:8000` the hub, `:9221` the iOS WebKit debug proxy) are declared in `allow_refs`;
fenced code blocks are not read. Exit 3 (git unusable, a doc that is not UTF-8, an
unclosed fence) fails as NOT CONFIRMED, never a pass.

The dead code ratchet (`scripts/dead_code_ratchet.py`, rules in `.fleet.toml`
`[dead_code_ratchet]`) parses the Python in `app/` and `src/` and fails the gate, naming
the file, line and qualified name, on a function, class or variable that nothing names
(no other file, string or non-Python source mentions it) and that is not in
`.dead-code-ratchet-baseline.json` (empty). Exit 3 (git unusable, a `.py` file that
cannot be read or does not parse) fails as NOT CONFIRMED, never a pass.

The runtime write ratchet (`scripts/runtime_write_ratchet.py` with its resolver
`scripts/runtime_write_resolve.py`, rules in `.fleet.toml` `[runtime_write_ratchet]`)
fails the gate when a file's count of write targets (open for writing,
`Path.write_*`, `mkdir`, a log handler, `sqlite3.connect`) resolving under a code folder
rises above `.runtime-write-ratchet-baseline.json`; a new target it cannot resolve fails
as NOT CONFIRMED. Its `--snapshot`/`--probe` pair brackets the pytest and e2e runs and
fails on any untracked or ignored file they leave under a code folder. Runtime output
belongs under the runtime root (`src/runtime_data.py`). A write to a path a parameter
or config names goes through `src/runtime_files.py`, which raises at runtime when the
path resolves under `src/`, `app/` or `scripts/`; only that module (plus the search
index connection) carries runtime-write exempt markers.

The request budget ratchet (`scripts/request_budget_ratchet.py` with its sibling
`request_budget_scan.py`, rules in `.fleet.toml` `[request_budget_ratchet]`) reads browser
JS and server Python and counts, per file, fixed-rate polls that reach the server, polls
under the 2 s floor, server loops that sleep under 5 s or run an outside program,
directory scans in `app/webapp/routers`, `asyncio.to_thread` and `run_in_executor(None, ...)`
default-pool use, and a web app that never installs `RequestTimingMiddleware`. A count
above `.request-budget-ratchet-baseline.json` fails the gate, naming the file and rule; a
file it cannot read or parse fails as NOT CONFIRMED rather than passing. The baseline only
shrinks (`--update-baseline`); a background loop that must run while nobody views the app
is declared in `[[request_budget.loops]]` with its reason, and a single hit that must stay
carries `request-budget-exempt: <reason>`. The baseline is empty: blocking work runs on a
named pool (`app/webapp/pools.py`), `RequestTimingMiddleware` is installed, and the one
server loop is declared. The vendored `_vendored/poll` helper stays for any new browser poll.

The windows-only ratchet (`scripts/windows_only_ratchet.py`, rules in `.fleet.toml`
`[windows_only_ratchet]`) reads the test modules and counts, per file and rule, tests that
import a Windows-only module, read a Windows-only API or environment variable, or plant a
`.exe` under a Windows install variable without `@pytest.mark.windows_only`, the marker the
Mac nightly deselects. A count above `.windows-only-ratchet-baseline.json` (empty) fails the
gate, naming the file and rule; a test module it cannot read or parse fails as NOT CONFIRMED.
A test that needs Windows takes the marker; a hit the ratchet gets wrong carries
`windows-only-exempt: <reason>`.

## Surfaces and loopback ports

Each surface the suite opens is tagged `surface(page, "<id>")` from the vendored
`tests/e2e/_surface.py` (inert on every normal run), with its id from the map at
[docs/ux-surface-map.md](ux-surface-map.md) or a state name for a variant.
`/design-review photo-ocr --e2e` arms it against the throwaway webapp
`scripts/design_review_synthetic.py` boots, never the live tray.

The UX surface map ([docs/ux-surface-map.md](ux-surface-map.md), source
`docs/ux-surfaces.yaml`) lists every surface under the id its root carries as
`data-surface`; the ux surface ratchet (`scripts/ux_surface_ratchet.py`) holds the two
together.

The suite keeps off the loopback ephemeral-port pool, and a drained pool fails as its
own `PORT POOL EXHAUSTED` (see [docs/e2e-loopback-ports.md](e2e-loopback-ports.md)).
