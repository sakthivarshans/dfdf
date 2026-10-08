# Table Data Extraction API

Extracts tables from PDF documents with **PaddleOCR-VL** and merges the product
tables into a single CSV, repairing OCR misalignment and reporting anything it
could not trust rather than dropping it silently.

Ships a FastAPI service, a web UI, and a SQLite history of every run. The UI
also compares an extraction against a spreadsheet you already hold, and returns
the result as two Excel workbooks.

```text
PDF → page images → PaddleOCR-VL → JSON → canonicalise → gate → repair → merged CSV
```

## Quick start

```bash
./deploy.sh
```

| | |
| --- | --- |
| UI | `http://<host>:8001/ui` |
| API docs | `http://<host>:8001/docs` |
| Health | `http://<host>:8001/health` |

Requires an NVIDIA GPU and the Container Toolkit; `docker compose -f
docker-compose.cpu.yml up -d --build` runs the same service on CPU (port 8003,
and roughly 20× slower per page). **Set `ADMIN_PASSWORD` before deploying
anywhere shared** — it defaults to `changeme123`, and the seed
user is created on first startup only.

Extract from the command line without the UI:

```bash
curl -X POST http://localhost:8001/extract/csv \
  -F "file=@doc.pdf" -F "processing_mode=all" -o doc.csv
```

## What comes out

| File | Contents |
| --- | --- |
| `<doc>.csv` | every qualifying table merged into one product table |
| `<doc>-rejected.csv` | rows that could not be trusted, with the reason |
| `<doc>/individual/*.csv` | every table exactly as read |

A table joins the merge only if it has both `Midas Code` and `Consumer Deal`
after header canonicalisation. Section headers and category banners are
dropped and counted; rows the OCR shifted sideways are repaired when the
repair can be verified, and rejected when it cannot. Nothing disappears
without appearing in a count.

Full detail in [docs/csv-output.md](docs/csv-output.md).

## Checking a document against your own data

Upload the spreadsheet the document is supposed to reflect, map the fields that
have to agree, and the comparison returns:

| Workbook | Contents |
| --- | --- |
| Updated Existing Data | your file returned whole, with `Data in PDF`, `Status` and the PDF's own value added to every row |
| Data Only in Extracted | the PDF rows whose key is not in your file |

The two sides are paired on a unique field you choose on each side — `Midas
Code` against `Code` — so neither row order nor column naming has to match.
Every row of your file ends up accounted for: paired, or reported as missing
from the PDF.

Full detail in [docs/comparison.md](docs/comparison.md).

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Components, request flow, the singleton OCR engine, and the constraints that follow |
| [docs/csv-output.md](docs/csv-output.md) | What lands in the CSV, which tables qualify, how rows are repaired or rejected |
| [docs/comparison.md](docs/comparison.md) | Comparing an extraction against your own spreadsheet, the field mapping, the two workbooks |
| [docs/deployment.md](docs/deployment.md) | GPU Docker, CPU Docker, local dev, running a second instance |
| [docs/configuration.md](docs/configuration.md) | Every setting, which env vars override them, which are inert |
| [docs/api.md](docs/api.md) | Endpoint reference |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Verified failure modes and how to get out of them |

Start at [docs/](docs/README.md).

## Read this before relying on it

1. **One extraction at a time, and it blocks the whole API** — including
   `/health`, so Docker reports the container `unhealthy` during normal work.
2. **The API has no authentication.** The login gates the UI only. Trusted
   networks only.
3. **The merge is schema-specific** to trading-pack documents.
4. **Storage grows without bound** — deleting a record does not delete files.

Each is expanded in [docs/README.md § Known limitations](docs/README.md#known-limitations).

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install paddlepaddle==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
uvicorn app:app --reload
```

```bash
python -m pytest tests/test_table_normalizer.py tests/test_table_reader.py tests/test_database.py tests/test_compare_service.py -q
```

Those four suites are pure logic — no GPU, no model, no sample PDFs.

To iterate on the merge without re-running OCR, replay it over JSON already on
disk:

```bash
python scripts/replay_merge.py "BUD*"
```

## Technologies

Python · FastAPI · PaddleOCR-VL · PaddlePaddle · pandas · PyMuPDF · SQLite
