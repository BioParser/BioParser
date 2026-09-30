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

`mineru` is the heavier CPU backend. It can recover richer layout and reading order. On a 20-thread desktop the two test fixtures (3 and 15 pages) took about 25 s each, mostly model loading, and peaked at 4.4 to 5.2 GB of RAM. Treat that as a lower bound: slower CPUs and longer papers take longer, and memory grows with page count. Plan for 8 GB or more.

On the host, install the CLI as a uv tool, separate from the project environment. The first run downloads the models.

```sh
uv tool install --python 3.13 'mineru[pipeline]==3.4.5' --with six
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf --backend mineru -o artifact.json
```

If `mineru` is not found afterwards, run `uv tool update-shell` and start a new shell.

## Worker image

The image includes both backends. Pipeline weights are downloaded in their own build stage, then reused by the CPU and GPU images. `mineru-models-download` has no revision option, so the download follows the current Hugging Face pipeline weights. The MinerU package version is pinned. The running image sets `HF_HUB_OFFLINE=1`, so a job does not fetch weights. A CPU parse needs about 5 GB for short papers and takes roughly 25 s at best (see MinerU above). Compose caps the worker at 16 GB for longer papers. The GPU image keeps the weights in VRAM.

```sh
docker build -f docker/parser-worker.Dockerfile --target cpu -t bioparser-parser-worker:cpu .
docker build -f docker/parser-worker.Dockerfile --target gpu --build-arg TORCH_BACKEND=cu129 -t bioparser-parser-worker:gpu .
```

Compose starts this service on the backend network with Redis and a shared artifact volume at `/data`. The same volume is mounted into the API. To run it without Compose, the image needs Redis, the queue name, and a mounted artifact directory writable by the container user. The `BIOPARSER_` variables are listed in `.env.example`.

The process serves `GET /health` on port 8081 (`{"status":"ok"}`). The image probe calls that. A probe that gets no answer marks the container unhealthy. Plain Compose does not act on that, but a platform such as OpenShift can restart the worker. A parse that is still running keeps the probe healthy. The parse time limit ends a hung parse.