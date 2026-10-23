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

`mineru` is the heavier backend. It can recover richer layout and reading order. It runs as its own service, `mineru-api` from MinerU 4, and the parser sends it the PDF over HTTP (`POST /v1/file_parse`). On CPU, plan for 8 GB of RAM or more for long papers. Earlier measurements are in [Resource needs](#mineru-resource-needs).

MinerU 4 sets quality with a tier, chosen by `BIOPARSER_MINERU_TIER` (API and worker):

| Tier | What runs | Stack |
| ---- | --------- | ----- |
| `flash` | Native PDF text only, no models | not used |
| `basic` | Layout, OCR, formula and table small models, no VLM (3.x's pipeline backend) | CPU (default) |
| `standard` | Small models plus MinerU's VLM; native PDF text and tables are kept where they read cleanly | GPU (default) |
| `advanced` | Standard's models, with every block, tables included, going through the VLM | GPU, set `BIOPARSER_MINERU_TIER=advanced` |

A service serves its own tier and the ones below it: the CPU image serves `basic`, the GPU image `standard` and `advanced`. Each tier stores its artifacts under its own path, `pdf/<sha256>/parsed/mineru/2/<tier>`: the tier is one more path segment after the version, not part of the version. So changing the tier parses PDFs again rather than reusing results from another tier. `full` is not a tier: it is MinerU's install extra with the VLM engines, and the GPU image installs it.

On the host, run the image built for Compose with its port on loopback (the Compose `mineru` service is only on the internal network, so it cannot be published), then:

```sh
docker build -f docker/mineru.Dockerfile --target cpu -t bioparser-mineru:cpu .
docker run --rm -d --name mineru-host -p 127.0.0.1:8001:8000 bioparser-mineru:cpu
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf --backend mineru -o artifact.json
```

The scripts reach MinerU at `BIOPARSER_MINERU_BASE_URL`, default `http://127.0.0.1:8001`.

MinerU 4.0.12 reports every failed parse as `parse_failed`, an unreadable PDF as well as a crash or lack of memory, so a failed parse is retried, and a PDF that really is bad ends as a failed job (`internal_error`) once the job's claims run out. A request MinerU refuses (a 4xx, such as a tier it does not serve), a result over 32 MiB, or a middle.json from another MinerU release fails the job as `parse_failed` with no retry, and the worker logs the cause. A PDF with no mappable text fails as `invalid_pdf`. While MinerU is starting (`GET /v1/health` answers 503), a job waits for it for up to half its time limit before the attempt fails. A parse still running at the time limit is cancelled in MinerU and fails as `timeout`; one that is still queued in MinerU is cancelled and retried. mineru-api waits at most an hour for one parse, so `BIOPARSER_PARSE_TIME_LIMIT_SECONDS` is capped at 3600. Time a PDF spends queued in MinerU counts toward that limit, and `POST /extract` (up to `BIOPARSER_EXTRACT_CONCURRENCY` parses) uses the same MinerU, so keep parser workers plus that concurrency at or below MinerU's `--concurrency`. MinerU cancels a task only when asked: a worker stopped in the middle of a parse leaves its task running until it ends, and the redelivered job queues behind it. Requests to MinerU ignore `HTTP_PROXY` and `HTTPS_PROXY`, so the PDF goes straight to the service.

## MinerU image

`docker/mineru.Dockerfile` builds the service in two targets:

- `cpu`: `mineru`, ONNX small models, serves `basic`.
- `gpu`: `mineru[full]` (torch, and vLLM 0.28 for the VLM), serves `standard` and `advanced`. The PyPI wheels are CUDA 13 builds, so the host needs an NVIDIA driver for CUDA 13, as the vLLM extraction image already does.

```sh
docker build -f docker/mineru.Dockerfile --target cpu -t bioparser-mineru:cpu .
docker build -f docker/mineru.Dockerfile --target gpu -t bioparser-mineru:gpu .
```

Each target downloads and verifies the models of its tier at build time (`mineru-models-download --tier basic`, or `--tier standard --vlm-engine vllm`). The download has no revision option, so it follows the current Hugging Face weights; only the MinerU package version is pinned, and `tests/parser/test_mineru_image_pin.py` checks that pin against the mapper's `MINERU_VERSION`. The running image sets `MINERU_MODEL_SOURCE=local` and `HF_HUB_OFFLINE=1`, so it never fetches weights. It loads the models at start (`--preload-models`); `GET /v1/health` answers once they are ready. Uploads and results are kept for an hour (`MINERU_API_RETENTION_SECONDS`), and the interactive API docs are off. The tier the service serves and the number of parses that run at once are the image command, `--tier` and `--concurrency`: `basic` and 1 in the cpu target, `standard` and 2 in the gpu target. Compose does not override them.

On the GPU stack, MinerU and the vLLM extraction service share the card. MinerU's vLLM takes 6/N of the card, N being its VRAM in GB, and at least a tenth of it: about 6 GB (8 GB on an 80 GB card), plus its small models. The extraction service takes `VLLM_GPU_FRACTION` of the card (default 0.45). On a 16 GB card that is about 6 + 7 GB. `MINERU_VIRTUAL_VRAM_SIZE` replaces N, so a value X makes MinerU's share 6 × VRAM / X: above the card's VRAM it shrinks the share, below it it grows it, and below 6 it asks for more than the card. It also scales the small models' batch sizes. These figures are MinerU's sizing rules, not measurements: on a 12 GB card, measure with `nvidia-smi` before lowering `VLLM_GPU_FRACTION`, or run MinerU with `--concurrency 1`. Both engines size their memory from what is free on the card, so Compose starts MinerU once the extraction service is healthy. Compose restarts MinerU only when its process exits; one whose models did not load or whose engine died stays up answering 503 on `/v1/health` (unhealthy) until `docker compose restart mineru`. The GPU service may use 16 GiB of host RAM, the size MinerU asks for with Standard. The GPU image carries a C compiler, which Triton needs when vLLM starts. It turns off vLLM's usage reports and uses vLLM's PyTorch sampler, so FlashInfer does not JIT-compile kernels at first use. Plan on an Ampere or newer card (vLLM's FlashAttention backend).

## Worker image

The worker image runs the parser worker as its own process. It holds BioParser only; `mineru` jobs go to the MinerU service at `BIOPARSER_MINERU_BASE_URL` (Compose: `http://mineru:8000`), and `default` jobs run in the worker.

### Build

```sh
docker build -f docker/parser-worker.Dockerfile -t bioparser-parser-worker .
```

### Run

Compose starts the worker on the backend network with Redis, the MinerU service and a shared artifact volume at `/data`. The API mounts the same volume.

To run without Compose, the image needs Redis, the queue name, the MinerU URL, and a mounted artifact directory that the container user can write to. The `BIOPARSER_` variables are listed in `.env.example`.

### MinerU resource needs

Measured with MinerU 3.4.5 running inside the worker, one run per PDF in a fresh container, so parse time includes model load. MinerU 4 loads its models once, when the service starts. Re-measure the 4.x service before relying on these figures. The 105-page file is the 15-page fixture repeated seven times.

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
- **Memory:** container RAM grows with page count on both targets, up to MinerU's window of rendered pages (64 by default). A 105-page file is two windows, so its peak is a 64-page batch. `MINERU_PROCESSING_WINDOW_SIZE` lowers that peak. The CPU image used less RAM than the GPU image at every length. Treat the figures as single-run samples, not limits.
- **Compose limits:** the CPU MinerU service is capped at 12 GB of host RAM, with no extra swap. A paper that exceeds the limit is OOM-killed, and the 105-page runs stayed under 12 GiB.
- **VRAM:** MinerU used about 1 to 2 GiB and it did not grow with length.

### Health probe

The process serves `GET /health` on port 8081 (`{"status":"ok"}`), and the image probe calls it. This is liveness, not readiness: it answers while a parse runs and before the worker has connected to Redis. A probe with no answer marks the container unhealthy. Plain Compose does not act on that, but a platform such as OpenShift can restart the worker. The parse time limit ends a hung parse.

### Stopping

SIGTERM stops the consumer after the current job. A job that outlives the platform's stop timeout is killed without a graceful stop.