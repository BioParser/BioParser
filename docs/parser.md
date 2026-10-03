# PDF parser

A parse job runs either `default` or `mineru`. The host scripts `pdf-parse` and `pdf-view` call the same backends. The worker image runs them as its own process.

## Default

`default` reads digital-born text. It is fast and light, and it is what the scripts use when you do not pass `--backend`.

```sh
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf -o artifact.json
```

Omit `-o` to print the artifact JSON to stdout.

```sh
uv run pdf-view tests/parser/fixtures/plos-biology-1002000.pdf
```

Then open `http://127.0.0.1:8765/` (loopback only; change with `--port`). Run `uv run pdf-view` with no file to upload a PDF in the browser. The page can switch backend.

## MinerU

`mineru` is the heavier backend. It can recover richer layout and reading order. On CPU, plan for 8 GB of RAM or more for long papers. Measured times and memory are in [Resource needs](#resource-needs).

On the host, install the CLI as a uv tool, separate from the project environment. The first run downloads the models.

```sh
uv tool install --python 3.13 'mineru[pipeline]==3.4.5' --with six
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf --backend mineru -o artifact.json
```

If `mineru` is not found afterwards, run `uv tool update-shell` and start a new shell.

## Worker image

The worker image runs the parser worker as its own process. It includes both backends, `default` and `mineru`. It comes in two targets built from one Dockerfile: `cpu` and `gpu` (CUDA torch, `cu129`).

### Build

```sh
docker build -f docker/parser-worker.Dockerfile --target cpu -t bioparser-parser-worker:cpu .
docker build -f docker/parser-worker.Dockerfile --target gpu --build-arg TORCH_BACKEND=cu129 -t bioparser-parser-worker:gpu .
```

Pipeline weights are downloaded in their own build stage and reused by both targets. `mineru-models-download` has no revision option, so the download follows the current Hugging Face pipeline weights. Only the MinerU package version is pinned. The running image sets `HF_HUB_OFFLINE=1`, so a job never fetches weights.

### Run

Compose starts the worker on the backend network with Redis and a shared artifact volume at `/data`. The API mounts the same volume.

To run without Compose, the image needs Redis, the queue name, and a mounted artifact directory that the container user can write to. The `BIOPARSER_` variables are listed in `.env.example`.

### MinerU resource needs

MinerU backend, one run per PDF in a fresh container. The 105-page file is the 15-page fixture repeated seven times. Parse time includes model load.

- **CPU:** a 20-thread desktop, no GPU.
- **GPU:** a single 10 GiB NVIDIA card with `MINERU_DEVICE_MODE=cuda` and `MINERU_VIRTUAL_VRAM_SIZE=2`. GPU memory is the card's peak used memory minus what was in use before the container started (about 2 GiB).


| Target | Document  | Parse time | Peak container RAM | GPU memory used |
| ------ | --------- | ---------- | ------------------ | --------------- |
| cpu    | 3 pages   | 25.8 s     | 2.4 GiB            | none            |
| cpu    | 15 pages  | 85.0 s     | 3.4 GiB            | none            |
| cpu    | 105 pages | 7 min 57 s | 7.7 GiB            | none            |
| gpu    | 3 pages   | 20.4 s     | 3.3 GiB            | about 1.0 GiB   |
| gpu    | 15 pages  | 30.6 s     | 4.0 GiB            | about 2.0 GiB   |
| gpu    | 105 pages | 88.7 s     | 8.5 GiB            | about 2.0 GiB   |


- **CPU vs GPU:** on a 3-page paper they are close, because model load dominates. The CPU run takes about 3 times as long at 15 pages and about 5 times as long at 105 pages.
- **Memory:** container RAM grows with page count on both targets. The CPU image used less RAM than the GPU image at every length. Treat the figures as single-run samples, not limits.
- **Compose limits:** the worker is capped at 12 GB of host RAM, with no extra swap. A paper that exceeds the limit is OOM-killed, and the 105-page runs stayed under 12 GiB.
- **VRAM:** the worker used about 1 to 2 GiB and it did not grow with length.

### Health probe

The process serves `GET /health` on port 8081 (`{"status":"ok"}`), and the image probe calls it. This is liveness, not readiness: it answers while a parse runs and before the worker has connected to Redis. A probe with no answer marks the container unhealthy. Plain Compose does not act on that, but a platform such as OpenShift can restart the worker. The parse time limit ends a hung parse.

### Stopping

SIGTERM stops the consumer after the current job. A job that outlives the platform's stop timeout is killed without a graceful stop.