# Documentation

Reference documentation for the Table Data Extraction API.

| Document | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Components, request flow, the merge pipeline, the singleton OCR engine, and the design constraints that follow from it |
| [csv-output.md](csv-output.md) | What lands in the CSV, which tables qualify, and how misaligned rows are repaired or rejected |
| [comparison.md](comparison.md) | Checking an extraction against your own data: the three-step workflow, the field mapping, the two report workbooks |
| [deployment.md](deployment.md) | The three ways to run the service: GPU Docker, CPU Docker, and local dev |
| [configuration.md](configuration.md) | Every value in `config/settings.py`, which env vars override them, and which settings are currently inert |
| [api.md](api.md) | Endpoint reference for the stateless `/extract/csv` and the UI-backing `/api/*` endpoints |
| [troubleshooting.md](troubleshooting.md) | Verified failure modes and how to get out of them |

## Where to start

- **Running it for the first time** → [deployment.md](deployment.md)
- **Understanding what the CSV contains** → [csv-output.md](csv-output.md)
- **Checking a document against data you already hold** → [comparison.md](comparison.md)
- **Changing the extraction or merge logic** → [architecture.md](architecture.md)
- **Calling the API from another service** → [api.md](api.md)
- **Something is broken** → [troubleshooting.md](troubleshooting.md)

## Known limitations

These are real and currently unaddressed. They are described in full in the
documents linked above, and collected here so they are not a surprise.

1. **One extraction at a time, and it blocks everything.** `POST /api/extract`
   runs the whole pipeline synchronously on the event loop. While an extraction
   is in flight the process answers nothing else — including `/health`, so Docker
   marks the container `unhealthy` mid-job. See
   [architecture.md § Concurrency](architecture.md#concurrency) and the fix
   planned in [plan/2026-08-14_async-extraction-worker.md](plan/2026-08-14_async-extraction-worker.md).

2. **The API has no authentication.** `/api/login` verifies a password and
   returns no token; no endpoint requires one. Anyone who can reach the port can
   list, download, and delete extractions. CORS is open (`allow_origins=["*"]`).
   Run it on a trusted network only. See
   [configuration.md § Authentication](configuration.md#authentication).

3. **The merge is schema-specific.** A table joins the CSV only if it has both
   `Midas Code` and `Consumer Deal` after header canonicalisation. Documents of
   any other shape extract to an empty CSV and return HTTP 422. See
   [csv-output.md § What qualifies](csv-output.md#what-qualifies).

4. **A table split vertically by OCR used to lose its rows** — closed by
   `layout_nms=False`, which makes the layout model keep the whole-table region
   instead of two side-by-side halves that each failed the column gate.
   Confirmed on the document it was found on: 65 → 84 rows. The setting is
   still marked experimental and has not been validated on the other documents.
   See [csv-output.md § Known gap (closed)](csv-output.md#known-gap-closed).

5. **Storage growth is unbounded.** Deleting a record removes its database rows,
   not its files. The uploaded PDF, page images, OCR JSON, and CSVs all remain,
   as do the workbooks every comparison writes. See
   [deployment.md § Disk usage](deployment.md#disk-usage).

6. **Several settings are inert.** `PDF_DPI`, `MAX_PAGES`, `SAVE_HTML`,
   `SAVE_EXCEL`, `EXCEL_SHEET_NAME`, `INCLUDE_*`, `LOG_LEVEL`, `API_HOST`, and
   `API_PORT` are defined but never read. See
   [configuration.md § Inert settings](configuration.md#inert-settings).
