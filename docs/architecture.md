# Architecture

## Overview

A PDF goes in, a single merged CSV of product rows comes out. The pipeline
renders each page to an image, runs PaddleOCR-VL over it, then normalises and
merges the tables it found.

```text
              Web UI (frontend/index.html, served at /ui)
                           │
                           ▼
                        FastAPI
                           │
      ┌────────────────────┴─────────────────────┐
      ▼                                          ▼
 api/routes.py                        api/records_routes.py
 (stateless /extract/csv)             (UI: extract, history, preview,
      │                                compare, auth — writes SQLite)
      │                                          │
      │                                          ▼
      │                              services/extraction_service.py
      │                                          │
      └──────────────────┬───────────────────────┘
                         ▼
                 extract_document.py
                         │
      ┌──────────────────┼──────────────────┐
      ▼                  ▼                  ▼
 split_pdf.py    process_pages.py     json_to_csv.py
                         │                  │
                         ▼                  ▼
                    OCRService     utils/table_reader.py
                         │                  │
                         ▼                  ▼
                   PaddleEngine     utils/table_normalizer.py
                         │                  │
                         ▼                  ▼
                   PaddleOCR-VL        merged CSV
```

## Layers

| Layer | Module | Responsibility |
| --- | --- | --- |
| API | `api/routes.py` | The stateless `POST /extract/csv`: takes a PDF, returns a CSV, records nothing |
| API | `api/records_routes.py` | Everything the UI needs — extract, records, preview, download, compare, login. All database-backed |
| Service | `services/extraction_service.py` | Runs the pipeline, then writes the history rows |
| Service | `services/ocr_service.py` | Owns the one PaddleOCR-VL engine |
| Service | `services/compare_service.py` | Pairs extracted rows against an existing-data file by a mapped key, and builds the two report workbooks |
| Pipeline | `scripts/split_pdf.py` | PDF → one PNG per page |
| Pipeline | `scripts/process_pages.py` | PNGs → OCR result JSON |
| Pipeline | `scripts/json_to_csv.py` | OCR JSON → merged CSV, rejected CSV, per-table CSVs |
| Parsing | `utils/table_reader.py` | OCR JSON → `TableRecord`s. Schema-agnostic |
| Parsing | `utils/table_normalizer.py` | Canonicalise headers, gate, repair rows, merge. All trading-pack knowledge lives here |
| Storage | `app_db/database.py` | SQLite: users, extraction records, per-table rows, comparisons |

The split between the last two matters. `table_reader` knows nothing about
Midas codes or consumer deals — it turns OCR output into dataframes. Every
assumption about what a *product table* looks like is in `table_normalizer`,
so a different document type needs a different normaliser rather than changes
to the pipeline.

### Two modules in `utils/` are not part of this path

Neither is imported by anything above, and neither runs:

- **`utils/html_table_utils.py`** parses table HTML into a rowspan/colspan-aware
  grid and applies its own layout fixes — image and barcode column removal,
  consumer-deal group alignment, several shifted-row repairs. It reached the
  output only through `scripts/json_to_excel.py`, which the CSV work deleted.
  It covers much of the same ground as `table_normalizer` (which works on the
  dataframe rather than the HTML grid), but not all of it, and the two have not
  been compared against the same documents. Reconciling them is open work — see
  the note in the merge commit that brought the CSV output onto `main`'s
  layout fixes.
- **`utils/table_utils.py`** is commented out in its entirety.

## Request flow

`POST /api/extract` with a PDF:

1. **Validate** the filename ends in `.pdf` and the processing-mode arguments
   agree (`max_pages` for `first_n`, `start_page`/`end_page` for `page_range`).
2. **Save** to `storage/input/<uid>_<filename>`. The uid namespaces every
   downstream directory so concurrent uploads of the same filename cannot
   overwrite each other, and the filename is stripped to its base name so it
   cannot escape the directory.
3. **Split** into `storage/pages/<doc>/page_NNN.png` at 300 DPI.
4. **OCR** each page, writing `storage/json/<doc>/page_NNN_res.json`.
5. **Merge** — read the tables back, canonicalise, gate, repair, concatenate,
   and write `storage/output/<doc>.csv`.
6. **Record** the run and one row per table found in SQLite.
7. **Respond** with counts and the first 50 merged rows.

Steps 3–5 are `extract_document()`, which is also what the stateless
`/extract/csv` endpoint calls — it just skips 6 and streams the file back.

### How table metadata is recovered

The OCR JSON describes one *page image*, not the source document, so two
fields have to be reconstructed:

| Field | Where it comes from | Why not the obvious source |
| --- | --- | --- |
| Page number | the `page_NNN` in the JSON filename | `page_index` is `null` — PaddleOCR only sets it for multi-page inputs, and this pipeline feeds it one PNG at a time |
| Document name | the JSON folder name | `input_path` points at the page image, so it yields `page_002` |

Both were previously taken from the JSON and produced `Page : -` and
`Document : page_001` in every output.

### Why the first row becomes the header

PaddleOCR-VL emits every table cell as `<td>` with no `<th>`. `pd.read_html`
therefore has no header to read: it numbers the columns `0..n` and leaves the
document's own header text sitting in data row 0.

`table_reader._promote_header_row` lifts that row into the column names when
the columns are merely positional. Without it no table can ever match
`Midas Code` / `Consumer Deal`, so nothing merges — and a comparison has only
`0..n` to offer as the field to map on.

A header row may legitimately contain blank cells (the unlabelled shelf
column). Those become positional names rather than blocking the promotion;
refusing to promote over one empty cell left whole documents unmergeable.

## The singleton OCR engine

`OCRService` loads PaddleOCR-VL once during FastAPI startup and every request
reuses it. The model is ~8 GB on the GPU and takes tens of seconds to load, so
loading per request is not viable.

```text
FastAPI startup → OCRService.initialize() → PaddleEngine → PaddleOCR-VL
                                                  │
                              all requests reuse this one engine
```

This follows FastAPI's recommended lifespan pattern for shared ML models. The
consequence is that the process is stateful and single-engine, which drives
the concurrency limits below.

## Concurrency

**One extraction at a time, and it blocks the whole API.**

`POST /api/extract` is declared `async` but calls the pipeline synchronously,
so it runs on the event loop thread. For the duration — minutes for a small
document, tens of minutes for a large one — the process serves nothing else,
including `/health`. Docker's healthcheck fails during a normal job and marks
the container `unhealthy`; nothing restarts on that, so it is misleading
rather than harmful.

Moving the call to a threadpool was tried and reverted (2026-08-14) after what
looked like a GPU deadlock. That diagnosis is now doubtful: a stack trace
later showed paddlex runs VLM inference on its **own** worker thread
(`_worker_vlm`) regardless of the caller, so the calling thread cannot be what
mattered. The hang was most likely the wedged-GPU problem described in
[troubleshooting.md](troubleshooting.md#a-wedged-gpu-looks-exactly-like-a-hung-extraction),
which was present at the same time. It is worth re-testing rather than
treating a threadpool as unsafe.

The real fix is a separate worker process, which sidesteps the question
entirely by giving OCR its own CUDA context and leaving the API free. See
[plan/2026-08-14_async-extraction-worker.md](plan/2026-08-14_async-extraction-worker.md).

## Shared state on disk

Two processes must not point at the same `storage/` tree. The pipeline clears
and rewrites `storage/pages/<doc>` and `storage/json/<doc>` on every run, so a
second instance sharing the directory would delete a running extraction's
working files. Give each instance its own checkout or its own bind mounts —
see [deployment.md § Running a second instance](deployment.md#running-a-second-instance-on-the-same-host).

## Storage layout

```text
storage/
├── input/                     uploaded PDFs, prefixed with a uid
├── pages/<doc>/page_NNN.png   300 DPI page renders
├── json/<doc>/page_NNN_res.json   raw PaddleOCR-VL output
├── output/
│   ├── <doc>.csv              the merged product table
│   ├── <doc>-rejected.csv     rows that could not be trusted (only if any)
│   ├── <doc>/individual/*.csv every table as read, qualifying or not
│   └── comparison_<id>_*.xlsx the two workbooks each comparison writes
├── tmp/                       scratch for uploaded comparison files, deleted after use
└── db/table_extractor.db      extraction history
```

The OCR JSON is the durable artefact: previews and comparisons are re-read from
it on demand rather than duplicated into the database, and
`scripts/replay_merge.py` re-runs the whole merge over it in about a second.
That makes post-processing changes testable without touching the GPU.

## Technology choices

| Choice | Why |
| --- | --- |
| PaddleOCR-VL | Reads table structure directly to HTML, including merged cells |
| PyMuPDF | Page rendering with no external binary |
| pandas `read_html` | Resolves `rowspan`/`colspan` into a full grid, so vertically merged cells (a consumer deal spanning several products) replicate into every row they cover |
| SQLite | The history is small and single-writer; a server would be more moving parts than the problem needs |
| CSV | One flat table per document, readable by every downstream tool |
