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

## Advent symbol fonts

Advent 3B2 is the typesetter, not one typeface. It is proprietary commercial software, later sold as Arbortext Advanced Print Publisher. The typesetting engine was never published, so there is no public list of the symbol subsets it embeds. It embeds each symbol font under a generated name that starts with `Adv`, such as `AdvP`, `AdvPi`, `AdvT`, or `AdvTT`. The glyph name and the unicode map agree with each other. The drawing does not. A glyph named `comma` is drawn as `<`. A glyph named `six` is ± in one of these fonts, × in another, and ° in a third. pdfplumber and pdfium both follow the map, so switching the reader does not fix the character. Both backends rewrite the map before they read the file. The artifact checksum stays the hash of the original PDF. The rewritten file is only what the parsers read. The default parser rewrites it inside the child process the worker can stop, and that child's log is kept. MinerU rewrites it in a child process of its own, which the worker stops the same way.

The repair follows four rules.

A font is touched only when its base name starts with `Adv`. A small subset of Times, Symbol, or a maths font keeps the character the PDF already gives it. Those fonts are small because the page used only a few of their glyphs, and the glyph name is already that character. An en dash named `endash` is an en dash.

A saved row is one `Adv` font plus one glyph name. The row is applied as written. The glyph name alone is not enough, because the same name is a different symbol in a different `Adv` font. Rows are taken from the CC BY fixtures and, for a few glyphs, from the rendered pages of Costa et al., Environ Sci Pollut Res (2013) 20:5887-5912, of the NAR 2007 paper gkl933, and of client PDFs that are not committed. Those papers are not fixtures. Tests check the glyphs that appear in the CC BY fixtures. The table is not a complete catalog of 3B2. In the papers checked so far, one font plus one glyph name has drawn one symbol, and the same pair has kept that symbol in later papers. A saved row looks stable. A symbol that has not been seen still needs a new row. A small text face that has not been seen needs its name on the text list.

A small Adv font has at most 8 glyphs with ink. A standard text encoding does not change that. The cutoff is ours. It counts drawings, not the span of character codes, because a text encoding lists every code between the first and the last. A known text face keeps the character the map claims, even when that face is small. The names seen so far are `AdvP4041BD`, `AdvP497E2`, `AdvP497E3`, `AdvTimes`, `AdvPSHN-MI`, `AdvOT45bf69de.BI`, `AdvTT3978f921.BI`, `AdvOTd72d62f3.BI`, `AdvP403A40`, and `AdvP41461E`. The last two are body text. Their `currency` glyph is a circled times and has its own row. A saved glyph row still overrides that face. A known symbol face is a name on the symbol list. An unlisted glyph in that face becomes U+FFFD even when the face is large. A new glyph row does not put a face on that list. A face on neither list keeps the character the map claims, even when it is small. Those drawn glyphs are counted as `advent_characters_unverified`. An unlisted glyph in a known symbol face becomes U+FFFD, and so does a `(cid:N)` in any `Adv` font, including a known text face. A missing character is safer than a wrong one when the face is known to draw symbols. The parse still succeeds. Each replacement is logged with the page it was first seen on, the font, the glyph name, the character the map claimed, and the count. The parser artifact stores a `problems` list. Each item is a code and a count. An empty list means there is nothing to warn about. `advent_symbols_replaced` counts Advent glyphs this repair replaced with U+FFFD. `advent_characters_unverified` counts glyphs in a small Advent face that is on neither list. `advent_pages_uncounted` counts pages whose content stream could not be counted. `advent_fonts_unchecked` means the Advent repair left the original PDF, and its count stays 0. A U+FFFD that was already in the PDF is not included. The public job and ETS responses do not carry this list. The evidence view shows one sentence for each problem. A repair that cannot finish leaves the original PDF, so a file the parsers could read is not made unreadable.

A large `Adv` font that has not been seen keeps an unlisted glyph as the map claims. That includes an unseen text face. A glyph that has a row is still rewritten. `AdvP41461E` is large, and its `currency` glyph is a circled times.

Some rows drop a glyph. `AdvP4C4E46` glyph `ffi` is the bar over a square root. A few client glyphs are accents drawn before their letter, or formula pieces with no ink. Those are dropped too. Each drop is mapped to U+0000. pdfium omits that character, which is what MinerU reads. pdfplumber keeps it, so the default parser strips U+0000 from each word. The letter under an accent remains, and the bar does not sit between a radical and its quantity.

## MinerU

`mineru` is the heavier backend. It can recover richer layout and reading order. It runs as its own service, `mineru-api` from MinerU 4.2, and the parser sends it the PDF over HTTP through MinerU's Tasks API: `POST /v1/tasks` names the PDF, a `PUT` uploads it (skipped when MinerU already holds a file with the same SHA-256), `GET /v1/tasks/{task_id}` is polled until the parse ends, and the result is a ZIP whose `middle_json.json` the mapper reads. MinerU 4.1 removed `POST /v1/file_parse` from `mineru-api`. On CPU, plan for 8 GB of RAM or more for long papers. Earlier measurements are in [Resource needs](#mineru-resource-needs).

MinerU sets quality with a tier, chosen by `BIOPARSER_MINERU_TIER` on the parser worker:

| Tier | What runs | Stack |
| ---- | --------- | ----- |
| `flash` | Native PDF text only, no models | not used |
| `basic` | Layout, OCR, formula and table small models, no VLM (3.x's pipeline backend) | CPU (default) |
| `standard` | Small models plus MinerU's VLM; native PDF text and tables are kept where they read cleanly | GPU (default) |
| `advanced` | Standard's models, with every block, tables included, going through the VLM | GPU, set `BIOPARSER_MINERU_TIER=advanced` |

A service's `--tier` is a ceiling: it serves that tier and the ones below it. The CPU image serves up to `basic`, the GPU image up to `advanced` (so `standard` too). Since MinerU 4.1 a `standard` ceiling no longer serves `advanced`. Each tier stores its artifacts under its own path, `pdf/<sha256>/parsed/mineru/5/<tier>`: the tier is one more path segment after the version, not part of the version. Version 5 is MinerU 4.2.3 with glyph edges and the symbol-font repair. Version 4 stores glyph edges without that repair. Version 3 is MinerU 4.2.3 before those edges, and version 2 is MinerU 4.0.12. A measured character keeps its own box. On basic and above, MinerU can rebuild a span's text without that character list. The image patch attaches the page's characters again when the assigner can place them. Flash OCR has no character list from the detector. The recognizer's column for each character is kept and placed along the detection box. A tagged PDF is read from its structure tree, which keeps one box for the whole block. Each character box is copied onto the visual line it belongs to. A span that still has no character positions contributes no glyphs. Older results are not reused, so PDFs are parsed again once after the upgrade. So changing the tier parses PDFs again rather than reusing results from another tier. `full` is not a tier: it is MinerU's install extra with the VLM engines, and the GPU image installs it.

On the host, run the image built for Compose with its port on loopback (the Compose `mineru` service is only on the internal network, so it cannot be published), then:

```sh
docker build -f docker/mineru.Dockerfile --target cpu -t bioparser-mineru:cpu .
docker run --rm -d --name mineru-host -p 127.0.0.1:8001:8000 bioparser-mineru:cpu
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf --backend mineru -o artifact.json
```

The scripts reach MinerU at `BIOPARSER_MINERU_BASE_URL`, default `http://127.0.0.1:8001`.

MinerU 4.2.3 names a PDF it cannot read: `file_corrupted`, `file_encrypted`, `file_too_large`, `file_type_unsupported` or `resource_limit`. Such a job fails as `invalid_pdf` with no retry, as does an empty PDF or one with no mappable text. Every other failed parse, `parse_failed` above all, is retried: MinerU gives that code to a crash or a lack of memory as well as to a PDF it could not read, so a PDF that really is bad ends as a failed job (`retries_exhausted`) once the job's claims run out. A request MinerU refuses (a 4xx, such as a tier it does not serve), a result ZIP over 256 MiB or a `middle_json.json` over 64 MiB, a URL MinerU names outside its own address, or a middle.json from another MinerU release fails the job as `parse_failed` with no retry, and the worker logs the cause. A task MinerU lost (it restarted: tasks live in its memory) is retried. While MinerU is starting (`GET /v1/health` answers 503), the parse worker defers the job before it claims it, then tries again later without spending a parse claim. If MinerU still has not answered after an hour, the job fails as `deferral_expired`. The same code is used when Postgres or the extractor's dependency never answers. `failed_stage` says where the job was waiting. Inside a delivery, MinerU is not blocked waiting for models to load. A parse still running at the time limit is cancelled in MinerU and fails as `timeout`; one that is still queued in MinerU is cancelled and retried. A parse that is complete keeps its result: the download of the result ZIP is not part of the client's wait and has its own limit of 15 s, and the job is retried if the download runs out of time. The client waits at most an hour for one parse, so `BIOPARSER_PARSE_TIME_LIMIT_SECONDS` is capped at 3600. Time a PDF spends queued in MinerU counts toward that limit. Keep the number of parser workers at or below MinerU's `--concurrency`. A parse that ends early, by an error or by the queue cancelling the worker, cancels its MinerU task too; only a worker that is killed (for example by the OOM killer) leaves its task running until it ends, and the redelivered job queues behind it. Requests to MinerU ignore `HTTP_PROXY` and `HTTPS_PROXY` and follow no redirect, so the PDF goes straight to the service.
## MinerU image

`docker/mineru.Dockerfile` builds the service in two targets:

- `cpu`: `mineru`, ONNX small models, serves `basic`.
- `gpu`: `mineru[full]` (torch, and vLLM 0.30 for the VLM), serves up to `advanced`, `standard` included. `advanced` runs on the `standard` models. The PyPI wheels are CUDA 13 builds, so the host needs an NVIDIA driver for CUDA 13, as the vLLM extraction image already does.

```sh
docker build -f docker/mineru.Dockerfile --target cpu -t bioparser-mineru:cpu .
docker build -f docker/mineru.Dockerfile --target gpu -t bioparser-mineru:gpu .
```

Each target downloads and verifies the models of its tier at build time (`mineru-models-download --tier basic`, or `--tier standard --vlm-engine vllm`). The download has no revision option, so it follows the current Hugging Face weights. The MinerU package is pinned, and so is DocVortex, the library that builds MinerU's middle.json: MinerU accepts any DocVortex 0.6.x from 0.6.24, so an unpinned build could change the output under the same MinerU version. Other dependencies (vLLM, torch, transformers) are not locked. `tests/parser/test_mineru_image_pin.py` checks both pins against the mapper's `MINERU_VERSION` and `DOCVORTEX_VERSION`. The running image sets `MINERU_MODEL_SOURCE=local` and `HF_HUB_OFFLINE=1`, so it never fetches weights. It loads the models at start (`--preload-models`); `GET /v1/health` answers once they are ready. Uploads and results are kept for an hour (`MINERU_API_RETENTION_SECONDS`), and the interactive API docs are off. The tier ceiling the service serves is the image command, `--tier`: `basic` in the cpu target, `advanced` in the gpu target. The number of parses that run at once is `--concurrency`, from `MINERU_API_CONCURRENCY` (default 1, set by Compose). Compose does not override the tier. The cpu target sets `MINERU_MODEL_ONNX_DEVICE=cpu`: otherwise MinerU 4.1+ probes for a GPU at the first parse, may time a calibration run, and writes its choice to its config file.

`--task-timeout` (`MINERU_API_TASK_TIMEOUT`, 3900 s in the image) stops one parse after that much parsing time, queue time not counted. A parse that reaches it puts `mineru-api` out of service: `GET /v1/health` answers 503 (`task_timeout`) and every task fails with that code until the container restarts. A job whose task fails that way is retried, since its PDF was not parsed. The parse worker then defers its jobs, and Compose does not restart an unhealthy container, so run `docker compose restart mineru`; on OpenShift or Kubernetes, a liveness probe on `/v1/health` restarts it. The image value is above the client's longest wait (3600 s, queue time included), so the client cancels a slow parse first and only a parse orphaned by a killed worker can reach it. Keep it that way if you change either.

On the GPU stack, MinerU and the vLLM extraction service share the card. MinerU's vLLM takes 6/N of the card, N being its VRAM in GB, and at least a tenth of it: about 6 GB (8 GB on an 80 GB card), plus its small models. The extraction service takes `VLLM_GPU_FRACTION` of the card (default 0.45). On a 16 GB card that is about 6 + 7 GB. `MINERU_VIRTUAL_VRAM_SIZE` replaces N, so a value X makes MinerU's share 6 × VRAM / X: above the card's VRAM it shrinks the share, below it it grows it, and below 6 it asks for more than the card. It also scales the small models' batch sizes. These figures are MinerU's sizing rules, not measurements: on a 12 GB card, measure with `nvidia-smi` before lowering `VLLM_GPU_FRACTION`, or run MinerU with `--concurrency 1`. Both engines size their memory from what is free on the card, so Compose starts MinerU once the extraction service is healthy. Compose restarts MinerU only when its process exits; one whose models did not load or whose engine died stays up answering 503 on `/v1/health` (unhealthy) until `docker compose restart mineru`. The GPU service may use 16 GiB of host RAM, the size MinerU asks for with Standard. The GPU image carries a C compiler, which Triton needs when vLLM starts. It turns off vLLM's usage reports and uses vLLM's PyTorch sampler, so FlashInfer does not JIT-compile kernels at first use. Plan on an Ampere or newer card (vLLM's FlashAttention backend).

## Worker image

The worker image runs the parser worker as its own process. It holds BioParser only; `mineru` jobs go to the MinerU service at `BIOPARSER_MINERU_BASE_URL` (Compose: `http://mineru:8000`), and `default` jobs run pdfplumber in a child process the worker can kill when a time limit fires.

### Build

```sh
docker build -f docker/parser-worker.Dockerfile -t bioparser-parser-worker .
```

### Run

Compose starts the worker on the backend network with Redis, the MinerU service and a shared artifact volume at `/data`. The API mounts the same volume.

To run without Compose, the image needs Redis, the queue name, the MinerU URL, and a mounted artifact directory that the container user can write to. The `BIOPARSER_` variables are listed in `.env.example`.

### MinerU resource needs

Measured with MinerU 3.4.5 running inside the worker, one run per PDF in a fresh container, so parse time includes model load. MinerU 4 loads its models once, when the service starts. Re-measure the 4.2 service before relying on these figures. The 105-page file is the 15-page fixture repeated seven times.

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