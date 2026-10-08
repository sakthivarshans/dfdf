# Configuration

All configuration lives in [`config/settings.py`](../config/settings.py). Six
values read the environment; the rest are edited in the file. `HOST_PORT` is the
exception — it is read by Compose, not by the application.

## Environment variables

These are the only settings that can be changed without editing code, which
makes them the only ones usable to differentiate deployments.

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `OCR_DEVICE` | `cpu` | `PaddleOCRVL(device=…)` | `cpu`, `gpu:0`, `gpu:1`, … Set to `gpu:2` in `docker-compose.yml`, and pinned to `cpu` in `docker-compose.cpu.yml` and `Dockerfile.cpu` |
| `CPU_THREADS` | `8` | `PaddleOCRVL(cpu_threads=…)` | The main CPU tuning knob. Ignored on GPU |
| `OCR_PRECISION` | `fp32` | `PaddleOCRVL(precision=…)` | `fp32` or `fp16`. Only meaningful on GPU |
| `DB_PATH` | `<root>/table_extractor.db` | `app_db/database.py` | Set to `/app/storage/db/table_extractor.db` in Docker so history survives a container rebuild; the CPU compose file uses `…_cpu.db` so the two deployments never write one SQLite file |
| `ADMIN_USERNAME` | `admin` | seed user on first startup | |
| `ADMIN_PASSWORD` | `changeme123` | seed user on first startup | **Change this before deploying** |
| `HOST_PORT` | `8001` | `docker-compose.yml` port mapping | Host side only — the app always listens on 8001 inside the container |

The OCR default is `cpu` deliberately: a checkout with no environment set up
runs locally without a GPU. The GPU Docker deployment opts in explicitly
rather than the other way round.

```bash
OCR_DEVICE=gpu:1 docker compose up -d
ADMIN_PASSWORD='…' docker compose up -d
HOST_PORT=8090 docker compose up -d
CPU_THREADS=16 docker compose -f docker-compose.cpu.yml up -d
OCR_DEVICE=gpu:1 uvicorn app:app --reload
```

### Why the compose default is `gpu:2`, not `gpu:0`

GPU 0 wedged on 2026-08-15 after a second container briefly created a CUDA
context on it: extraction hung indefinitely at 0% GPU utilisation on a
document that had worked minutes earlier, and only moving to another GPU
cleared it. The default was changed to keep the deployment off that GPU. See
[troubleshooting.md](troubleshooting.md#a-wedged-gpu-looks-exactly-like-a-hung-extraction).

## Authentication

The seed user is created on **first startup only**, from `ADMIN_USERNAME` and
`ADMIN_PASSWORD`. Changing the variables later does not update an existing
database — delete the user row, or the database file, to re-seed.

Two things to understand before exposing this service:

1. **The login gates the UI, not the API.** `/api/login` returns no token and no
   endpoint requires one. Anyone who can reach the port can list, download, and
   delete extractions regardless of the password.
2. **CORS is fully open** (`allow_origins=["*"]` with `allow_credentials=True`),
   so any page in a browser that can reach the host can call it.

Run it on a trusted network. Putting it on a public address needs real
authentication in front of it first.

Passwords are stored as unsalted SHA-256.

## Paths

Derived from the project root, so they follow the checkout and need no
configuration in Docker.

| Setting | Value |
| --- | --- |
| `PROJECT_ROOT` | parent of `config/` |
| `STORAGE_DIR` | `<root>/storage` |
| `INPUT_DIR` | `<root>/storage/input` |
| `PAGES_DIR` | `<root>/storage/pages` |
| `JSON_DIR` | `<root>/storage/json` |
| `OUTPUT_DIR` | `<root>/storage/output` |
| `TMP_DIR` | `<root>/storage/tmp` — uploaded comparison files, deleted after each comparison. The reports a comparison writes go to `OUTPUT_DIR` and stay |
| `DB_PATH` | `<root>/table_extractor.db`, overridden in Docker |

All storage subdirectories are bind-mounted by both compose files, including
`storage/db`, so the extraction history survives a rebuild.

## Model settings

| Setting | Value | Effect |
| --- | --- | --- |
| `OCR_PIPELINE_VERSION` | `v1.6` | Documented for reference; the pipeline version is chosen by `paddleocr` |
| `ENABLE_MKLDNN` | `True` | CPU acceleration. Ignored on GPU |
| `MKLDNN_CACHE_CAPACITY` | `10` | CPU op cache |
| `ENABLE_HPI` | `False` | High-performance inference. Off |
| `USE_TENSORRT` | `False` | TensorRT. Off |

`ENABLE_HPI` and `USE_TENSORRT` are the two obvious GPU throughput levers and
both are off. Neither has been benchmarked against this workload; turning
either on is untested here.

## API settings

| Setting | Value | Effect |
| --- | --- | --- |
| `API_TITLE` / `API_DESCRIPTION` / `API_VERSION` | — | Shown in `/docs` |
| `MAX_FILE_SIZE` | 200 MB | Enforced by `POST /api/extract`. Larger uploads get 400 |

## Output settings

| Setting | Value | Effect |
| --- | --- | --- |
| `SAVE_JSON` | `True` | Writes the OCR result JSON. **Required** — the merge, previews, and comparisons all re-read it |
| `SAVE_MARKDOWN` | `False` | Also write PaddleOCR's markdown rendering |

Turning `SAVE_JSON` off would break the CSV output, previews, and
`scripts/replay_merge.py`, since all of them read the JSON back.

## Inert settings

Defined but never read. They are left in place because they describe intent,
but changing them does nothing today.

| Setting | What actually applies |
| --- | --- |
| `PDF_DPI` | `split_pdf()` takes `dpi` as an argument and defaults to 300 |
| `MAX_PAGES` | superseded by the per-request `processing_mode` |
| `SAVE_HTML`, `SAVE_EXCEL` | no longer any HTML/Excel output path |
| `EXCEL_SHEET_NAME`, `INCLUDE_PAGE_NUMBER`, `INCLUDE_DOCUMENT_NAME`, `INCLUDE_TABLE_TITLE` | left from the Excel output that CSV replaced |
| `LOG_LEVEL` | logging is not configured from settings |
| `API_HOST`, `API_PORT` | the port comes from the `uvicorn` command in the Dockerfiles (8001); note `API_PORT` still says `8000` |

## What is not configurable

| Behaviour | Where it is fixed |
| --- | --- |
| Which tables merge | `REQUIRED_COLS` in `utils/table_normalizer.py` |
| Header spellings | `CANONICAL` in the same file |
| What a Midas code looks like | `MIDAS_PATTERN`, `^M\d+$` |
| Per-column value shapes used by row repair | `_VALIDATORS`, same file |
| Page render DPI | the `dpi=300` default in `split_pdf()` |
| Preview size | 50 rows in the extract response, 100 in the preview endpoints |
