# Plan: Move extraction to a separate worker process

**Date:** 2026-08-14
**Status:** Proposed — not started
**Context:** Follow-up to the extraction-history UI work (`feature/extraction-ui-workflow`)

---

## The problem

Extraction runs synchronously on the event loop thread, so **a running
extraction blocks the entire API**. While a document is being processed:

* `/ui`, `/api/records`, previews, and downloads all hang
* `/health` stops answering, so Docker marks the container `unhealthy`
  after ~90s (cosmetic today — nothing restarts on unhealthy — but it makes
  the healthcheck useless as a signal)
* a second upload queues invisibly behind the first, with no feedback

Measured on the deployed A16: ~10–35s per page. A 5-page file blocks the API
for ~3 minutes; the 68-page BUD trading pack blocks it for ~15–20 minutes.

This is long-standing behaviour, not a regression — it predates the UI work.
The UI simply makes it far more visible, because there is now a records list
and a preview that users expect to stay responsive.

---

## Why the obvious fix does not work

**Do not move extraction to a threadpool.** This was tried on
2026-08-14 and had to be reverted.

PaddleOCR-VL's GPU inference deadlocks when driven from any thread other
than the one the process starts on. The failure signature:

* `split_pdf` completes normally (page PNGs appear on disk)
* OCR then produces **no output at all** — the JSON folder stays empty
* GPU holds ~8 GB allocated at **0% utilisation**
* one CPU core spins at 100%, ~246 threads parked in `futex_wait`
* the request never returns and never errors

Pinning all Paddle calls to a single dedicated worker thread — engine
initialisation *and* every inference on the same thread — **does not help**.
It has to be the main thread.

Anything that fixes this has to keep inference on the main thread of
whichever process owns the CUDA context. That is what makes a separate
*process*, rather than a thread, the right unit.

---

## Proposed design

Split the API and the OCR engine into two processes.

```
┌────────────────────┐        job queue        ┌──────────────────────┐
│  FastAPI (api)     │ ──────────────────────► │  worker process      │
│  - never loads     │                          │  - owns the GPU      │
│    the OCR model   │ ◄────────────────────── │  - runs extraction   │
│  - always responsive│      status + result    │    on ITS main thread│
└────────────────────┘                          └──────────────────────┘
         │                                                  │
         └──────────────── SQLite (job state) ──────────────┘
```

The worker's main thread runs the extraction, so the thread-affinity
constraint is satisfied without the API ever blocking.

### Job queue

Start with **SQLite as the queue** — a `jobs` table alongside the existing
history tables. It needs no new infrastructure, and the volume here (a
handful of jobs a day, one at a time) is nowhere near needing Redis/Celery.
Revisit only if multi-worker or multi-host is ever required.

```sql
CREATE TABLE extraction_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT DEFAULT 'queued',   -- queued|running|completed|failed
    filename      TEXT NOT NULL,
    pdf_path      TEXT NOT NULL,
    processing_mode TEXT, max_pages INTEGER, start_page INTEGER, end_page INTEGER,
    extraction_id INTEGER,                 -- set on success
    error         TEXT,                    -- set on failure
    pages_done    INTEGER DEFAULT 0,       -- progress
    pages_total   INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at    TIMESTAMP,
    finished_at   TIMESTAMP
);
```

The worker polls for the oldest `queued` row, claims it with a conditional
`UPDATE ... WHERE status='queued'`, and processes it. One worker means no
contention; the conditional update keeps it correct if that ever changes.

### API changes

`POST /api/extract` stops blocking and becomes a submission:

| | Now | After |
| --- | --- | --- |
| Response | 200 with the full result, after minutes | **202** with `{job_id, status: "queued"}`, immediately |
| Progress | none | `GET /api/jobs/{id}` → status, `pages_done`/`pages_total`, `extraction_id` when done |

`GET /api/jobs/{id}` returns the job row; on `completed` it carries the
`extraction_id`, and the client then reads the existing
`/api/records/{id}` and preview endpoints, which are unchanged.

**Keep the current blocking `POST /api/extract` available** under a flag or
a separate path for one release, so any existing scripted caller does not
break. The legacy `POST /extract/excel` returns a file directly and cannot
become async without changing its contract — leave it as-is, and document
that it blocks.

### UI changes

`frontend/index.html` currently awaits one long `fetch`. It becomes:

1. POST the file → get `job_id`
2. poll `GET /api/jobs/{job_id}` every ~2s
3. drive the existing elapsed-time indicator from `pages_done`/`pages_total`
   — turning today's "(3m 21s elapsed)" into a real "page 12 of 68"
4. on `completed`, fetch the record and render the table list exactly as now

This also removes the current failure mode where a browser or proxy timeout
kills a long upload that is in fact succeeding server-side.

### Process management

Both processes run in the one container, under `supervisord` (already
present in the venv) or two compose services sharing the storage volumes.
Two compose services is cleaner — the worker gets the GPU reservation and
the API does not need it at all, which also drops the API image's start-up
time and memory.

---

## Work breakdown

1. `extraction_jobs` table + queue helpers in `app_db/database.py`
2. `worker.py` — claim loop calling the existing `run_extraction()`, unchanged
3. `pages_done`/`pages_total` progress reporting from `process_pages.py`
4. `POST /api/extract` → 202; add `GET /api/jobs/{id}` and `GET /api/jobs`
5. Frontend: submit + poll + progress
6. Compose: split API and worker services; GPU only on the worker
7. Restore a meaningful healthcheck (the API can now always answer `/health`)

Steps 1–3 are independently testable without touching the API.

---

## Verification

The bug this replaces was invisible to CPU-only testing, so:

* **Must be verified on the GPU deployment**, not just locally on CPU.
* Confirm `nvidia-smi` shows non-zero GPU utilisation during a run — 0%
  utilisation with allocated memory is the deadlock signature.
* Confirm `/health` and `/api/records` respond **while** an extraction runs.
* Confirm two uploads queue and both complete, in order.
* Re-run the 68-page BUD trading pack and compare table count and per-page
  timing against the ~10–20s/page baseline from 2026-08-11.

---

## Risks

* **Model load time doubles if both processes load it** — the API must never
  import/initialise the OCR engine. Keep `ocr_service` out of the API path.
* **SQLite write contention** between API and worker. Both already use WAL;
  keep writes short and avoid long transactions.
* **Orphaned `running` jobs** if the worker is killed mid-job. On worker
  startup, reset any `running` row back to `queued` (or mark it `failed`) —
  otherwise a job is stuck forever.
* **Storage growth** is unchanged by this work and still unbounded; see the
  retention note in the README.

---

## Out of scope

Authentication. The API has none today — the UI login gates the UI only.
That is a separate decision, called out in the README, and should not be
bundled into this change.
