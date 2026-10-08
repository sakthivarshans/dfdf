# API Reference

Two groups of endpoints:

- **Stateless** — `POST /extract/csv` takes a PDF and returns a CSV. Nothing is
  recorded.
- **UI-backing** — everything under `/api/*` plus `/download/*`. These read and
  write the extraction history and are what `/ui` calls.

Interactive docs are at `/docs`.

> **No endpoint requires authentication.** `/api/login` verifies a password and
> returns no token; nothing checks one. Anyone who can reach the port can list,
> download, and delete extractions. See
> [configuration.md § Authentication](configuration.md#authentication).

---

## `GET /`

Service name and a pointer to `/docs`.

## `GET /health`

```json
{"status": "healthy", "service": "Document Extraction API"}
```

Used by the Docker healthcheck. Note that it **stops answering while an
extraction is running** — see [architecture.md § Concurrency](architecture.md#concurrency).

## `GET /ui`

Serves the single-page web UI (`frontend/index.html`).

---

## `POST /extract/csv`

Upload a PDF, get the merged CSV back. Stateless — no record is kept and the
response is the file itself.

### Request

Form data:

| Field | Required | Description |
| --- | --- | --- |
| `file` | yes | PDF document |
| `processing_mode` | no | `all` (default), `first_n`, or `page_range` |
| `max_pages` | for `first_n` | number of leading pages to process |
| `start_page` | for `page_range` | first page, 1-based, inclusive |
| `end_page` | for `page_range` | last page, 1-based, inclusive |

### Processing modes

| Mode | Processes | Requires |
| --- | --- | --- |
| `all` | every page | — |
| `first_n` | pages 1…`max_pages` | `max_pages` |
| `page_range` | `start_page`…`end_page` | both, with `end_page >= start_page` |

### Examples

```bash
# entire document
curl -X POST http://localhost:8001/extract/csv \
  -F "file=@doc.pdf" -F "processing_mode=all" -o doc.csv

# first 5 pages
curl -X POST http://localhost:8001/extract/csv \
  -F "file=@doc.pdf" -F "processing_mode=first_n" -F "max_pages=5" -o doc.csv

# pages 6 to 8
curl -X POST http://localhost:8001/extract/csv \
  -F "file=@doc.pdf" -F "processing_mode=page_range" \
  -F "start_page=6" -F "end_page=8" -o doc.csv
```

### Errors

| Status | When |
| --- | --- |
| 400 | not a `.pdf`, or the mode's required arguments are missing/inconsistent |
| 500 | the pipeline raised |

---

## `POST /api/login`

Form fields `username` and `password`. Returns `{"success": true, "username": …}`
or 401.

Gates the UI screen only — it issues no token and no other endpoint checks one.

---

## `POST /api/extract`

The UI's extraction endpoint. Same form fields as `/extract/csv`, but it
records the run and returns JSON instead of a file.

### Response

```json
{
  "success": true,
  "record_id": 3,
  "message": "Merged 65 rows from 4/6 tables across 5 page(s) in 184.0s",
  "document_name": "40a0a5a6_test2",
  "pages_processed": 5,
  "tables_extracted": 6,
  "tables_merged": 4,
  "rows_extracted": 65,
  "rows_repaired": 0,
  "rows_rejected": 0,
  "columns": ["Product Description", "Midas Code", "..."],
  "data_preview": [{"Midas Code": "M317988", "...": "..."}],
  "tables": [
    {"table_index": 1, "page_number": "1", "table_heading": "...",
     "row_count": 19, "col_count": 8}
  ]
}
```

`data_preview` holds the first 50 merged rows; `tables` lists every table
found, including ones the merge skipped.

### Errors

| Status | When |
| --- | --- |
| 400 | not a `.pdf`, bad mode arguments, or the file exceeds `MAX_FILE_SIZE` (200 MB) |
| 422 | no tables at all, **or** tables were found but none had `Midas Code` + `Consumer Deal` |
| 500 | the pipeline raised |

A 422 leaves no record behind — a run that produced nothing is not history.
The message distinguishes the two cases, because "no tables" and "no *product*
tables" have different causes.

### Upload handling

The file is written to `storage/input/<uid>_<filename>`, where `uid` is 8 hex
characters. Every downstream directory is keyed off that name, so two uploads
of the same filename cannot overwrite each other. The filename is reduced to
its base name first, so `../../x.pdf` cannot escape the directory.

---

## `GET /api/records`

Every extraction, newest first.

## `GET /api/records/{id}`

One record, plus a `tables` array of every table found in it.

## `DELETE /api/records/{id}`

Deletes the record, its table rows, and its comparisons. **Files on disk are
not removed** — see [deployment.md § Disk usage](deployment.md#disk-usage).

## `GET /api/records/{id}/preview`

The merged product table: `columns`, the first 100 rows, and `total_rows`.

## `GET /api/records/{id}/tables/{table_index}/preview`

One table as it was read, before normalisation — for checking what the merge
kept or skipped.

---

## `GET /download/{id}`

The merged CSV, as `text/csv`.

## `GET /download/{id}/rejected`

The rejected-rows CSV. 404 when the extraction rejected nothing.

---

## `POST /api/existing-data/columns`

The column headers of an uploaded `.csv`, `.xlsx`, or `.xls` file, so a
caller can build the field mapping that `POST /api/compare` expects.

| Field | Required | Description |
| --- | --- | --- |
| `file` | yes | existing data, `.csv`, `.xlsx`, or `.xls` |

Returns `filename`, `columns`, `total_rows`, and the first five rows as
`preview`. The upload is read and discarded — nothing is stored. An
unreadable file is a `400`, not a `500`.

## `POST /api/compare`

Compare an extraction against an existing-data file.

| Field | Required | Description |
| --- | --- | --- |
| `extraction_id` | yes | which record to compare |
| `file` | yes | existing data, `.csv`, `.xlsx`, or `.xls` |
| `table_index` | no | compare one raw table instead of the merged CSV |
| `unique_key` | no | JSON `{"extracted": col, "existing": col}` to pair rows on |
| `compare_fields` | no | JSON `[{"extracted": col, "existing": col}, ...]` to compare |

Defaults to the merged CSV, because an existing-data file describes the whole
document. Pass `table_index` to narrow down a discrepancy to one table.

**With `unique_key`** (what the UI sends) the existing file is the reference
list: every one of its rows is looked up in the extracted data by key, and
only the `compare_fields` pairs are checked on the ones that are found. Keys
are matched normalised — trimmed, whitespace-collapsed, case-folded — so row
order and column naming can differ freely. `compare_fields` is then required,
and naming a column that is not in the file is a `400`.

Values count as equal if they agree as normalised text, or as numbers once
thousands separators and currency symbols are dropped: `1` / `1.0`,
`1,000` / `1000` and `£1.50` / `1.50` are not reported as OCR errors.

The response adds `mode: "mapped"`, `rows_paired`, `only_in_existing_rows`,
`only_in_extracted_rows`, `duplicate_keys_*` / `blank_keys_*`, and a
`downloads` object holding the two report URLs below. `rows_paired +
only_in_existing_rows` always equals `total_rows_ground` — every row of the
uploaded file is accounted for. Where the extracted data repeats a key the
first row wins the lookup and the repeats are counted in
`duplicate_keys_extracted`.

### The two reports

Every mapped comparison writes two Excel workbooks to `storage/output`,
named after the comparison id, and records their paths:

`GET /download/comparison/{comparison_id}/existing-data` — the uploaded file
exactly as given, plus a verdict on every row:

| Column | Value |
| --- | --- |
| `Data in PDF` | `TRUE` when the key was found in the extracted data |
| `Status` | `No data in PDF`, `Same Values`, or `Values Mismatch` |
| `Value in PDF` | what the PDF said for the compared field |

With more than one compared field there is one value column per field,
named `<existing column> in PDF`, because a single column cannot hold
several values. A column whose name is already taken is added with a
` (comparison)` suffix rather than overwriting the user's own.

`GET /download/comparison/{comparison_id}/extracted-only` — the extracted
rows, all columns, whose key never appears in the uploaded file.

Both are `404` for a comparison that predates the reports or whose file has
been removed from disk.

**Without `unique_key`** it falls back to the original behaviour: column
names lower-cased and trimmed on both sides, then compared positionally row
by row over the columns they share, reporting `common_columns`. If the two
files share no column names the response is still `200` with
`accuracy_percent: 0.0` and a `summary` saying so.

Either way the response carries overall accuracy, per-field accuracy, and up
to 100 differing rows. Results are recorded and listed by
`GET /api/comparisons`.

The accuracy figures are still computed, returned and stored, but the UI no
longer displays them — see
[comparison.md § The summary](comparison.md#the-summary).

## `GET /api/comparisons`

Every comparison run, newest first, joined to its extraction's filename.
