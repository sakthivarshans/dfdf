# Troubleshooting

Failure modes that have actually been hit, and how to get out of them.

## A wedged GPU looks exactly like a hung extraction

**Symptom.** `split_pdf` finishes and page images appear, OCR then produces no
JSON at all, the GPU sits at 0% utilisation and idle power while still holding
its ~8 GB, and one CPU core spins at 100%. The request never returns and never
errors.

Seen on 2026-08-15 on GPU 0, on a document that had extracted fine minutes
earlier. It followed a second container briefly creating a CUDA context on the
same GPU.

**Confirm it.** A stack dump frozen at the same line across samples, with the
GPU idle, means the GPU rather than the code:

```bash
nvidia-smi --query-gpu=index,utilization.gpu,memory.used,power.draw --format=csv

docker run --rm --pid=container:table-data-extraction --cap-add SYS_PTRACE \
  python:3.10-slim sh -c "pip install -q py-spy && py-spy dump --pid 1"
```

A hung run shows paddlex's VLM worker parked in one call:

```text
Thread-N (active): "Thread-3 (_worker_vlm)"
    embedding (paddle/nn/functional/input.py:335)
    ...
    greedy_search (…/generation/utils.py:1284)
```

Sample it two or three times, ~25 s apart. If the line never changes while the
GPU reads 0%, it is stuck, not slow.

**Fix.** Move to another GPU and recreate the container:

```bash
OCR_DEVICE=gpu:3 docker compose up -d
```

Restarting the container on the same GPU did **not** clear it, and plain CUDA
compute on that GPU still worked, so neither is a useful test.

**Avoid it.** Do not run a second model-loading container against the GPU this
one uses. `NVIDIA_VISIBLE_DEVICES=all` means a second instance can create a
context on it even with `OCR_DEVICE` pointed elsewhere — pin each instance with
`device_ids`. See
[deployment.md § Running a second instance](deployment.md#running-a-second-instance-on-the-same-host).

## The container says `unhealthy` but the logs show it working

Expected during an extraction. The pipeline runs synchronously on the event
loop, so `/health` is not answered until the job finishes, and the healthcheck
(30 s interval, 3 retries) fails after ~90 s of normal work.

Nothing restarts on `unhealthy`, so this is misleading rather than harmful.
Check the logs to tell a working job from a stuck one — a working one prints
`Processing page_NNN.png` as it goes.

The fix is a separate worker process:
[plan/2026-08-14_async-extraction-worker.md](plan/2026-08-14_async-extraction-worker.md).

## Requests hang while another extraction is running

Same cause. One extraction blocks every other request — the UI, previews,
downloads, `/health`, everything. A second upload does not fail; it queues
invisibly until it or a proxy times out.

Because uvicorn only logs a request when it *completes*, a queued upload
produces **no log line at all**, which reads exactly like "the request never
arrived". Check `storage/input/` — if the file is on disk, it arrived.

## Extraction returns 422

Two different causes, distinguished by the message.

**"No tables were found in this PDF"** — OCR found no table blocks at all.
Check the page images under `storage/pages/<doc>/`; if the tables are there,
the layout model did not detect them.

**"none had the required 'Midas Code' and 'Consumer Deal' columns"** — tables
were found, but none qualified for the merge. Inspect the headers as read:

```bash
python scripts/replay_merge.py "<doc>*"
```

Then compare against
[csv-output.md § Header canonicalisation](csv-output.md#header-canonicalisation).
The usual causes are a spelling not in `CANONICAL`, or a wide table that OCR
split into two halves so that neither carries both columns.

No record is kept for a 422 — a run that produced nothing is not history — but
the page images and OCR JSON stay on disk for inspection.

## Rows are missing from the CSV

Every removed row is counted, so start with the numbers rather than the data:

```bash
python scripts/replay_merge.py "<doc>*"
```

| Reported as | Meaning |
| --- | --- |
| `blank Midas` | section-header rows, correctly dropped |
| `banners` | category rows like `FRESH`, correctly dropped |
| `rejected` | rows the repair could not verify — read `<doc>-rejected.csv` |
| tables `qualified of N found` | the gap is tables skipped by the gate |

If the count is short and none of those explain it, check for a vertically
split table: a wide table detected as two side-by-side blocks fails the column
gate on both halves. `layout_nms=False` in `engine/paddle_engine.py` fixed the
case this was found on — if you see it again, that setting is the first thing
to check. See [csv-output.md § Known gap (closed)](csv-output.md#known-gap-closed).

## A column appears twice under similar names

An OCR spelling that is not in `CANONICAL` becomes its own column, so a real
column forks — `Prom Por` beside `Promo POR`. Unmapped headers are logged:

```bash
docker compose logs | grep "unmapped column header"
```

Add the spelling to `CANONICAL` in `utils/table_normalizer.py` and re-run
`scripts/replay_merge.py` to confirm the column count drops.

## Every row comes back `No data in PDF`

The unique field is wrong. Nothing paired, so every row of your file is
reported as missing — the comparison ran, it just had no way to match a row in
the PDF to a row in your spreadsheet.

Step 3 suggests a unique field but cannot know your data. Check that the pair
it picked identifies a row on **both** sides (`Midas Code` ↔ `Code`, not
`Shelf` ↔ `Code`), and that the values actually overlap — a key the OCR read
with a different prefix will not match either.

`Rows Paired` is the number to watch. If it is 0, fix the mapping before
reading anything else.

## `Only in Extracted` and `Only in Existing` are both large

Same cause, one step subtler: the key is right but the two sides spell its
values differently, so only some rows pair. Open both workbooks and compare a
key from each — a stray space or a lost leading zero is the usual answer. Keys
are matched trimmed, whitespace-collapsed and case-folded, but nothing more
aggressive than that.

## The comparison downloads return 404

Only mapped comparisons write workbooks. A comparison made before the reports
existed, or one run through the API without `unique_key`, has nothing to
download — the response's `downloads` object is the authoritative answer. The
files can also have been removed from `storage/output` by hand.

## A comparison reports 0% accuracy (API only)

The accuracy figure survives in the API response but is no longer shown in the
UI. Without `unique_key` the endpoint falls back to matching column names
case-insensitively after trimming and comparing rows positionally; if the two
files share no column names, nothing is compared and it still returns `200`
with `accuracy_percent: 0.0`. Check `common_columns` — if it is empty, that is
why. Send `unique_key` and `compare_fields` instead.

## `Permission denied` on `storage/`, or `PermissionError` from `rmtree`

The container runs as root, so everything it writes through the bind mount —
uploaded PDFs, `storage/pages/<doc>/`, `storage/json/<doc>/` — is root-owned on
the host.

**Reading it as your user fails.** To get at the JSON, come out through the
container rather than the mount; `docker cp` writes as the invoking user:

```bash
docker exec table-data-extraction ls -la /app/storage/json/<doc>/
docker cp table-data-extraction:/app/storage/json/<doc>/page_001_res.json .
```

**A local dev run against the same checkout fails harder.**
`extract_document()` calls `shutil.rmtree()` on those directories at the start
of every run, and your user cannot delete them:

```
cp: cannot open 'storage/input/test1.pdf' for reading: Permission denied
PermissionError: [Errno 13] Permission denied: 'storage/pages/test1'
```

Fixes, in order of preference: use a separate checkout for local dev; upload
under a filename the container has not used; or stop the containers and
`sudo chown -R "$USER:$USER" storage/` — a reset, not a fix, since the next
container run recreates root-owned directories. See
[deployment.md § Running Docker and local dev against the same checkout](deployment.md#running-docker-and-local-dev-against-the-same-checkout).

## First request after deploy takes several minutes

The PaddleOCR-VL weights (~8 GB) download on first use, into the `model_cache`
and `model_cache_paddlex` volumes. Subsequent restarts reuse them. Watch:

```bash
docker compose logs -f | grep -E "Fetching|Loading|Ready"
```

Removing those volumes forces the download again.

## `RuntimeError: OCR Engine has not been initialized.`

`OCRService.get_engine()` was called before `initialize()`. In the app that
happens in the FastAPI lifespan, so this normally means a script was run
outside the app without initialising first:

```python
from services.ocr_service import ocr_service
ocr_service.initialize()
```

## Port 8001 already allocated

Another container holds it — often a previous instance under a different
project name.

```bash
docker ps -a --filter "publish=8001"
docker compose down
```

## `nvidia-smi` works on the host but the container has no GPU

The NVIDIA Container Toolkit is missing or Docker was not restarted after
installing it.

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

If that fails, fix the toolkit before looking at this project.

## A build fails with `no space left on device`

Docker's build cache is the usual culprit, not the images. It grows with every
rebuild and nothing trims it:

```bash
docker system df                  # look at RECLAIMABLE against Build Cache
docker builder prune -af          # drop it all
docker image prune -f             # and any dangling images
```

Note the filesystem Docker actually writes to — `docker info | grep "Docker
Root Dir"` — which is not necessarily the one `df -h /` reports.

Pruning the cache means the next build downloads the PaddlePaddle CUDA wheels
again, which takes roughly half an hour. It is still the right move when the
disk is full; there is nothing else of that size to reclaim.

## Cannot reach the Docker daemon

```
warn: Cannot reach Docker daemon as current user.
```

`deploy.sh` prints this and retries with `sudo`. To fix it permanently:

```bash
sudo usermod -aG docker "$USER"
newgrp docker        # or log out and back in
```

## Out of GPU memory

Each instance needs ~8 GB. On a 15 GB card two instances do not fit, and the
second fails during model load.

```bash
nvidia-smi --query-compute-apps=pid,used_memory --format=csv
```

Give each instance its own GPU with `device_ids` — which is also what prevents
the wedging described at the top of this page.
