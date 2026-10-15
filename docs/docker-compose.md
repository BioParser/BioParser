# Docker Compose

Compose runs four services:

- `vllm` is a local OpenAI-compatible model server on host port `VLLM_PORT` (default 8000).  
- `mineru` is the PDF parser service on internal network port 8000; it is not published to the host unless the commented `ports` mapping in `docker-compose.yml` is enabled (`MINERU_PORT`, default 8001).  
- `redis` is queue and job state storage on port 6379 (accessed inside the Docker network using `redis:6379`).  
- `bioparser` is FastAPI on host port `BIOPARSER_PORT` (default 8080).  

`docker-compose.logging.yml` adds four services for central logs. The `COMPOSE_FILE` lines in `.env.example` include it:

- `alloy` reads the output of every container in this Compose project through `docker-api` and sends it to Loki.  
- `docker-api` is the only container with the Docker socket. It passes on the few read-only Docker API calls Alloy needs and refuses everything else. It is built from source: `docker/socket-proxy.Dockerfile`.  
- `loki` stores the logs on the `loki-data` volume for `LOKI_RETENTION_PERIOD` (default 7 days). Only Alloy and Grafana can reach it.  
- `grafana` shows them on host port `GRAFANA_PORT` (default 3000), user `admin`. It loads only the Loki plugins; to add another data source, remove its plugin from `GF_PLUGINS_DISABLE_PLUGINS` in `docker-compose.logging.yml`. It logs only its own warnings and errors (`GF_LOG_LEVEL`).

`docker-compose.traefik.yml` adds an encrypted reverse proxy with HTTP Basic Authentication in front of the API:

- `traefik` terminates TLS/HTTPS on host port `TRAEFIK_HTTPS_PORT` (default 443), enforces Basic Authentication (configured in `docker/traefik/users`), and routes internally to `http://bioparser:8080`. Plain HTTP is disabled so credentials never travel unencrypted. It runs as unprivileged user `65534:65534` (`nobody`), uses file-based configuration (`docker/traefik/bioparser.yml`), and requires no Docker socket access.

The README has the default install and start commands. This page covers env vars, networking, logs, HTTPS, and `test_prompt`.

## Environment

vLLM reads the model id from `VLLM_MODEL`. If that variable is empty, the `vllm` container exits immediately with an error like `Repo id must use alphanumeric chars ...: ''`.

```sh
cp .env.example .env
```

Required:

`COMPOSE_FILE`: Compose configuration preference, e.g. `docker-compose.yml:docker-compose.gpu.yml` (GPU) or `docker-compose.yml:docker-compose.cpu.yml` (CPU).  
`VLLM_MODEL`: Hugging Face repo id, for example `Qwen/Qwen3-0.6B` (already set in `.env.example`).  
`VLLM_PORT`: host port for vLLM (default `8000`).  
`BIOPARSER_PORT`: host port for the API (default `8080`).

Optional:

`HF_TOKEN`: needed for gated or private Hugging Face models.  
`MINERU_PORT`: host port for MinerU (default `8001`; requires enabling the commented `ports` mapping in `docker-compose.yml`).  

With `docker-compose.logging.yml`:

`GRAFANA_ADMIN_PASSWORD` (required): password of the Grafana user `admin`. Grafana reads it only when the `grafana-data` volume is new; after that, change the password in Grafana.  
`GRAFANA_PORT`: host port for Grafana (default `3000`).  
`LOKI_RETENTION_PERIOD`: how long Loki keeps logs (default `168h`, 7 days).  

With `docker-compose.traefik.yml`:

`TRAEFIK_BIND_ADDRESS`: host interface to bind Traefik to (default `127.0.0.1` for loopback). Set to `0.0.0.0` to expose to the LAN.  
`TRAEFIK_HTTPS_PORT`: host port for HTTPS (default `443`). Plain HTTP is disabled so credentials are never sent unencrypted.  
`TRAEFIK_LOG_LEVEL`: log verbosity for Traefik (default `INFO`).  

Inside Compose, the API uses `BIOPARSER_VLLM_BASE_URL=http://vllm:8000/v1` and `BIOPARSER_MINERU_BASE_URL=http://mineru:8000`. On the host, `test_prompt` should use `http://localhost:8000/v1` (the default in `.env.example`).

Weights cache on the host at `~/.cache/huggingface`.

## Start

All services are included in the Compose stack; the API container starts only after `vllm`, `mineru`, and `redis` health checks pass. The first model download can take several minutes (vLLM's health-check `start_period` is 180s for GPU and 600s for CPU).

```sh
docker compose up --build
curl localhost:8080/health
```

Stop with `Ctrl+C`, or `docker compose down`.

## Logs

Open `http://localhost:3000` and log in as `admin`. The home page is the **BioParser logs** dashboard: log lines, errors and warnings per service, the health checks that the API, MinerU and vLLM passed, API requests by status code, and the newest errors. Its source is `docker/grafana/provisioning/dashboards/bioparser-logs.json`. Grafana picks up changes to that file within 30 seconds; changes made in the Grafana UI cannot be saved.

**Drilldown → Logs** lists the services and their log volume. **Explore** takes LogQL queries, for example:

```logql
{service_name="bioparser"}                                      # API lines
{service_name=~".+"} | detected_level=~"error|critical|fatal"   # errors from every service
{service_name="bioparser"} | json | status_code >= 500          # failed requests
```

Lines are stored exactly as the services wrote them. BioParser's own code writes JSON, so `| json` turns keys such as `level`, `logger`, `status_code` and `checksum` into fields to filter on. The only labels are `service_name` (the Compose service) and `container`.

`docker compose logs` still works: Docker keeps its own copy.

The Docker socket gives full control of Docker, and so of the host. Only `docker-api` mounts it. It accepts requests only from `alloy`, only on the internal network `docker-api`, and only to list containers, inspect them and read their logs. Alloy runs as its own user with no capabilities. Loki has no login, so Loki and Alloy are only on the internal network `logs`, which the app containers are not on. To turn central logs off, remove `docker-compose.logging.yml` from `COMPOSE_FILE` and run `docker compose up -d --remove-orphans`.

## HTTPS and Traefik

`docker-compose.traefik.yml` runs Traefik as an encrypted reverse proxy with HTTP Basic Authentication in front of the API.

To enable it, append `:docker-compose.traefik.yml` to `COMPOSE_FILE` in `.env`:

```sh
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.logging.yml:docker-compose.traefik.yml
```

When started:

- Traefik binds to `TRAEFIK_BIND_ADDRESS` (default `127.0.0.1` for loopback security) on HTTPS port `TRAEFIK_HTTPS_PORT` (default 443). Plain HTTP is disabled so credentials are never transmitted unencrypted.
- All requests require HTTP Basic Authentication defined in `docker/traefik/users` (format: `username:bcrypt_hash`). The default user is `bioparser` with password `bioparser`. Additional users can be added with `htpasswd -nbB <username> <password>`.
- HTTPS connections are encrypted with TLS and routed internally across the `edge` network to `http://bioparser:8080`.
- Direct plain HTTP to the API remains accessible on loopback (`http://127.0.0.1:8080`).
- By default, Traefik generates an internal self-signed TLS certificate (`TRAEFIK DEFAULT CERT`) regenerated on restart, so encrypted connections work immediately without external domain setup. To use custom certificates, define a `tls.certificates` block in `docker/traefik/bioparser.yml` and mount your cert files.
- Note: On rootless Docker hosts, binding privileged host ports below 1024 requires setting `TRAEFIK_HTTPS_PORT=8443` in `.env` (or configuring `net.ipv4.ip_unprivileged_port_start=0`).
- On Docker Desktop for Windows, dynamic file watcher changes may require running `docker compose restart traefik` after modifying `docker/traefik/bioparser.yml` or `docker/traefik/users`.

Testing unauthenticated request (returns 401 Unauthorized):

```sh
curl -k https://localhost/health
```

Testing with Basic Authentication:

```sh
curl -k -u bioparser:bioparser https://localhost/health
# Returns {"status":"healthy"}
```

(or `curl -k -u bioparser:bioparser https://localhost:8443/health` if `TRAEFIK_HTTPS_PORT=8443` is configured in `.env`).

Traefik uses file-based configuration (`docker/traefik/bioparser.yml`), runs as unprivileged user `65534:65534` (`nobody`), and does not mount the Docker socket, keeping the container isolated with dropped capabilities (`cap_drop: [ALL]`) and `read_only: true`.

## Test prompt

`test_prompt` sends one prompt to vLLM. It does not go through the FastAPI app. Needs [uv](https://docs.astral.sh/uv/) and Python 3.13 on the host (`uv sync`).

With Compose vLLM published on localhost:8000:

```sh
uv run test_prompt "Say hello in one sentence"
uv run test_prompt --help
uv run test_prompt "Summarize this" --system-prompt "Be brief" --temperature 0.0 --max-tokens 256
```

Override the URL if needed:

```sh
BIOPARSER_VLLM_BASE_URL=http://localhost:8000/v1 uv run test_prompt "Hello"
```

If vLLM is still loading, `test_prompt` exits with a short message instead of a connection traceback. Wait until `curl http://127.0.0.1:8000/health` succeeds, then retry.
