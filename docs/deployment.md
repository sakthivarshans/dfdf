# Deployment

Three ways to run the service. They differ only in how PaddlePaddle is
installed and what `OCR_DEVICE` is set to — the application code is identical.

| | GPU Docker | CPU Docker | Local dev |
| --- | --- | --- | --- |
| Files | `Dockerfile`, `docker-compose.yml` | `Dockerfile.cpu`, `docker-compose.cpu.yml` | none |
| Base image | `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` | `python:3.10-slim-bookworm` | your machine |
| Paddle build | `paddlepaddle-gpu==3.3.1` (cu126) | `paddlepaddle==3.3.1` (cpu) | `paddlepaddle==3.3.1` (cpu) |
| `OCR_DEVICE` | `gpu:2` | `cpu` | `cpu` |
| Host port | 8001 (`HOST_PORT`) | 8003 | 8000 (uvicorn default) |
| Needs NVIDIA Container Toolkit | yes | no | no |
| Typical use | production | GPU-less hosts, CI | editing code |

The two compose files use different container names, host ports, database
files, and cache volumes, so **the GPU and CPU deployments can run side by side
on one host** — but see [the GPU warning below](#running-a-second-instance-on-the-same-host)
before starting a second *model-loading* container on a GPU host.

## Verification status

| Path | State |
| --- | --- |
| GPU Docker (`docker-compose.yml`) | **Verified.** Running on an NVIDIA A16; full extraction, UI, and comparison exercised end to end |
| CPU Docker (`docker-compose.cpu.yml`) | Verified on `main` before this branch's UI and history database. The image now also copies `app_db/` and `frontend/`, and the compose file seeds the admin user and mounts `storage/db` — **not rebuilt since those changes** |
| Local dev (`uvicorn`) | Verified on CPU |

Throughput on the A16: roughly **30–40 seconds per page**. A 5-page document
takes about 3 minutes, a 68-page trading pack about 35 minutes.

CPU is roughly **20× slower per page**: a single page carrying one small table
measured 268 s on CPU Docker against 10.9 s on GPU. That ratio is the one number
that matters when choosing between them.

## GPU Docker

### Prerequisites

- NVIDIA driver and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- `nvidia-smi` working on the host
- ~18 GB of disk for the image, plus ~8 GB of GPU memory per instance

### Deploy

```bash
./deploy.sh
```

It checks Docker and the GPU, creates the `storage/` directories the bind
mounts need, builds, starts, and waits for the healthcheck. Equivalent to:

```bash
mkdir -p storage/input storage/pages storage/json storage/output storage/tmp storage/db
docker compose build
docker compose up -d
```

Then:

| | |
| --- | --- |
| UI | `http://<host>:8001/ui` |
| API docs | `http://<host>:8001/docs` |
| Health | `http://<host>:8001/health` |

**If 8001 is taken**, map a different host port. The app always listens on
8001 inside the container; `HOST_PORT` moves only the host side, and
`deploy.sh` passes it through:

```bash
HOST_PORT=8090 ./deploy.sh
```

**Set a password before deploying anywhere shared:**

```bash
ADMIN_USERNAME=you ADMIN_PASSWORD='…' docker compose up -d
```

The seed user is created on first startup only. See
[configuration.md § Authentication](configuration.md#authentication) — and note
the API itself is unauthenticated regardless.

### First run is slow — twice

1. **The build** downloads PaddlePaddle GPU wheels and their CUDA
   dependencies. Expect 5–10 minutes, and note that editing
   `requirements.txt` invalidates that layer and pays the cost again.
2. **The first request** downloads the PaddleOCR-VL weights (~8 GB) into the
   `model_cache` / `model_cache_paddlex` volumes. Those volumes are what keep
   it a one-time cost across restarts.

The model is not pre-fetched during the build, because importing Paddle's
GPU-linked core needs `libcuda.so.1`, which is not present in a plain
`docker build` sandbox without GPU passthrough.

### Selecting a GPU

`docker-compose.yml` defaults to `gpu:2`, deliberately — see
[configuration.md](configuration.md#why-the-compose-default-is-gpu2-not-gpu0).

```bash
OCR_DEVICE=gpu:1 docker compose up -d
```

The container is given every GPU (`count: all`) and picks one by index at
runtime. To hard-limit which it can see:

```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ['1']      # instead of count: all
              capabilities: [gpu]
```

### Running a second instance on the same host

Two things collide, and a third is easy to miss.

1. **Container name, image tag, and port** are all hard-coded in
   `docker-compose.yml`.
2. **The `storage/` tree.** The pipeline clears and rewrites
   `storage/pages/<doc>` and `storage/json/<doc>` on every run, so a second
   instance sharing the directory will delete a running extraction's working
   files. Give it its own checkout, worktree, or bind mounts.
3. **The GPU.** A second model-loading container will create a CUDA context on
   whatever GPU it can see — including the one the first instance is using,
   even with `OCR_DEVICE` pointed elsewhere, because `NVIDIA_VISIBLE_DEVICES`
   is `all`. That is the condition under which GPU 0 wedged. **Pin each
   instance to its own GPU with `device_ids`.** This applies to the CPU
   deployment too if you start it on a GPU host: it loads the same model, and
   nothing stops Paddle from finding a device.

```yaml
# docker-compose.second.yml — use with: -f docker-compose.yml -f docker-compose.second.yml
services:
  app:
    image: table-data-extraction:second
    container_name: table-data-extraction-second
    ports: !override
      - "8002:8001"                  # the app still listens on 8001 inside
    environment:
      - OCR_DEVICE=gpu:1
      - NVIDIA_VISIBLE_DEVICES=1     # cannot touch the other instance's GPU
      - DB_PATH=/app/storage/db/table_extractor_second.db
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ['1']
              capabilities: [gpu]
```

```bash
docker compose -f docker-compose.yml -f docker-compose.second.yml \
               -p tde-second up -d
```

`!override` matters: Compose *appends* to list fields, so a plain `ports:`
entry keeps the base `8001:8001` mapping too and fails to bind.

## CPU Docker

For hosts with no GPU: CI runners, laptops, a staging box that does not justify
an accelerator.

```bash
mkdir -p storage/input storage/pages storage/json storage/output storage/tmp storage/db
docker compose -f docker-compose.cpu.yml up -d --build
```

`docker-compose.cpu.yml` is **standalone, not an override** — do not chain it
with `-f docker-compose.yml`. It defines its own container name
(`table-data-extraction-cpu`), host port (8003), cache volumes
(`model_cache_cpu`, `model_cache_paddlex_cpu`), and database file
(`storage/db/table_extractor_cpu.db`, so two containers never write one SQLite
file).

```bash
curl http://localhost:8003/health
# {"status":"healthy","service":"Document Extraction API"}
```

The UI is at `http://<host>:8003/ui`, and `ADMIN_USERNAME` / `ADMIN_PASSWORD`
seed it exactly as they do for the GPU deployment.

### Expect it to be slow

PaddleOCR-VL is a vision-language model doing autoregressive decoding per page.
On CPU, per-page latency is minutes, not seconds — a single page with one small
table measured **268 seconds**, against 10.9 seconds for the same page on GPU.
Latency scales with core count, and `CPU_THREADS` is the knob that matters:

```bash
CPU_THREADS=16 docker compose -f docker-compose.cpu.yml up -d
```

It defaults to 8. The setting reaches Paddle through `PaddleOCRVL(cpu_threads=…)`,
alongside MKL-DNN, which is enabled unconditionally in `config/settings.py`.

Because a single page can occupy the process for minutes and the event loop is
blocked throughout ([architecture.md § Concurrency](architecture.md#concurrency)),
the CPU compose file sets a 180-second health-check `start_period` — triple the
GPU one. Even so, expect the container to read `unhealthy` *during* a large
extraction. That is the health check timing out against a busy event loop, not a
crashed service; `docker compose -f docker-compose.cpu.yml logs -f` will show the
pages still being processed.

Treat the CPU deployment as a correctness and portability path. For anything
resembling production throughput, use the GPU one.

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install paddlepaddle==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
uvicorn app:app --reload
```

`paddlepaddle` is deliberately not in `requirements.txt`: the CPU and GPU
builds need different indexes and pinned CUDA wheels, and listing either would
have pip resolve over the other. The Dockerfile installs `paddlepaddle-gpu`
separately for the same reason.

Runs on <http://127.0.0.1:8000> by default (the Docker images use 8001). Python
3.10 is what the images use and what this is verified against.

`--reload` restarts the process on every code change, and **every restart
reloads the model**, which takes tens of seconds. When iterating on anything
that is not the OCR path — the merge, header canonicalisation, route validation
— work against the test suite or replay the merge over JSON already on disk
instead:

```bash
python -m pytest tests/test_table_normalizer.py tests/test_table_reader.py tests/test_database.py -q
python scripts/replay_merge.py "BUD*"
```

Those three suites are pure logic and need no GPU or model. The remaining
files in `tests/` are manual scripts that require a loaded engine and sample
PDFs under `storage/input/`.

### Pointing local dev at a GPU

`OCR_DEVICE` works the same way outside Docker, provided you installed the GPU
wheel instead of the CPU one:

```bash
OCR_DEVICE=gpu:1 uvicorn app:app --reload
```

Pick a GPU no container is using — the same CUDA-context warning applies.

### Model cache

Weights land in `~/.paddlex/official_models/` (about 3.7 GB with PaddleOCR-VL-1.6
and the layout models). They are downloaded once and shared by every local run.
This is the host equivalent of the containers' `model_cache_paddlex` volume.

### Running Docker and local dev against the same checkout

This bites in practice. The containers run as **root** and create
`storage/pages/<stem>/`, `storage/json/<stem>/`, and the uploaded PDFs as
root-owned through the bind mount. A local dev process running as your own user
then cannot read those PDFs or delete those directories — `extract_document()`
calls `shutil.rmtree()` on them at the start of every run and raises
`PermissionError`.

Symptoms: `Permission denied` on `storage/input/*.pdf`, or a `PermissionError`
traceback from the `rmtree` in `extract_document`.

Options, roughly in order of preference:

1. **Use a separate checkout** for local dev. Cleanest, no ownership conflict.
2. **Upload under a different filename** for local runs, so the derived
   `pages/`/`json/` directories are fresh and owned by you.
3. **Reclaim ownership** once, after stopping the containers:
   ```bash
   sudo chown -R "$USER:$USER" storage/
   ```
   The next container run will create new root-owned subdirectories again, so
   this is a reset, not a fix.

## Operations

### Logs

```bash
docker compose logs -f                              # GPU
docker compose -f docker-compose.cpu.yml logs -f    # CPU
```

The pipeline logs each step, each page as it is OCR'd, and the merge summary.

### Disk usage

`storage/` grows without bound. Deleting a record through the UI or API
removes its database rows but **not** its files — the uploaded PDF, the
300-DPI page images, the OCR JSON, and the CSVs all remain. Every comparison
adds two more workbooks under `storage/output`, and nothing removes those
either.

```bash
du -sh storage/*
```

The page images are the bulk of it. The OCR JSON is the piece worth keeping:
previews, comparisons, and `scripts/replay_merge.py` all re-read it, so
deleting `storage/json/<doc>` makes an existing record unpreviewable.

### Backups

`storage/db/table_extractor.db` is the only file that cannot be regenerated
from the source PDFs. It is SQLite in WAL mode; copy the `.db`, `.db-wal`, and
`.db-shm` files together, or use `sqlite3 … ".backup"`.

### Updating

```bash
git pull
docker compose build
docker compose up -d
```

The database migrates itself on startup — `init_db()` adds new columns to an
existing `extraction_records` table rather than requiring a fresh database.

### Other useful commands

```bash
docker compose down                                 # stop, keep volumes
docker compose down -v                              # stop and drop the model cache

docker compose build --no-cache                     # rebuild from scratch
docker inspect --format='{{.State.Health.Status}}' table-data-extraction
```

**Ports.** Every deployment listens on 8001 *inside* the container; only the
host-side mapping differs. Change the host port, not the container port.

**Restart policy.** Both compose files use `restart: unless-stopped`. Combined
with the health-check behaviour described above, be careful with any
orchestrator configured to restart on `unhealthy` — it will kill extractions in
progress.
