# Keeping the e2e suite off the loopback port pool

Every TCP connection a browser closes leaves its (ephemeral port, server port) pair in TIME_WAIT for minutes. On Windows a `connect()` that lands on such a pair fails as `net::ERR_ADDRESS_IN_USE` on whichever page loads next, unrelated to the diff, so a busy host (this run, or another lane gating at the same time) flakes the gate. None of the measures below touches the machine's dynamic port range or `TcpTimedWaitDelay`.

## What limits the residue

- **One browser context per engine.** The `authed_page` fixture opens its pages in the session-scoped `_shared_context` (`tests/e2e/conftest.py`) instead of a fresh context per test. A fresh context starts with an empty HTTP cache and closes its connections to the app on teardown. Isolation is kept by `_reset_context`, which clears the page's storage and routes and the context's cookies and permissions after every test.
- **A test that changes context-wide state takes `isolated_page`.** It gets its own fresh context. `page.clock.install()` fakes time for the whole context and cannot be undone, so the extract-progress test uses it rather than leave a fake clock on the shared context.
- **Keep-alive HTTP for the harness's own calls.** The `/healthz` readiness poll (`scripts/disposable_webapp.py`) and the tests' direct API calls (`tests/e2e/_http.py`) go through a `requests.Session`, which reuses one connection.
- **A named failure.** A failure whose text carries `ERR_ADDRESS_IN_USE` leads with `PORT POOL EXHAUSTED`, and the run ends with a section of that name listing the affected tests (`tests/e2e/_port_pool.py`, pinned by `tests/e2e_harness/test_port_pool.py`). The gate output then reads as a host-capacity problem: wait a few minutes and re-run.

## Measuring

Sample `netstat -ano -p tcp` every 200 ms while one full run executes (Chromium + WebKit, `--e2e-autoboot`) and count the distinct new loopback TIME_WAIT pairs whose server port is the disposable app's. The host's other apps add pairs on their own ports, so count only the app's.

| | Before | After |
| --- | --- | --- |
| Full run, pairs to the app's port (three runs each) | 128, 129, 129 | 74, 73, 70 |
| Chromium alone | 40 | 7 |
| WebKit alone | 50 | 52 |

WebKit's share does not shrink with a shared context: its networking closes some connections itself between page loads, which a probe of one context loading the app four times showed (Chromium kept 6 connections open throughout, WebKit's count of closed ones grew with each page). Its residue is a property of the engine, not of how the suite builds contexts.
