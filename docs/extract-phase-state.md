# Extraction phase

A take's extraction is a state machine: it is queued, read in chunks, merged, and ends read or failed. The machine is declared once in `src/extract_state.py` (`ExtractPhase`, `ACTIVE_PHASES`, `derive_extract_phase`, the legal-move table) and registered in `.fleet.toml` as the `extract-phase` state machine. Every reader asks that module for the phase; none re-spells a phase name or re-infers the mode from a flag.

## Why it is declared

The facts that make up the phase live in two stored places: `meta.extra["extract_progress"]["phase"]` (written by the job as it moves) and `meta.extract_succeeded` (written when a text lands or a run fails). Alongside them sit `meta.error` and the side facts `retry_error`, `missing_photos`, `reused` and `photos_sig` that qualify a finished read. Left implicit, each reader inferred the mode from whichever it happened to look at, and they disagreed: a failed redo kept its old text on disk while the take read as failed, so History showed the error in place of the good text, and a queued, running or merging take looked like any other row. Declaring the phase gives those combinations one answer, and a write along an edge that does not exist is refused instead of stored.

## The phases

The values are the strings stored in `meta.json` and returned by the API, so nothing on disk or on the wire changes.

| Phase | Meaning | Active |
|---|---|---|
| `idle` | never read; nothing queued | no |
| `queued` | accepted; the job has not started | yes |
| `running` | reading photos; `chunks_done` of `chunks_total` | yes |
| `merging` | chunks read; collating and writing the text | yes |
| `succeeded` | a read finished; the text on disk is current (see `missing_photos` for a partly read take, `retry_error` for a retry that read nothing new) | no |
| `failed` | the last run failed; `error` says why | no |

`ACTIVE_PHASES` is `queued`, `running` and `merging`. A take in an active phase cannot be started again, retried, or have its Save to History switch flipped.

## Deriving the phase

`derive_extract_phase(stored_phase, extract_succeeded)` is the only code that maps the stored facts to a phase:

- a stored phase that is one of the six wins, whatever `extract_succeeded` says (a redo in flight over a good text has `extract_succeeded` true and phase `running`);
- otherwise (a take written before phases were recorded, or a stored string this build does not know) the phase comes from `extract_succeeded`: true is `succeeded`, false is `failed`, none is `idle`.

`extract_phase(session)` in `src/extract_job.py` applies it to a take.

## A failed redo over a good text

A redo that fails keeps the previous `extracted.txt`. The take is `failed` and has text at once. This is one declared combination, not a contradiction:

- the phase stays `failed`, and `error` carries the reason;
- the status payload's `has_text` is true (`extracted_chars` above zero), which tells it apart from a first read that failed (`has_text` false);
- History leads with the kept text, shows the Failed chip, and the take sheet adds "The last read failed: …" under the text. A first read that failed has no text, so its row leads with the error.

`settle_interrupted` agrees with it: a redo or retry that died over a good text goes back to `succeeded` with the text intact and `retry_error` saying why, because the last good read is still the current one; only a take with no text is marked `failed`.

## Legal moves

A phase may always repeat (a progress tick). Between different phases:

| From | To |
|---|---|
| `idle`, `succeeded`, `failed` | any active phase (a redo or retry queues; the single-shot route starts at `running`; a sweep may find a job at any active phase) |
| `queued` | `running`, `succeeded`, `failed` |
| `running` | `merging`, `succeeded`, `failed` |
| `merging` | `succeeded`, `failed` |

`succeeded` and `failed` never turn into each other without passing through an active phase. `set_extract_progress` checks every write that carries a `phase` against this table and raises `IllegalExtractTransition` before anything is stored; an unknown phase string raises `ValueError`.

## Who reads it

| Reader | Uses |
|---|---|
| `extract_status_payload` (every status body, the History summary) | `phase`, plus the derived `in_flight`, `failed` and `has_text` |
| `POST /extract` and `/redo` | `extract_phase` and `ACTIVE_PHASES` to return the running job instead of starting another |
| `POST /retry-missing` | the same check, `409` while active |
| `PATCH /api/sessions/{id}` and the photo routes (upload, reorder, remove) | the same check (`refuse_while_extracting`), `409` while active |
| boot sweep and crash handler (`settle_interrupted_extracts`, `settle_interrupted`) | `extract_phase` and `ACTIVE_PHASES` |
| History (`sessions.js`) | the payload's `in_flight`, `failed` and `has_text`; it does not spell phase names |

The Capture progress poll (`poll.js`, `extract.js`) reads the phase through `app/webapp/static/extract_phase.js`, the client's one place for the phase names (the `extract-phase-client` machine in `.fleet.toml`). `deriveExtractPhase(body)` maps a status body to a phase (a body with none, or one this build does not know, is `idle`, so the loop keeps waiting), `isExtractDone` and `isExtractFailed` answer the two terminal questions, and `requireForwardPhase` throws if successive polls of one job move from a later live phase to an earlier one (`merging` back to `running`), which the server cannot produce. Its values are checked against the server's `ExtractPhase` in `tests/test_client_state_machines.py`.

A new reader calls `extract_phase` (or reads the payload) and extends `ExtractPhase` if it needs a mode the table lacks. It does not look at `extract_succeeded`, `error` or the progress dict to guess one.

## Proof

`tests/test_extract_state.py` holds the derive table (one row per reachable observation, including the failed-redo-over-text and unknown-string cases), the legal-move table, a test that an illegal write is refused and leaves `meta.json` untouched (carrying the `state-machine-proof: extract-phase` marker), and meta.json shapes written by earlier builds (a phase only, `extract_succeeded` only, neither).
